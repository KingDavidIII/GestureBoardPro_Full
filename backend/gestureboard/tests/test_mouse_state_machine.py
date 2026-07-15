from __future__ import annotations

from unittest import TestCase

from gestureboard.mouse import MouseLifecycleError, MouseMode, MouseReason
from gestureboard.mouse.state_machine import MouseCommand, MouseStateMachine


class MouseStateMachineTests(TestCase):
    def test_enable_tracking_pause_resume_and_disable_lifecycle(self) -> None:
        machine = MouseStateMachine()

        enabled = machine.apply(MouseCommand.ENABLE)
        acquired = machine.apply(MouseCommand.TRACKING_ACQUIRED)
        paused = machine.apply(MouseCommand.PAUSE)
        resumed = machine.apply(MouseCommand.RESUME)
        disabled = machine.apply(MouseCommand.DISABLE)

        self.assertEqual(enabled.mode, MouseMode.READY)
        self.assertEqual(acquired.mode, MouseMode.ACTIVE)
        self.assertTrue(paused.clear_target)
        self.assertTrue(paused.safety_reset)
        self.assertEqual(resumed.mode, MouseMode.READY)
        self.assertEqual(
            (disabled.mode, disabled.reason), (MouseMode.DISABLED, MouseReason.DISABLED)
        )

    def test_idempotence_and_emergency_stop_safety_reset(self) -> None:
        machine = MouseStateMachine()

        self.assertTrue(machine.apply(MouseCommand.ENABLE).changed)
        self.assertFalse(machine.apply(MouseCommand.ENABLE).changed)
        emergency = machine.apply(MouseCommand.EMERGENCY_STOP)
        repeated = machine.apply(MouseCommand.EMERGENCY_STOP)

        self.assertEqual(emergency.mode, MouseMode.DISABLED)
        self.assertTrue(emergency.safety_reset)
        self.assertFalse(repeated.changed)
        self.assertTrue(repeated.safety_reset)

    def test_shutdown_is_harmless_to_repeat_and_rejects_other_commands(self) -> None:
        machine = MouseStateMachine()

        closed = machine.apply(MouseCommand.SHUTDOWN)
        repeated = machine.apply(MouseCommand.SHUTDOWN)

        self.assertEqual(closed.mode, MouseMode.CLOSED)
        self.assertTrue(closed.safety_reset)
        self.assertFalse(repeated.changed)
        with self.assertRaises(MouseLifecycleError):
            machine.apply(MouseCommand.ENABLE)
