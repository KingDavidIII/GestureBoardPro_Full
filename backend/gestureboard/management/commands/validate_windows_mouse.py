"""Manually invoke guarded live Windows mouse validation."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import sys
import tkinter as tk
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser

from gestureboard.mouse.windows_validation import (
    ACKNOWLEDGEMENT,
    ScreenPoint,
    ValidationRegion,
    WindowsMouseValidationRunner,
    acquire_native_outputs,
    build_report,
    validate_countdown,
    validate_scenario,
    write_report_atomic,
)


class TkValidationWindow:
    """Owned, topmost validation surface that records real Tk mouse events."""

    def __init__(self, desktop: ValidationRegion) -> None:
        desktop_width = desktop.right - desktop.left + 1
        desktop_height = desktop.bottom - desktop.top + 1
        if desktop_width < 760 or desktop_height < 540:
            raise RuntimeError("The virtual desktop is too small for safe validation.")
        self.root = tk.Tk()
        try:
            self._initialize(desktop, desktop_width, desktop_height)
        except Exception:
            try:
                self.root.destroy()
            except Exception:
                pass
            raise

    def _initialize(
        self, desktop: ValidationRegion, desktop_width: int, desktop_height: int
    ) -> None:
        self.root.title("GestureBoardPro guarded mouse validation")
        left = desktop.left + (desktop_width - 720) // 2
        top = desktop.top + (desktop_height - 480) // 2
        self.root.geometry(f"720x480{left:+d}{top:+d}")
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._mark_closed)
        self._closed = False
        self._events = []
        self.status = tk.StringVar(value="Preparing dedicated validation window…")
        tk.Label(
            self.root,
            text="LIVE NATIVE MOUSE VALIDATION",
            fg="white",
            bg="#9b1c1c",
            font=("Segoe UI", 16, "bold"),
        ).pack(fill="x")
        tk.Label(
            self.root, textvariable=self.status, wraplength=680, justify="left"
        ).pack(fill="x", padx=20, pady=12)
        tk.Label(
            self.root,
            text="Emergency stop: press Escape or close this window.",
            fg="#9b1c1c",
        ).pack()
        self.canvas = tk.Canvas(
            self.root,
            width=640,
            height=300,
            bg="white",
            highlightthickness=2,
            highlightbackground="black",
        )
        self.canvas.pack(padx=20, pady=16)
        self.canvas.create_rectangle(
            260, 30, 380, 110, fill="#ffd54f", outline="#222", width=3, tags="click"
        )
        self.canvas.create_text(
            320, 70, text="CLICK TARGET", font=("Segoe UI", 12, "bold")
        )
        self.canvas.create_rectangle(
            80, 190, 560, 250, fill="#bbdefb", outline="#1565c0", width=3
        )
        self.canvas.create_text(
            320, 220, text="BOUNDED DRAG LANE", font=("Segoe UI", 12, "bold")
        )
        self.root.bind_all(
            "<Motion>",
            lambda event: self._record("move", event, bool(event.state & 0x0100)),
        )
        self.root.bind_all(
            "<ButtonPress-1>", lambda event: self._record("down", event, True)
        )
        self.root.bind_all(
            "<ButtonRelease-1>", lambda event: self._record("up", event, False)
        )
        self.root.bind("<Escape>", lambda event: self._mark_closed())
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.update()

    def _record(self, kind, event, held) -> None:
        from gestureboard.mouse.windows_validation import RecordedMouseEvent

        self._events.append(
            RecordedMouseEvent(kind, ScreenPoint(event.x_root, event.y_root), held)
        )

    def _mark_closed(self) -> None:
        self._closed = True

    def safe_region(self) -> ValidationRegion:
        return ValidationRegion(
            self.canvas.winfo_rootx() + 10,
            self.canvas.winfo_rooty() + 10,
            self.canvas.winfo_rootx() + self.canvas.winfo_width() - 10,
            self.canvas.winfo_rooty() + self.canvas.winfo_height() - 10,
        )

    def click_target(self) -> ScreenPoint:
        return ScreenPoint(
            self.canvas.winfo_rootx() + 320, self.canvas.winfo_rooty() + 70
        )

    def drag_points(self):
        y = self.canvas.winfo_rooty() + 220
        left = self.canvas.winfo_rootx()
        return tuple(ScreenPoint(left + x, y) for x in (100, 200, 320, 440, 540))

    def drag_region(self) -> ValidationRegion:
        left = self.canvas.winfo_rootx()
        top = self.canvas.winfo_rooty()
        return ValidationRegion(left + 80, top + 190, left + 560, top + 250)

    def focused(self) -> bool:
        focused = self.root.focus_displayof()
        return focused is not None and focused.winfo_toplevel() == self.root

    def closed(self) -> bool:
        return self._closed

    def events(self):
        return tuple(self._events)

    def clear_events(self) -> None:
        self._events.clear()

    def update_status(self, text: str) -> None:
        self.status.set(text)
        self.pump()

    def pump(self) -> None:
        if self._closed:
            return
        self.root.update_idletasks()
        self.root.update()

    def close(self) -> None:
        if self._closed:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            return
        self._closed = True
        self.root.destroy()


def interactive_desktop_available() -> bool:
    try:
        return bool(ctypes.windll.user32.GetShellWindow())
    except Exception:
        return False


def escape_pressed() -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)


def cursor_position() -> ScreenPoint:
    point = ctypes.wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        raise OSError("GetCursorPos failed")
    return ScreenPoint(point.x, point.y)


def restore_cursor(point: ScreenPoint) -> bool:
    return bool(ctypes.windll.user32.SetCursorPos(point.x, point.y))


def desktop_region() -> ValidationRegion:
    user32 = ctypes.windll.user32
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78)
    height = user32.GetSystemMetrics(79)
    if width <= 0 or height <= 0:
        raise OSError("GetSystemMetrics returned invalid virtual desktop bounds")
    return ValidationRegion(left, top, left + width - 1, top + height - 1)


class Command(BaseCommand):
    help = (
        "Run guarded manual Windows cursor/click/drag validation. Dry-run is default."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--scenario",
            choices=("cursor", "click", "drag", "interruption", "all"),
            default="all",
        )
        parser.add_argument("--countdown", type=int, default=5)
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--acknowledge", default=None)
        parser.add_argument("--json-report", type=Path, default=None)

    def handle(self, *args, **options):
        scenario = options["scenario"]
        countdown = options["countdown"]
        try:
            validate_scenario(scenario)
            validate_countdown(countdown)
        except ValueError as error:
            raise CommandError(str(error)) from error
        live = options["live"]
        report_path = options["json_report"]
        if not live:
            if options["acknowledge"] is not None:
                raise CommandError("--acknowledge is accepted only with --live.")
            report = build_report(scenario, False, countdown)
            if report_path:
                write_report_atomic(report_path, report)
            self.stdout.write(
                self.style.WARNING("Dry-run only: no native input was generated.")
            )
            return None
        if os.name != "nt":
            raise CommandError("Live validation requires Windows.")
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise CommandError("Live validation requires an interactive terminal.")
        if not interactive_desktop_available():
            raise CommandError(
                "Live validation requires an interactive desktop session."
            )
        if options["acknowledge"] != ACKNOWLEDGEMENT:
            raise CommandError(
                f'Live validation requires --acknowledge "{ACKNOWLEDGEMENT}".'
            )
        outputs = acquire_native_outputs()
        try:
            window = TkValidationWindow(outputs.desktop_region)
        except Exception:
            errors: list[str] = []
            try:
                outputs.buttons.release_all()
            except Exception as error:
                errors.append(str(error))
            outputs.close_once(errors)
            raise
        report = WindowsMouseValidationRunner(
            window,
            outputs,
            emergency_stop=escape_pressed,
            cursor_position=outputs.cursor_position or cursor_position,
            restore_cursor=restore_cursor,
            desktop_region=desktop_region,
        ).run(scenario, countdown)
        if report_path:
            write_report_atomic(report_path, report)
        if not report.overall_pass:
            self.stdout.write(self.style.ERROR("Validation report: FAIL"))
            raise CommandError(
                "Live Windows mouse validation failed; review the report."
            )
        self.stdout.write(self.style.SUCCESS("Validation report: PASS"))
        return None
