"""Public Alpha 1 API for transport-neutral gesture-mouse foundations."""

from .config import (
    GestureMouseConfigurationError,
    GestureMouseOutputMode,
    GestureMouseRuntimeConfig,
    load_gesture_mouse_config,
)
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
from .output import (
    NullVirtualCursorOutput,
    VirtualCursorOutputPort,
    WindowsCursorOutput,
    WindowsDesktopBounds,
    create_windows_cursor_api,
)
from .ownership import MouseOwnershipLease, WindowsCursorOwnershipLease
from .runtime import GestureMouseRuntimeCoordinator, GestureMouseRuntimeResult
from .service import GestureMouseService, MouseOutputPort, NullMouseOutputPort
from .tracking import INDEX_FINGERTIP_LANDMARK_INDEX, cursor_target_from_selected_hand

__all__ = [
    "CursorTarget",
    "GestureMouseConfigurationError",
    "GestureMouseOutputMode",
    "GestureMouseRuntimeConfig",
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
    "NullVirtualCursorOutput",
    "MouseOwnershipLease",
    "INDEX_FINGERTIP_LANDMARK_INDEX",
    "VirtualCursorMapper",
    "VirtualCursorMappingPolicy",
    "VirtualCursorMappingResult",
    "VirtualCursorReason",
    "VirtualCursorSnapshot",
    "VirtualCursorTarget",
    "VirtualSurface",
    "VirtualCursorOutputPort",
    "WindowsCursorOutput",
    "WindowsCursorOwnershipLease",
    "WindowsDesktopBounds",
    "GestureMouseRuntimeCoordinator",
    "GestureMouseRuntimeResult",
    "cursor_target_from_selected_hand",
    "create_windows_cursor_api",
    "load_gesture_mouse_config",
]
