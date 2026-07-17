from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from gestureboard.mouse import (
    GestureMouseRuntimeCoordinator,
    MouseLifecycleError,
    MouseOutputError,
    MouseValidationError,
    WindowsCursorOwnershipLease,
)
from gestureboard.mouse.buttons import (
    MouseButton,
    MouseButtonActionKind,
    MouseButtonIntent,
    MouseButtonPolicy,
)
from gestureboard.mouse.mapping import VirtualCursorMapper, VirtualSurface
from gestureboard.recognition.models import GestureId
from gestureboard.recognition.observations import (
    Handedness,
    HandObservation,
    HandSelection,
    Landmark3D,
)


class RecordingOutput:
    def __init__(self, fail: bool = False) -> None:
        self.targets = []
        self.closed = 0
        self.fail = fail

    def move(self, target: object) -> None:
        if self.fail:
            raise MouseOutputError("failed")
        self.targets.append(target)

    def close(self) -> None:
        self.closed += 1


class RecordingButtonOutput:
    def __init__(
        self, *, fail_action: bool = False, fail_release: bool = False
    ) -> None:
        self.calls = []
        self.release_calls = 0
        self.closed = 0
        self.fail_action = fail_action
        self.fail_release = fail_release

    def button_down(self, button: MouseButton) -> None:
        if self.fail_action:
            raise MouseOutputError("distinct button action failure")
        self.calls.append((button, True))

    def button_up(self, button: MouseButton) -> None:
        if self.fail_action:
            raise MouseOutputError("distinct button action failure")
        self.calls.append((button, False))

    def release_all(self) -> None:
        self.release_calls += 1
        if self.fail_release:
            raise MouseOutputError("release cleanup failure")

    def close(self) -> None:
        self.closed += 1


class FalsyRecordingOutput(RecordingOutput):
    def __bool__(self) -> bool:
        return False


class FalsyRecordingButtonOutput(RecordingButtonOutput):
    def __bool__(self) -> bool:
        return False


class FalsyMapper(VirtualCursorMapper):
    def __init__(self) -> None:
        super().__init__(VirtualSurface(100, 100))

    def __bool__(self) -> bool:
        return False


def hand(x: float = 0.25, y: float = 0.75, source: int = 0) -> HandSelection:
    points = [Landmark3D(0, 0, 0) for _ in range(21)]
    points[8] = Landmark3D(x, y, 0)
    return HandSelection(
        1, HandObservation(tuple(points), source, Handedness.RIGHT, 1, None, 1, 1)
    )


class GestureMouseRuntimeCoordinatorTests(TestCase):
    def test_falsy_dependencies_are_retained_and_receive_lifecycle_calls(self) -> None:
        mapper = FalsyMapper()
        output = FalsyRecordingOutput()
        buttons = FalsyRecordingButtonOutput()
        policy = MouseButtonPolicy(buttons_enabled=True)
        coordinator = GestureMouseRuntimeCoordinator(
            "falsy-runtime",
            enabled=True,
            mapper=mapper,
            output=output,
            button_policy=policy,
            button_output=buttons,
        )
        result = coordinator.process(
            hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
        )
        self.assertTrue(result.moved)
        self.assertIs(coordinator._mapper, mapper)
        self.assertIs(coordinator._output, output)
        self.assertIs(coordinator._button_policy, policy)
        self.assertIs(coordinator._button_output, buttons)
        coordinator.close()
        self.assertEqual(output.closed, 1)
        self.assertEqual(buttons.closed, 1)

    def test_disabled_buttons_have_no_decision_and_close_button_output(self) -> None:
        buttons = RecordingButtonOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "buttons", enabled=True, button_output=buttons
        )
        result = coordinator.process(hand(), timestamp_ms=0)
        self.assertIsNone(result.button_decision)
        self.assertEqual(buttons.calls, [])
        coordinator.close()
        coordinator.close()
        self.assertEqual(buttons.release_calls, 1)
        self.assertEqual(buttons.closed, 1)

    def test_stale_timestamp_is_rejected_before_output(self) -> None:
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "timestamps", enabled=True, output=output
        )
        coordinator.process(hand(), timestamp_ms=10)
        with self.assertRaises(MouseValidationError):
            coordinator.process(hand(0.5), timestamp_ms=9)
        self.assertEqual(len(output.targets), 1)

    def test_non_point_stable_gesture_fails_closed_for_buttons(self) -> None:
        buttons = RecordingButtonOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "stable",
            enabled=True,
            button_policy=MouseButtonPolicy(buttons_enabled=True),
            button_output=buttons,
        )
        result = coordinator.process(
            hand(), timestamp_ms=0, stable_gesture=GestureId.OPEN_PALM
        )
        self.assertIsNotNone(result.button_decision)
        self.assertEqual(buttons.calls, [])

    def test_primary_click_requires_dwell_and_confirmed_release(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True)
        buttons = RecordingButtonOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "primary", enabled=True, button_policy=policy, button_output=buttons
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            coordinator.process(hand(), timestamp_ms=0, stable_gesture=GestureId.POINT)
            coordinator.process(
                hand(),
                timestamp_ms=policy.intent_activation_ms - 1,
                stable_gesture=GestureId.POINT,
            )
            coordinator.process(
                hand(),
                timestamp_ms=policy.intent_activation_ms,
                stable_gesture=GestureId.POINT,
            )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.NONE,
        ):
            coordinator.process(
                hand(),
                timestamp_ms=policy.intent_activation_ms + 1,
                stable_gesture=GestureId.POINT,
            )
            result = coordinator.process(
                hand(),
                timestamp_ms=policy.intent_activation_ms + 1 + policy.intent_release_ms,
                stable_gesture=GestureId.POINT,
            )
        self.assertEqual(
            result.button_decision.action, MouseButtonActionKind.PRIMARY_CLICK
        )
        self.assertEqual(
            buttons.calls, [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)]
        )

    def test_secondary_click_rearms_only_after_confirmed_none(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True)
        buttons = RecordingButtonOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "secondary", enabled=True, button_policy=policy, button_output=buttons
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.SECONDARY_CONTACT,
        ):
            coordinator.process(hand(), timestamp_ms=0, stable_gesture=GestureId.POINT)
            coordinator.process(
                hand(),
                timestamp_ms=policy.intent_activation_ms,
                stable_gesture=GestureId.POINT,
            )
            coordinator.process(
                hand(),
                timestamp_ms=policy.intent_activation_ms + 1,
                stable_gesture=GestureId.POINT,
            )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.NONE,
        ):
            coordinator.process(
                hand(), timestamp_ms=1_000, stable_gesture=GestureId.POINT
            )
            coordinator.process(
                hand(),
                timestamp_ms=1_000 + policy.intent_release_ms,
                stable_gesture=GestureId.POINT,
            )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.SECONDARY_CONTACT,
        ):
            coordinator.process(
                hand(), timestamp_ms=1_500, stable_gesture=GestureId.POINT
            )
            coordinator.process(
                hand(),
                timestamp_ms=1_500 + policy.intent_activation_ms,
                stable_gesture=GestureId.POINT,
            )
        self.assertEqual(
            buttons.calls,
            [
                (MouseButton.SECONDARY, True),
                (MouseButton.SECONDARY, False),
                (MouseButton.SECONDARY, True),
                (MouseButton.SECONDARY, False),
            ],
        )

    def test_drag_moves_cursor_and_non_point_releases_once(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        buttons = RecordingButtonOutput()
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "drag",
            enabled=True,
            button_policy=policy,
            button_output=buttons,
            output=output,
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            coordinator.process(hand(), timestamp_ms=0, stable_gesture=GestureId.POINT)
            coordinator.process(
                hand(0.3),
                timestamp_ms=policy.drag_hold_ms,
                stable_gesture=GestureId.POINT,
            )
            coordinator.process(
                hand(0.4),
                timestamp_ms=policy.drag_hold_ms + 1,
                stable_gesture=GestureId.POINT,
            )
        for timestamp, gesture in enumerate(
            (None, GestureId.OPEN_PALM, GestureId.CLOSED_FIST), start=1_000
        ):
            coordinator.process(hand(), timestamp_ms=timestamp, stable_gesture=gesture)
        self.assertEqual(
            buttons.calls, [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)]
        )
        self.assertGreaterEqual(len(output.targets), 3)

    def test_button_action_error_preserves_original_and_attempts_release(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        buttons = RecordingButtonOutput(fail_action=True, fail_release=True)
        coordinator = GestureMouseRuntimeCoordinator(
            "button-error", enabled=True, button_policy=policy, button_output=buttons
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            coordinator.process(hand(), timestamp_ms=0, stable_gesture=GestureId.POINT)
            with self.assertRaisesRegex(
                MouseOutputError, "distinct button action failure"
            ):
                coordinator.process(
                    hand(),
                    timestamp_ms=policy.drag_hold_ms,
                    stable_gesture=GestureId.POINT,
                )
        self.assertEqual(buttons.release_calls, 1)

    def test_button_failure_releases_shared_lease_and_disables_coordinator(
        self,
    ) -> None:
        lease = WindowsCursorOwnershipLease()
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        buttons = RecordingButtonOutput(fail_action=True, fail_release=True)
        coordinator = GestureMouseRuntimeCoordinator(
            "button-failure",
            enabled=True,
            button_policy=policy,
            button_output=buttons,
            windows_lease=lease,
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            coordinator.process(hand(), timestamp_ms=0, stable_gesture=GestureId.POINT)
            with self.assertRaisesRegex(
                MouseOutputError, "distinct button action failure"
            ):
                coordinator.process(
                    hand(),
                    timestamp_ms=policy.drag_hold_ms,
                    stable_gesture=GestureId.POINT,
                )
        self.assertEqual(buttons.release_calls, 1)
        self.assertFalse(coordinator.enabled)
        self.assertIsNone(lease.owner_id)

    def test_cursor_error_preserves_original_and_releases_ownership(self) -> None:
        lease = WindowsCursorOwnershipLease()
        coordinator = GestureMouseRuntimeCoordinator(
            "cursor-error",
            enabled=True,
            output=RecordingOutput(fail=True),
            windows_lease=lease,
        )
        with self.assertRaisesRegex(MouseOutputError, "failed"):
            coordinator.process(hand(), timestamp_ms=3)
        self.assertFalse(coordinator.enabled)
        self.assertIsNone(lease.owner_id)

    def test_lifecycle_aggregation_attempts_later_cleanup_and_rejects_stale_time(
        self,
    ) -> None:
        lease = WindowsCursorOwnershipLease()
        buttons = RecordingButtonOutput(fail_release=True)
        coordinator = GestureMouseRuntimeCoordinator(
            "cleanup", enabled=True, button_output=buttons, windows_lease=lease
        )
        coordinator.process(hand(), timestamp_ms=10)
        with self.assertRaisesRegex(MouseOutputError, "release cleanup failure"):
            coordinator.tracking_lost(timestamp_ms=10)
        self.assertEqual(buttons.release_calls, 1)
        self.assertIsNone(lease.owner_id)
        with self.assertRaises(MouseValidationError):
            coordinator.emergency_stop(timestamp_ms=9)

    def test_close_aggregates_disabled_button_cleanup_once(self) -> None:
        buttons = RecordingButtonOutput(fail_release=True)
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "close", output=output, button_output=buttons
        )
        with self.assertRaisesRegex(MouseOutputError, "release cleanup failure"):
            coordinator.close()
        coordinator.close()
        self.assertEqual(buttons.release_calls, 1)
        self.assertEqual(buttons.closed, 1)
        self.assertEqual(output.closed, 1)

    def test_disabled_default_never_maps_or_outputs(self) -> None:
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator("a", output=output)
        self.assertFalse(coordinator.process(hand(), timestamp_ms=0).moved)
        self.assertEqual(output.targets, [])

    def test_virtual_mapping_rate_boundaries_reset_and_source_change(self) -> None:
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "a", enabled=True, output=output, max_output_hz=10
        )
        self.assertTrue(coordinator.process(hand(), timestamp_ms=0).moved)
        self.assertTrue(coordinator.process(hand(0.5), timestamp_ms=100).moved)
        self.assertTrue(coordinator.process(hand(0.7), timestamp_ms=199).rate_limited)
        self.assertTrue(
            coordinator.process(hand(0.8, source=1), timestamp_ms=200).moved
        )
        self.assertTrue(coordinator.process(None, timestamp_ms=201).mapping is None)
        self.assertTrue(coordinator.process(hand(), timestamp_ms=202).moved)
        self.assertEqual(len(output.targets), 4)

    def test_windows_lease_denial_and_emergency_release(self) -> None:
        lease = WindowsCursorOwnershipLease()
        self.assertTrue(lease.acquire("other"))
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "a", enabled=True, output=output, windows_lease=lease
        )
        self.assertFalse(coordinator.process(hand(), timestamp_ms=0).moved)
        lease.release("other")
        self.assertTrue(coordinator.process(hand(0.5), timestamp_ms=1).moved)
        self.assertEqual(lease.owner_id, "a")
        coordinator.emergency_stop(timestamp_ms=2)
        self.assertIsNone(lease.owner_id)

    def test_lease_denial_prevents_button_state_and_cursor_output(self) -> None:
        lease = WindowsCursorOwnershipLease()
        self.assertTrue(lease.acquire("other"))
        buttons = RecordingButtonOutput()
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "denied",
            enabled=True,
            output=output,
            windows_lease=lease,
            button_policy=MouseButtonPolicy(buttons_enabled=True),
            button_output=buttons,
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            result = coordinator.process(
                hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
            )
        self.assertIsNone(result.button_decision)
        self.assertEqual(buttons.calls, [])
        self.assertEqual(output.targets, [])
        lease.release("other")
        coordinator.process(hand(), timestamp_ms=1, stable_gesture=GestureId.POINT)
        self.assertEqual(buttons.calls, [])

    def test_output_failure_stops_and_shutdown_is_idempotent(self) -> None:
        lease = WindowsCursorOwnershipLease()
        output = RecordingOutput(fail=True)
        coordinator = GestureMouseRuntimeCoordinator(
            "a", enabled=True, output=output, windows_lease=lease
        )
        with self.assertRaises(MouseOutputError):
            coordinator.process(hand(), timestamp_ms=0)
        self.assertFalse(coordinator.enabled)
        self.assertIsNone(lease.owner_id)
        coordinator.close()
        coordinator.close()
        self.assertEqual(output.closed, 1)
        with self.assertRaises(MouseLifecycleError):
            coordinator.process(hand(), timestamp_ms=1)
