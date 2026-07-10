"""Tests for gesture-to-keyboard action dispatching."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from gestureboard.services.action_dispatcher import (
    ActionDispatcher,
    ActionDispatcherError,
)
from gestureboard.services.gesture_classifier import GestureLabel
from gestureboard.services.keyboard_controller import (
    KeyboardAction,
    KeyboardControllerError,
    KeyboardExecutionResult,
)


class ActionDispatcherTests(SimpleTestCase):
    def setUp(self) -> None:
        self.action = KeyboardAction.tap("space")
        self.controller = MagicMock()
        self.execution = KeyboardExecutionResult(self.action)
        self.controller.execute.return_value = self.execution
        self.dispatcher = ActionDispatcher(
            {GestureLabel.OPEN_PALM: self.action},
            controller=self.controller,
        )

    def test_mapped_prediction_dispatches_exact_action_once(self) -> None:
        prediction = SimpleNamespace(label=GestureLabel.OPEN_PALM)

        result = self.dispatcher.dispatch(prediction)

        self.controller.execute.assert_called_once_with(self.action)
        self.assertEqual(result.gesture_label, GestureLabel.OPEN_PALM)
        self.assertIs(result.action, self.action)
        self.assertIs(result.execution_result, self.execution)
        self.assertTrue(result.executed)

    def test_unmapped_gesture_does_not_execute(self) -> None:
        result = self.dispatcher.dispatch(GestureLabel.PEACE)

        self.assertEqual(result.gesture_label, GestureLabel.PEACE)
        self.assertIsNone(result.action)
        self.assertIsNone(result.execution_result)
        self.assertFalse(result.executed)
        self.controller.execute.assert_not_called()

    def test_unknown_never_executes_even_if_mapped(self) -> None:
        dispatcher = ActionDispatcher(
            {GestureLabel.UNKNOWN: KeyboardAction.tap("a")},
            controller=self.controller,
        )

        result = dispatcher.dispatch(GestureLabel.UNKNOWN)

        self.assertFalse(result.executed)
        self.controller.execute.assert_not_called()

    def test_each_dispatch_performs_at_most_one_execution(self) -> None:
        self.dispatcher.dispatch(GestureLabel.OPEN_PALM)

        self.assertEqual(self.controller.execute.call_count, 1)

    def test_mapping_is_protected_from_external_mutation(self) -> None:
        supplied = {GestureLabel.OPEN_PALM: self.action}
        dispatcher = ActionDispatcher(supplied, controller=self.controller)
        supplied.clear()
        supplied[GestureLabel.OPEN_PALM] = KeyboardAction.tap("x")

        result = dispatcher.dispatch(GestureLabel.OPEN_PALM)

        self.assertIs(result.action, self.action)
        with self.assertRaises(TypeError):
            dispatcher.actions[GestureLabel.PEACE] = KeyboardAction.tap("p")

    def test_controller_failure_is_wrapped_with_action_label_and_cause(self) -> None:
        original = KeyboardControllerError("backend failure")
        self.controller.execute.side_effect = original

        with self.assertRaises(ActionDispatcherError) as caught:
            self.dispatcher.dispatch(GestureLabel.OPEN_PALM)

        self.assertIn("OPEN_PALM", str(caught.exception))
        self.assertIn("KeyboardAction", str(caught.exception))
        self.assertIs(caught.exception.__cause__, original)

    def test_invalid_gesture_input_is_rejected(self) -> None:
        with self.assertRaises(ActionDispatcherError):
            self.dispatcher.dispatch(SimpleNamespace(label="OPEN_PALM"))

    def test_injected_controller_is_not_closed_and_close_is_idempotent(self) -> None:
        self.dispatcher.close()
        self.dispatcher.close()

        self.controller.close.assert_not_called()

    def test_context_manager_cleanup_rejects_later_dispatch(self) -> None:
        with ActionDispatcher(controller=self.controller) as dispatcher:
            result = dispatcher.dispatch(GestureLabel.POINT)
            self.assertFalse(result.executed)

        with self.assertRaisesRegex(ActionDispatcherError, "closed"):
            dispatcher.dispatch(GestureLabel.POINT)
        self.controller.close.assert_not_called()

    def test_internally_owned_controller_is_closed_once(self) -> None:
        owned_controller = MagicMock()
        with patch(
            "gestureboard.services.action_dispatcher.KeyboardController",
            return_value=owned_controller,
        ):
            dispatcher = ActionDispatcher()
        dispatcher.close()
        dispatcher.close()

        owned_controller.close.assert_called_once_with()
