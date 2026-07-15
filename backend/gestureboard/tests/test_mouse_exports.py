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
            },
        )
        self.assertFalse(hasattr(mouse, "MouseStateMachine"))
