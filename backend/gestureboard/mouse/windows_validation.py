"""Guarded, dependency-injectable live Windows mouse validation orchestration."""

from __future__ import annotations

import json
import math
import os
import platform
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .button_output import (
    MouseButtonOutputPort,
    WindowsMouseButtonOutput,
    create_windows_mouse_button_api,
)
from .buttons import MouseButton
from .mapping import VirtualCursorTarget
from .models import MouseOutputError, MouseValidationError
from .output import WindowsCursorOutput, WindowsDesktopBounds, create_windows_cursor_api
from .ownership import WindowsCursorOwnershipLease, WindowsNamedMutex

ACKNOWLEDGEMENT = "I-UNDERSTAND-THIS-CONTROLS-MY-MOUSE"
REPORT_SCHEMA_VERSION = 1
MIN_COUNTDOWN_SECONDS = 3
MAX_COUNTDOWN_SECONDS = 30
SCENARIO_TIMEOUT_SECONDS = 20.0
MAX_ACTION_DISTANCE_PX = 500
CURSOR_TOLERANCE_PX = 8
CURSOR_VERIFICATION_TIMEOUT_SECONDS = 1.0
CURSOR_POLL_INTERVAL_SECONDS = 0.01
CURSOR_VISIBLE_DWELL_SECONDS = 0.5
MIN_VISIBLE_CURSOR_DISTANCE_PX = 120
BUTTON_EVENT_VERIFICATION_TIMEOUT_SECONDS = 1.0
BUTTON_EVENT_QUIET_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class ScreenPoint:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class ValidationRegion:
    left: int
    top: int
    right: int
    bottom: int

    def clamp(self, point: ScreenPoint) -> ScreenPoint:
        return ScreenPoint(
            min(self.right, max(self.left, point.x)),
            min(self.bottom, max(self.top, point.y)),
        )

    def contains(self, point: ScreenPoint) -> bool:
        return self.left <= point.x <= self.right and self.top <= point.y <= self.bottom


@dataclass(frozen=True, slots=True)
class RecordedMouseEvent:
    kind: str
    point: ScreenPoint
    primary_held: bool


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    name: str
    status: str
    expected_events: tuple[str, ...] = ()
    observed_events: tuple[str, ...] = ()
    expected_point: ScreenPoint | None = None
    observed_point: ScreenPoint | None = None
    tolerance_px: int | None = None
    expected_start: ScreenPoint | None = None
    expected_end: ScreenPoint | None = None
    observed_start: ScreenPoint | None = None
    observed_end: ScreenPoint | None = None
    observed_displacement_px: float | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    schema_version: int
    utc_timestamp: str
    platform: str
    python_version: str
    command_scenario: str
    live: bool
    countdown_seconds: int
    scenarios: tuple[ScenarioResult, ...]
    cancellation_reason: str | None
    operational_error: str | None
    cleanup_errors: tuple[str, ...]
    final_release_status: str
    cursor_restoration_status: str
    overall_pass: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ValidationWindow(Protocol):
    def safe_region(self) -> ValidationRegion: ...
    def click_target(self) -> ScreenPoint: ...
    def drag_points(self) -> Sequence[ScreenPoint]: ...
    def drag_region(self) -> ValidationRegion: ...
    def focused(self) -> bool: ...
    def closed(self) -> bool: ...
    def events(self) -> Sequence[RecordedMouseEvent]: ...
    def clear_events(self) -> None: ...
    def update_status(self, text: str) -> None: ...
    def pump(self) -> None: ...
    def close(self) -> None: ...


class CursorOutput(Protocol):
    def move(self, target: VirtualCursorTarget) -> None: ...
    def close(self) -> None: ...


@dataclass(slots=True)
class NativeOutputs:
    cursor: CursorOutput
    buttons: MouseButtonOutputPort
    lease: WindowsCursorOwnershipLease | None = None
    owner_id: str = "gestureboard-windows-validation"
    owned: bool = True
    desktop_origin: ScreenPoint = ScreenPoint(0, 0)
    desktop_region: ValidationRegion = ValidationRegion(0, 0, 1919, 1079)
    cursor_position: Callable[[], ScreenPoint] | None = None
    _closed: bool = field(default=False, init=False)

    def close_once(self, errors: list[str]) -> None:
        if self._closed or not self.owned:
            return
        self._closed = True
        for name, action in (
            ("button close", self.buttons.close),
            ("cursor close", self.cursor.close),
            (
                "ownership release",
                lambda: self.lease.release(self.owner_id) if self.lease else None,
            ),
        ):
            try:
                action()
            except Exception as error:
                errors.append(f"{name}: {error}")


class ValidationCancelled(RuntimeError):
    pass


class ValidationInterruptedForRelease(ValidationCancelled):
    pass


class WindowsMouseValidationRunner:
    def __init__(
        self,
        window: ValidationWindow,
        outputs: NativeOutputs,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        emergency_stop: Callable[[], bool] = lambda: False,
        cursor_position: Callable[[], ScreenPoint] = lambda: ScreenPoint(0, 0),
        restore_cursor: Callable[[ScreenPoint], bool] = lambda point: True,
        desktop_region: Callable[[], ValidationRegion] | None = None,
        timeout_seconds: float = SCENARIO_TIMEOUT_SECONDS,
    ) -> None:
        self.window = window
        self.outputs = outputs
        self.clock = clock
        self.sleep = sleep
        self.emergency_stop = emergency_stop
        self.cursor_position = cursor_position
        self.restore_cursor = restore_cursor
        self.desktop_region = desktop_region or (lambda: self.outputs.desktop_region)
        self.timeout_seconds = timeout_seconds
        self._scenario_deadline = 0.0

    def run(self, scenario: str, countdown_seconds: int) -> ValidationReport:
        original: ScreenPoint | None = None
        results: list[ScenarioResult] = []
        cleanup_errors: list[str] = []
        cancellation: str | None = None
        operational_error: str | None = None
        release_status = "not attempted"
        restoration_status = "not attempted"
        interruption_pending = False
        try:
            validate_scenario(scenario)
            validate_countdown(countdown_seconds)
            original = self.cursor_position()
            self._countdown(countdown_seconds)
            names = (
                ("cursor", "click", "drag", "interruption")
                if scenario == "all"
                else (scenario,)
            )
            for name in names:
                self.window.update_status(f"Active scenario: {name}")
                self._scenario_deadline = self.clock() + self.timeout_seconds
                try:
                    result = getattr(self, f"_run_{name}")()
                except ValidationInterruptedForRelease:
                    interruption_pending = True
                    break
                results.append(result)
                if result.status != "passed":
                    self.window.update_status(f"Failed scenario: {name}")
                    break
        except ValidationCancelled as error:
            cancellation = str(error)
        except Exception as error:
            operational_error = f"{type(error).__name__}: {error}"
        finally:
            try:
                self.outputs.buttons.release_all()
                release_status = "released"
            except Exception as error:
                release_status = "release failed"
                cleanup_errors.append(f"release_all: {error}")
            if interruption_pending:
                try:
                    self.window.pump()
                except Exception as error:
                    cleanup_errors.append(f"interruption event pump: {error}")
            try:
                if original is None:
                    restoration_status = "capture failed"
                elif not self.desktop_region().contains(original):
                    restoration_status = "restore failed"
                    cleanup_errors.append(
                        "cursor restore: original position is outside the current desktop"
                    )
                else:
                    restoration_status = (
                        "restored"
                        if self.restore_cursor(original)
                        else "restore failed"
                    )
            except Exception as error:
                restoration_status = "restore failed"
                cleanup_errors.append(f"cursor restore: {error}")
            if interruption_pending:
                try:
                    observed = tuple(
                        event.kind
                        for event in self.window.events()
                        if event.kind in ("down", "up")
                    )
                    interruption_result = ScenarioResult(
                        "interruption",
                        "passed" if observed == ("down", "up") else "failed",
                        ("down", "up from final cleanup"),
                        observed,
                    )
                except Exception as error:
                    cleanup_errors.append(f"interruption event inspection: {error}")
                    interruption_result = ScenarioResult(
                        "interruption",
                        "failed",
                        ("down", "up from final cleanup"),
                        detail=f"event inspection failed: {error}",
                    )
                results.append(interruption_result)
            self.outputs.close_once(cleanup_errors)
            try:
                if cancellation:
                    self.window.update_status(f"Cancelled: {cancellation}")
                elif operational_error:
                    self.window.update_status(f"Failed: {operational_error}")
                provisional_pass = (
                    bool(results)
                    and all(item.status == "passed" for item in results)
                    and cancellation is None
                    and operational_error is None
                    and not cleanup_errors
                    and release_status == "released"
                    and restoration_status == "restored"
                )
                self.window.update_status(
                    "Final result: PASS" if provisional_pass else "Final result: FAIL"
                )
                self.window.pump()
                self.sleep(0.25)
            except Exception as error:
                cleanup_errors.append(f"result presentation: {error}")
            try:
                self.window.close()
            except Exception as error:
                cleanup_errors.append(f"window close: {error}")
        passed = bool(results) and all(item.status == "passed" for item in results)
        passed = (
            passed
            and cancellation is None
            and operational_error is None
            and not cleanup_errors
            and release_status == "released"
            and restoration_status == "restored"
        )
        return build_report(
            scenario,
            True,
            countdown_seconds,
            tuple(results),
            cancellation,
            operational_error,
            tuple(cleanup_errors),
            release_status,
            restoration_status,
            passed,
        )

    def _countdown(self, seconds: int) -> None:
        for remaining in range(seconds, 0, -1):
            self.window.update_status(
                f"Native mouse validation starts in {remaining}. Press Escape to cancel."
            )
            self._guard(self.clock() + 1.0)

    def _guard(self, deadline: float) -> None:
        while self.clock() < deadline:
            self.window.pump()
            if self.window.closed():
                raise ValidationCancelled("validation window closed")
            if not self.window.focused():
                raise ValidationCancelled("validation window lost focus")
            if self.emergency_stop():
                raise ValidationCancelled("Escape emergency stop")
            self.sleep(min(0.01, max(0.0, deadline - self.clock())))

    def _move(self, point: ScreenPoint) -> ScreenPoint:
        self._check_active()
        region = self.window.safe_region()
        desktop = self.desktop_region()
        captured = self.outputs.desktop_region
        if desktop != captured:
            raise MouseValidationError(
                "virtual desktop layout changed after native output acquisition"
            )
        if not region_within(region, desktop) or not region_within(region, captured):
            raise MouseValidationError(
                "validation region is outside the current virtual desktop"
            )
        if (
            not region.contains(point)
            or not desktop.contains(point)
            or not captured.contains(point)
        ):
            raise MouseValidationError(
                "generated coordinate is outside the validation window or desktop"
            )
        relative_x = point.x - self.outputs.desktop_origin.x
        relative_y = point.y - self.outputs.desktop_origin.y
        self.outputs.cursor.move(
            VirtualCursorTarget(0.0, 0.0, relative_x, relative_y, 0, 0)
        )
        return self._position_verified(point)

    def _position_verified(self, expected: ScreenPoint) -> ScreenPoint:
        deadline = min(
            self.clock() + CURSOR_VERIFICATION_TIMEOUT_SECONDS,
            self._scenario_deadline,
        )
        while self.clock() < deadline:
            self._check_active()
            desktop = self.desktop_region()
            captured = self.outputs.desktop_region
            observed = self.cursor_position()
            if (
                self.window.safe_region().contains(observed)
                and desktop.contains(observed)
                and captured.contains(observed)
                and desktop == captured
                and distance(expected, observed) <= CURSOR_TOLERANCE_PX
            ):
                return observed
            self.sleep(
                min(
                    CURSOR_POLL_INTERVAL_SECONDS,
                    max(0.0, deadline - self.clock()),
                )
            )
        raise MouseValidationError(
            "native cursor did not reach the requested position within the verification timeout"
        )

    def _button_events_verified(
        self,
        expected_kinds: tuple[str, ...],
        expected_points: tuple[ScreenPoint, ...],
        expected_held: tuple[bool, ...],
        *,
        marker: int = 0,
    ) -> tuple[RecordedMouseEvent, ...]:
        deadline = min(
            self.clock() + BUTTON_EVENT_VERIFICATION_TIMEOUT_SECONDS,
            self._scenario_deadline,
        )
        while self.clock() < deadline:
            self._check_active()
            self._ensure_current_layout()
            events = tuple(
                event for event in self.window.events() if event.kind in ("down", "up")
            )
            events = events[marker:]
            actual_kinds = tuple(event.kind for event in events)
            if actual_kinds != expected_kinds[: len(events)]:
                raise MouseValidationError(
                    "unexpected, duplicate, or missing button event"
                )
            if len(events) == len(expected_kinds):
                if all(
                    distance(expected, event.point) <= CURSOR_TOLERANCE_PX
                    and event.primary_held is held
                    for event, expected, held in zip(
                        events, expected_points, expected_held, strict=True
                    )
                ):
                    return events
                raise MouseValidationError(
                    "button event was misplaced or had invalid held state"
                )
            self.sleep(
                min(
                    CURSOR_POLL_INTERVAL_SECONDS,
                    max(0.0, deadline - self.clock()),
                )
            )
        raise MouseValidationError("required Tk button event was not observed in time")

    def _drag_hold_verified(self, expected: ScreenPoint) -> None:
        self._button_events_verified(("down",), (expected,), (True,))

    def _filtered_button_events(self) -> tuple[RecordedMouseEvent, ...]:
        return tuple(
            event for event in self.window.events() if event.kind in ("down", "up")
        )

    def _button_events_quiet(self, marker: int) -> None:
        deadline = min(
            self.clock() + BUTTON_EVENT_QUIET_SECONDS, self._scenario_deadline
        )
        while self.clock() < deadline:
            self._check_active()
            self._ensure_current_layout()
            if len(self._filtered_button_events()) != marker:
                raise MouseValidationError(
                    "duplicate button event arrived after release"
                )
            self.sleep(
                min(CURSOR_POLL_INTERVAL_SECONDS, max(0.0, deadline - self.clock()))
            )

    def _ensure_current_layout(self) -> None:
        desktop = self.desktop_region()
        captured = self.outputs.desktop_region
        region = self.window.safe_region()
        if desktop != captured or not region_within(region, desktop):
            raise MouseValidationError(
                "virtual desktop layout changed after native output acquisition"
            )

    def _validate_drag_plan(
        self, points: Sequence[ScreenPoint], drag_region: ValidationRegion
    ) -> None:
        self._ensure_current_layout()
        desktop = self.desktop_region()
        captured = self.outputs.desktop_region
        safe = self.window.safe_region()
        if not region_within(drag_region, safe) or not region_within(
            drag_region, desktop
        ):
            raise MouseValidationError(
                "drag lane is outside the safe validation region"
            )
        if not all(
            drag_region.contains(point)
            and safe.contains(point)
            and desktop.contains(point)
            and captured.contains(point)
            for point in points
        ):
            raise MouseValidationError(
                "planned drag waypoint is outside the safe drag lane"
            )

    def _move_drag_waypoint(
        self, point: ScreenPoint, validated_drag_region: ValidationRegion
    ) -> ScreenPoint:
        self._check_active()
        current_drag_region = self.window.drag_region()
        desktop = self.desktop_region()
        captured = self.outputs.desktop_region
        safe = self.window.safe_region()
        if current_drag_region != validated_drag_region:
            raise MouseValidationError("drag lane changed after preflight validation")
        if not (
            current_drag_region.contains(point)
            and safe.contains(point)
            and desktop.contains(point)
            and captured.contains(point)
            and desktop == captured
        ):
            raise MouseValidationError(
                "drag waypoint is outside the current safe drag lane"
            )
        relative_x = point.x - self.outputs.desktop_origin.x
        relative_y = point.y - self.outputs.desktop_origin.y
        self.outputs.cursor.move(
            VirtualCursorTarget(0.0, 0.0, relative_x, relative_y, 0, 0)
        )
        return self._position_verified(point)

    def _check_active(self) -> None:
        self.window.pump()
        if self.window.closed():
            raise ValidationCancelled("validation window closed")
        if not self.window.focused():
            raise ValidationCancelled("validation window lost focus")
        if self.emergency_stop():
            raise ValidationCancelled("Escape emergency stop")
        if self._scenario_deadline and self.clock() > self._scenario_deadline:
            raise ValidationCancelled("scenario timeout")

    def _run_cursor(self) -> ScenarioResult:
        self.window.clear_events()
        expected_start, expected_end = self._cursor_demo_points()
        try:
            observed_start = self._move(expected_start)
            observed_end = self._move(expected_end)
        except MouseValidationError as error:
            if not str(error).startswith("native cursor did not reach"):
                raise
            return ScenarioResult(
                "cursor",
                "failed",
                ("native move", "native move"),
                detail=str(error),
                tolerance_px=CURSOR_TOLERANCE_PX,
                expected_start=expected_start,
                expected_end=expected_end,
            )
        self._guard(self.clock() + CURSOR_VISIBLE_DWELL_SECONDS)
        events = self.window.events()
        motion_events = tuple(event.kind for event in events if event.kind == "move")
        observed_displacement = distance(observed_start, observed_end)
        passed = (
            distance(expected_start, observed_start) <= CURSOR_TOLERANCE_PX
            and distance(expected_end, observed_end) <= CURSOR_TOLERANCE_PX
            and observed_displacement >= MIN_VISIBLE_CURSOR_DISTANCE_PX
        )
        return ScenarioResult(
            "cursor",
            "passed" if passed else "failed",
            ("native move", "native move"),
            motion_events,
            expected_end,
            observed_end,
            CURSOR_TOLERANCE_PX,
            expected_start,
            expected_end,
            observed_start,
            observed_end,
            observed_displacement,
            "Tk motion evidence received" if motion_events else "No Tk motion evidence",
        )

    def _run_click(self) -> ScenarioResult:
        self.window.clear_events()
        target = self.window.click_target()
        observed_target = self._move(target)
        self._check_active()
        self.outputs.buttons.button_down(MouseButton.PRIMARY)
        self._check_active()
        self.window.pump()
        self.outputs.buttons.button_up(MouseButton.PRIMARY)
        self.window.pump()
        observed = tuple(
            event.kind for event in self.window.events() if event.kind in ("down", "up")
        )
        button_events = tuple(
            event for event in self.window.events() if event.kind in ("down", "up")
        )
        expected = ("down", "up")
        passed = (
            observed == expected
            and all(distance(target, event.point) <= 8 for event in button_events)
            and button_events[-1].primary_held is False
        )
        return ScenarioResult(
            "click",
            "passed" if passed else "failed",
            expected,
            observed,
            target,
            observed_target,
            8,
        )

    def _run_drag(self) -> ScenarioResult:
        self.window.clear_events()
        points = tuple(self.window.drag_points())
        validate_drag_points(points)
        drag_region = self.window.drag_region()
        self._validate_drag_plan(points, drag_region)
        observed_start = self._move_drag_waypoint(points[0], drag_region)
        self._check_active()
        self.outputs.buttons.button_down(MouseButton.PRIMARY)
        down_events = self._button_events_verified(("down",), (points[0],), (True,))
        native_waypoints = [observed_start]
        observed_end = observed_start
        for point in points[1:]:
            self._validate_drag_plan((point,), drag_region)
            self._drag_hold_verified(points[0])
            observed_end = self._move_drag_waypoint(point, drag_region)
            native_waypoints.append(observed_end)
            self._drag_hold_verified(points[0])
        self._drag_hold_verified(points[0])
        event_marker = len(self._filtered_button_events())
        self.outputs.buttons.button_up(MouseButton.PRIMARY)
        button_events = self._button_events_verified(
            ("up",), (points[-1],), (False,), marker=event_marker
        )
        self._button_events_quiet(event_marker + len(button_events))
        events = tuple(self.window.events())
        observed = tuple(
            event.kind for event in events if event.kind in ("down", "move", "up")
        )
        desktop = self.desktop_region()
        observed_displacement = distance(observed_start, observed_end)
        all_bounded = all(
            drag_region.contains(point)
            and self.window.safe_region().contains(point)
            and desktop.contains(point)
            and self.outputs.desktop_region.contains(point)
            for point in native_waypoints
        )
        held_motion_events = tuple(
            event for event in events if event.kind == "move" and event.primary_held
        )
        passed = (
            tuple(event.kind for event in down_events) == ("down",)
            and tuple(event.kind for event in button_events) == ("up",)
            and len(native_waypoints) == len(points)
            and observed_displacement >= 40
            and all_bounded
            and distance(points[-1], observed_end) <= 8
        )
        return ScenarioResult(
            "drag",
            "passed" if passed else "failed",
            ("down", "move while held", "up"),
            observed,
            expected_start=points[0],
            expected_end=points[-1],
            observed_start=observed_start,
            observed_end=observed_end,
            observed_displacement_px=observed_displacement,
            tolerance_px=CURSOR_TOLERANCE_PX,
            detail=(
                "Tk held-motion evidence received"
                if held_motion_events
                else "No Tk held-motion evidence"
            ),
        )

    def _run_interruption(self) -> ScenarioResult:
        self.window.clear_events()
        point = tuple(self.window.drag_points())[0]
        observed = self._move(point)
        self._check_active()
        if not self.window.drag_region().contains(observed):
            raise MouseValidationError("interruption start is outside the drag lane")
        self.outputs.buttons.button_down(MouseButton.PRIMARY)
        self._check_active()
        self.window.pump()
        raise ValidationInterruptedForRelease("injected interruption")

    def _cursor_demo_points(self) -> tuple[ScreenPoint, ScreenPoint]:
        region = self.window.safe_region()
        midpoint_y = (region.top + region.bottom) // 2
        start = ScreenPoint(region.left + 80, midpoint_y)
        end = ScreenPoint(region.right - 80, midpoint_y)
        if (
            not region.contains(start)
            or not region.contains(end)
            or distance(start, end) < MIN_VISIBLE_CURSOR_DISTANCE_PX
        ):
            raise MouseValidationError(
                "validation region cannot provide two visibly distinct cursor points"
            )
        return start, end


def validate_countdown(value: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_COUNTDOWN_SECONDS <= value <= MAX_COUNTDOWN_SECONDS
    ):
        raise MouseValidationError(
            f"countdown must be an integer from {MIN_COUNTDOWN_SECONDS} to {MAX_COUNTDOWN_SECONDS}"
        )


def validate_scenario(value: str) -> None:
    if value not in {"cursor", "click", "drag", "interruption", "all"}:
        raise MouseValidationError("unsupported validation scenario")


def validate_drag_points(points: Sequence[ScreenPoint]) -> None:
    if len(points) < 2 or distance(points[0], points[-1]) > MAX_ACTION_DISTANCE_PX:
        raise MouseValidationError(
            "drag path exceeds the conservative validation limit"
        )


def distance(first: ScreenPoint, second: ScreenPoint) -> float:
    return math.hypot(second.x - first.x, second.y - first.y)


def region_within(inner: ValidationRegion, outer: ValidationRegion) -> bool:
    return (
        outer.left <= inner.left <= inner.right <= outer.right
        and outer.top <= inner.top <= inner.bottom <= outer.bottom
    )


def build_report(
    scenario: str,
    live: bool,
    countdown: int,
    results: tuple[ScenarioResult, ...] = (),
    cancellation: str | None = None,
    operational_error: str | None = None,
    cleanup_errors: tuple[str, ...] = (),
    release_status: str = "not applicable",
    restoration_status: str = "not applicable",
    passed: bool = False,
) -> ValidationReport:
    return ValidationReport(
        REPORT_SCHEMA_VERSION,
        datetime.now(UTC).isoformat(),
        platform.system(),
        platform.python_version(),
        scenario,
        live,
        countdown,
        results,
        cancellation,
        operational_error,
        cleanup_errors,
        release_status,
        restoration_status,
        passed,
    )


def write_report_atomic(path: Path, report: ValidationReport) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def acquire_native_outputs(
    *, mutex_factory: Callable[[], WindowsNamedMutex] = WindowsNamedMutex
) -> NativeOutputs:
    lease = WindowsCursorOwnershipLease()
    lease.enable_cross_process(mutex_factory)
    owner = "gestureboard-windows-validation"
    if not lease.acquire(owner):
        raise MouseOutputError("native mouse ownership is unavailable")
    cursor = None
    try:
        cursor_api = create_windows_cursor_api()
        bounds = WindowsDesktopBounds.from_windows_api(cursor_api)
        cursor = WindowsCursorOutput(bounds, cursor_api)
        buttons = WindowsMouseButtonOutput(create_windows_mouse_button_api())
    except Exception:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        try:
            lease.release(owner)
        except Exception:
            pass
        raise
    return NativeOutputs(
        cursor=cursor,
        buttons=buttons,
        lease=lease,
        owner_id=owner,
        desktop_origin=ScreenPoint(bounds.origin_x, bounds.origin_y),
        desktop_region=ValidationRegion(
            bounds.origin_x,
            bounds.origin_y,
            bounds.origin_x + bounds.width_px - 1,
            bounds.origin_y + bounds.height_px - 1,
        ),
        cursor_position=lambda: ScreenPoint(*cursor_api.get_cursor_pos()),
    )
