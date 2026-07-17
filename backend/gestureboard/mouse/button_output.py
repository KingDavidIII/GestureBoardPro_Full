"""Safe button output ports; tests inject all native boundaries."""

from __future__ import annotations

import ctypes
import os
from typing import Protocol

from .buttons import MouseButton
from .models import MouseLifecycleError, MouseOutputError


class MouseButtonOutputPort(Protocol):
    def button_down(self, button: MouseButton) -> None: ...
    def button_up(self, button: MouseButton) -> None: ...
    def release_all(self) -> None: ...
    def close(self) -> None: ...


class WindowsMouseButtonApi(Protocol):
    def send_input(self, button: MouseButton, is_down: bool) -> bool: ...


class _CtypesWindowsMouseButtonApi:
    """Small production boundary; tests always inject a fake instead."""

    _FLAGS = {
        (MouseButton.PRIMARY, True): 0x0002,
        (MouseButton.PRIMARY, False): 0x0004,
        (MouseButton.SECONDARY, True): 0x0008,
        (MouseButton.SECONDARY, False): 0x0010,
    }

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32

    def send_input(self, button: MouseButton, is_down: bool) -> bool:
        # mouse INPUT is 40 bytes on 64-bit Windows; only the flag is relevant.
        class MouseInput(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        class Input(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("mi", MouseInput)]

        item = Input(0, MouseInput(0, 0, 0, self._FLAGS[(button, is_down)], 0, None))
        return bool(self._user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(item)))


def create_windows_mouse_button_api() -> WindowsMouseButtonApi:
    """Construct the production button boundary only for explicit Windows mode."""

    if os.name != "nt":
        raise MouseOutputError("Windows button output is unavailable on this platform.")
    return _CtypesWindowsMouseButtonApi()


class NullMouseButtonOutput:
    def __init__(self) -> None:
        self._closed = False
        self._held: set[MouseButton] = set()

    def button_down(self, button: MouseButton) -> None:
        self._ensure_open()
        if not isinstance(button, MouseButton):
            raise MouseOutputError("button must be a MouseButton.")
        if button in self._held:
            return
        if self._held:
            raise MouseOutputError("primary and secondary buttons cannot both be held.")
        self._held.add(button)

    def button_up(self, button: MouseButton) -> None:
        self._ensure_open()
        if not isinstance(button, MouseButton):
            raise MouseOutputError("button must be a MouseButton.")
        self._held.discard(button)

    def release_all(self) -> None:
        self._ensure_open()
        self._held.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._held.clear()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise MouseLifecycleError("mouse button output is closed.")


class WindowsMouseButtonOutput:
    """Windows-only, injectable SendInput boundary for primary/secondary buttons."""

    def __init__(
        self,
        api: WindowsMouseButtonApi,
        *,
        platform_name: str | None = None,
    ) -> None:
        if (os.name if platform_name is None else platform_name) != "nt":
            raise MouseOutputError(
                "Windows button output is unavailable on this platform."
            )
        self._api = api
        self._closed = False
        self._held: set[MouseButton] = set()

    def button_down(self, button: MouseButton) -> None:
        self._ensure_open()
        self._validate(button)
        if button in self._held:
            return
        if self._held:
            raise MouseOutputError("primary and secondary buttons cannot both be held.")
        self._send_input(button, True)
        self._held.add(button)

    def button_up(self, button: MouseButton) -> None:
        self._ensure_open()
        self._validate(button)
        if button not in self._held:
            return
        self._send_input(button, False)
        self._held.remove(button)

    def release_all(self) -> None:
        self._ensure_open()
        failures = []
        for button in tuple(self._held):
            try:
                self.button_up(button)
            except MouseOutputError as error:
                failures.append(error)
        if failures:
            raise failures[0]

    def close(self) -> None:
        if self._closed:
            return
        self.release_all()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise MouseLifecycleError("mouse button output is closed.")

    @staticmethod
    def _validate(button: MouseButton) -> None:
        if not isinstance(button, MouseButton):
            raise MouseOutputError("button must be a MouseButton.")

    def _send_input(self, button: MouseButton, is_down: bool) -> None:
        try:
            accepted = self._api.send_input(button, is_down)
        except Exception as exc:
            raise MouseOutputError("SendInput operation failed.") from exc
        if not accepted:
            raise MouseOutputError("SendInput operation failed.")
