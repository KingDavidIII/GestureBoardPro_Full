"""Public Alpha 1 API for transport-neutral gesture-mouse foundations."""

from .models import (
    CursorTarget,
    MouseEvent,
    MouseEventKind,
    MouseLifecycleError,
    MouseMode,
    MouseOutputError,
    MouseReason,
    MouseSnapshot,
    MouseValidationError,
)
from .service import GestureMouseService, MouseOutputPort, NullMouseOutputPort

__all__ = [
    "CursorTarget",
    "GestureMouseService",
    "MouseEvent",
    "MouseEventKind",
    "MouseLifecycleError",
    "MouseMode",
    "MouseOutputError",
    "MouseOutputPort",
    "MouseReason",
    "MouseSnapshot",
    "MouseValidationError",
    "NullMouseOutputPort",
]
