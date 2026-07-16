from __future__ import annotations

from unittest import TestCase

import gestureboard.mouse as mouse


class MouseExportsTests(TestCase):
    def test_public_api_is_explicit_and_does_not_export_state_machine_helpers(
        self,
    ) -> None:
        self.assertEqual(
            set(mouse.__all__),
            {
                "CursorTarget",
                "MouseButton",
                "MouseButtonActionKind",
                "MouseButtonController",
                "MouseButtonDecision",
                "MouseButtonIntent",
                "MouseButtonOutputPort",
                "MouseButtonPolicy",
                "MouseButtonState",
                "ActiveCameraRegion",
                "GestureMouseConfigurationError",
                "GestureMouseOutputMode",
                "GestureMouseRuntimeConfig",
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
                "load_gesture_mouse_config",
            },
        )
        self.assertFalse(hasattr(mouse, "MouseStateMachine"))
