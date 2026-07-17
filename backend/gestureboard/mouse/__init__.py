"""Public transport-neutral gesture-mouse API."""

from .button_output import (
    MouseButtonOutputPort,
    NullMouseButtonOutput,
    WindowsMouseButtonOutput,
    create_windows_mouse_button_api,
)
from .buttons import (
    MouseButton,
    MouseButtonActionKind,
    MouseButtonController,
    MouseButtonDecision,
    MouseButtonIntent,
    MouseButtonPolicy,
    MouseButtonState,
    detect_button_intent,
)
from .composition import MouseRuntimeDependencies, build_mouse_runtime_dependencies
from .config import (
    GestureMouseConfigurationError,
    GestureMouseOutputMode,
    GestureMouseRuntimeConfig,
    MouseButtonOutputMode,
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
    "MouseButton",
    "MouseButtonActionKind",
    "MouseButtonController",
    "MouseButtonDecision",
    "MouseButtonIntent",
    "MouseButtonOutputPort",
    "MouseButtonOutputMode",
    "MouseRuntimeDependencies",
    "MouseButtonPolicy",
    "MouseButtonState",
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
    "NullMouseButtonOutput",
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
    "WindowsMouseButtonOutput",
    "WindowsCursorOwnershipLease",
    "WindowsDesktopBounds",
    "GestureMouseRuntimeCoordinator",
    "GestureMouseRuntimeResult",
    "cursor_target_from_selected_hand",
    "detect_button_intent",
    "create_windows_cursor_api",
    "create_windows_mouse_button_api",
    "build_mouse_runtime_dependencies",
    "load_gesture_mouse_config",
]
