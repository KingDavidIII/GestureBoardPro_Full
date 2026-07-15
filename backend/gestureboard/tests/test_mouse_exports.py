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
            },
        )
        self.assertFalse(hasattr(mouse, "MouseStateMachine"))
