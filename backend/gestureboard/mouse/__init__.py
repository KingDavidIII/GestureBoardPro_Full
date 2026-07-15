"""Public Alpha 1 API for transport-neutral gesture-mouse foundations."""

from .mapping import (
    ActiveCameraRegion,
    VirtualCursorMapper,
    VirtualCursorMappingPolicy,
    VirtualCursorMappingResult,
    VirtualCursorReason,
    VirtualCursorSnapshot,
    VirtualCursorTarget,
    VirtualSurface,
)
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
from .tracking import INDEX_FINGERTIP_LANDMARK_INDEX, cursor_target_from_selected_hand

__all__ = [
    "CursorTarget",
    "ActiveCameraRegion",
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
    "INDEX_FINGERTIP_LANDMARK_INDEX",
    "VirtualCursorMapper",
    "VirtualCursorMappingPolicy",
    "VirtualCursorMappingResult",
    "VirtualCursorReason",
    "VirtualCursorSnapshot",
    "VirtualCursorTarget",
    "VirtualSurface",
    "cursor_target_from_selected_hand",
]
