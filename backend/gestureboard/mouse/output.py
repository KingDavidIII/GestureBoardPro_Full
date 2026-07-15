"""Cursor movement ports with no button, scroll, or keyboard API."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Protocol

from .mapping import VirtualCursorTarget
from .models import MouseLifecycleError, MouseOutputError, MouseValidationError


class VirtualCursorOutputPort(Protocol):
    def move(self, target: VirtualCursorTarget) -> None: ...
    def close(self) -> None: ...


class WindowsCursorApi(Protocol):
    def get_system_metrics(self, metric_id: int) -> int: ...
    def set_cursor_pos(self, x: int, y: int) -> bool: ...


_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


class _CtypesWindowsCursorApi:
    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32

    def get_system_metrics(self, metric_id: int) -> int:
        return int(self._user32.GetSystemMetrics(metric_id))

    def set_cursor_pos(self, x: int, y: int) -> bool:
        return bool(self._user32.SetCursorPos(x, y))


def create_windows_cursor_api() -> WindowsCursorApi:
    """Construct the production API boundary only for explicit Windows mode."""

    return _CtypesWindowsCursorApi()


class NullVirtualCursorOutput:
    def __init__(self) -> None:
        self._closed = False

    def move(self, target: VirtualCursorTarget) -> None:
        if self._closed:
            raise MouseLifecycleError("virtual cursor output is closed.")
        del target

    def close(self) -> None:
        self._closed = True


@dataclass(frozen=True, slots=True)
class WindowsDesktopBounds:
    origin_x: int
    origin_y: int
    width_px: int
    height_px: int

    def __post_init__(self) -> None:
        for name in ("origin_x", "origin_y", "width_px", "height_px"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise MouseValidationError(f"{name} must be an integer.")
        if self.width_px < 1 or self.height_px < 1:
            raise MouseValidationError("desktop dimensions must be positive.")

    @classmethod
    def from_windows_api(cls, api: WindowsCursorApi) -> WindowsDesktopBounds:
        return cls(
            api.get_system_metrics(_SM_XVIRTUALSCREEN),
            api.get_system_metrics(_SM_YVIRTUALSCREEN),
            api.get_system_metrics(_SM_CXVIRTUALSCREEN),
            api.get_system_metrics(_SM_CYVIRTUALSCREEN),
        )


class WindowsCursorOutput:
    """Windows-only adapter using an injected SetCursorPos boundary."""

    def __init__(
        self,
        bounds: WindowsDesktopBounds,
        api: WindowsCursorApi | None = None,
        *,
        platform_name: str | None = None,
    ) -> None:
        if (os.name if platform_name is None else platform_name) != "nt":
            raise MouseOutputError(
                "Windows cursor output is unavailable on this platform."
            )
        self._bounds = bounds
        self._api = api or _CtypesWindowsCursorApi()
        self._closed = False

    def move(self, target: VirtualCursorTarget) -> None:
        if self._closed:
            raise MouseLifecycleError("Windows cursor output is closed.")
        if not isinstance(target, VirtualCursorTarget):
            raise MouseValidationError("target must be a VirtualCursorTarget.")
        x = min(self._bounds.width_px - 1, max(0, target.x_px)) + self._bounds.origin_x
        y = min(self._bounds.height_px - 1, max(0, target.y_px)) + self._bounds.origin_y
        if not self._api.set_cursor_pos(x, y):
            raise MouseOutputError("SetCursorPos failed.")

    def close(self) -> None:
        self._closed = True
