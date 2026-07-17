from django.test import SimpleTestCase

from gestureboard.mouse.buttons import (
    MouseButtonActionKind,
    MouseButtonController,
    MouseButtonIntent,
    MouseButtonPolicy,
    MouseButtonState,
)
from gestureboard.mouse.models import MouseLifecycleError, MouseValidationError


class MouseButtonControllerTests(SimpleTestCase):
    def test_one_frame_and_release_chatter_do_not_click(self) -> None:
        controller = MouseButtonController(MouseButtonPolicy(buttons_enabled=True))
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=1
        )
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.NONE, timestamp_ms=1, source_index=1
            ).action
        )
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=2, source_index=1
            ).action
        )
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.NONE, timestamp_ms=121, source_index=1
            ).action
        )
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=150, source_index=1
            ).action
        )

    def test_secondary_clicks_at_activation_and_requires_release_to_rearm(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True)
        controller = MouseButtonController(policy)
        start_ms = 1_000
        activation_ms = policy.intent_activation_ms
        release_ms = policy.intent_release_ms
        first = controller.process(
            MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=start_ms, source_index=0
        )
        self.assertIsNone(first.action)
        self.assertFalse(first.primary_held)
        self.assertFalse(first.secondary_held)
        self.assertFalse(first.primary_held)
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.SECONDARY_CONTACT,
                timestamp_ms=start_ms + activation_ms - 1,
                source_index=0,
            ).action
        )
        self.assertEqual(
            controller.process(
                MouseButtonIntent.SECONDARY_CONTACT,
                timestamp_ms=start_ms + activation_ms,
                source_index=0,
            ).action,
            MouseButtonActionKind.SECONDARY_CLICK,
        )
        self.assertEqual(
            controller.process(
                MouseButtonIntent.SECONDARY_CONTACT,
                timestamp_ms=start_ms + activation_ms + 1,
                source_index=0,
            ).action,
            MouseButtonActionKind.SUPPRESSED,
        )
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.NONE,
                timestamp_ms=start_ms + activation_ms + 2,
                source_index=0,
            ).action
        )
        result = controller.process(
            MouseButtonIntent.NONE,
            timestamp_ms=start_ms + activation_ms + 2 + release_ms,
            source_index=0,
        )
        self.assertIsNone(result.action)
        self.assertFalse(result.secondary_held)

    def test_primary_click_requires_dwell_and_release(self) -> None:
        controller = MouseButtonController(MouseButtonPolicy(buttons_enabled=True))
        self.assertEqual(
            controller.process(
                MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=1
            ).state,
            MouseButtonState.PRIMARY_PENDING,
        )
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=120, source_index=1
            ).action
        )
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.NONE, timestamp_ms=121, source_index=1
            ).action
        )
        result = controller.process(
            MouseButtonIntent.NONE, timestamp_ms=201, source_index=1
        )
        self.assertEqual(result.action, MouseButtonActionKind.PRIMARY_CLICK)

    def test_primary_release_before_activation_never_clicks(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=1
        )
        controller.process(MouseButtonIntent.NONE, timestamp_ms=1, source_index=1)
        result = controller.process(
            MouseButtonIntent.NONE,
            timestamp_ms=1 + policy.intent_release_ms,
            source_index=1,
        )
        self.assertIsNone(result.action)

    def test_invalid_source_and_missing_hand_intent_cannot_click(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True)
        controller = MouseButtonController(policy)
        rejected = controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=None
        )
        self.assertFalse(rejected.accepted)
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=1, source_index=0
        )
        interrupted = controller.process(
            MouseButtonIntent.AMBIGUOUS, timestamp_ms=2, source_index=0
        )
        self.assertIsNone(interrupted.action)

    def test_primary_cooldown_suppresses_a_held_retry(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=1
        )
        controller.process(MouseButtonIntent.NONE, timestamp_ms=1, source_index=1)
        controller.process(
            MouseButtonIntent.NONE,
            timestamp_ms=1 + policy.intent_release_ms,
            source_index=1,
        )
        result = controller.process(
            MouseButtonIntent.PRIMARY_CONTACT,
            timestamp_ms=2 + policy.intent_release_ms,
            source_index=1,
        )
        self.assertEqual(result.action, MouseButtonActionKind.SUPPRESSED)

    def test_drag_down_and_single_release(self) -> None:
        controller = MouseButtonController(
            MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        )
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=1
        )
        self.assertEqual(
            controller.process(
                MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=500, source_index=1
            ).action,
            MouseButtonActionKind.PRIMARY_DOWN,
        )
        self.assertEqual(
            controller.process(
                MouseButtonIntent.NONE, timestamp_ms=581, source_index=1
            ).action,
            None,
        )
        self.assertEqual(
            controller.process(
                MouseButtonIntent.NONE, timestamp_ms=661, source_index=1
            ).action,
            MouseButtonActionKind.PRIMARY_UP,
        )

    def test_cooldown_requires_confirmed_release_and_a_fresh_epoch(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=0, source_index=1
        )
        click = controller.process(
            MouseButtonIntent.SECONDARY_CONTACT,
            timestamp_ms=policy.intent_activation_ms,
            source_index=1,
        )
        self.assertEqual(click.action, MouseButtonActionKind.SECONDARY_CLICK)
        before = controller.process(
            MouseButtonIntent.PRIMARY_CONTACT,
            timestamp_ms=policy.intent_activation_ms + policy.click_cooldown_ms - 1,
            source_index=1,
        )
        at_boundary = controller.process(
            MouseButtonIntent.PRIMARY_CONTACT,
            timestamp_ms=policy.intent_activation_ms + policy.click_cooldown_ms,
            source_index=1,
        )
        self.assertEqual(before.action, MouseButtonActionKind.SUPPRESSED)
        self.assertEqual(at_boundary.action, MouseButtonActionKind.SUPPRESSED)
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.NONE,
                timestamp_ms=1_000,
                source_index=1,
            ).action
        )
        self.assertIsNone(
            controller.process(
                MouseButtonIntent.NONE,
                timestamp_ms=1_000 + policy.intent_release_ms,
                source_index=1,
            ).action
        )
        fresh = controller.process(
            MouseButtonIntent.PRIMARY_CONTACT,
            timestamp_ms=1_001 + policy.intent_release_ms,
            source_index=1,
        )
        self.assertIsNone(fresh.action)
        self.assertEqual(fresh.state, MouseButtonState.PRIMARY_PENDING)

    def test_stale_timestamps_are_rejected_without_mutating_or_advancing_actions(
        self,
    ) -> None:
        controller = MouseButtonController(MouseButtonPolicy(buttons_enabled=True))
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=100, source_index=1
        )
        stale = controller.process(
            MouseButtonIntent.NONE, timestamp_ms=99, source_index=1
        )
        equal = controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=100, source_index=1
        )
        self.assertFalse(stale.accepted)
        self.assertIsNone(stale.action)
        self.assertEqual(stale.state, MouseButtonState.PRIMARY_PENDING)
        self.assertEqual(stale.sequence, 0)
        self.assertTrue(equal.accepted)
        self.assertEqual(equal.sequence, 0)
        self.assertFalse(
            controller.process(
                MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=2
            ).accepted
        )

    def test_source_change_and_reset_release_drag_once_without_clicking(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=1
        )
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT,
            timestamp_ms=policy.drag_hold_ms,
            source_index=1,
        )
        changed = controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=600, source_index=2
        )
        self.assertEqual(changed.action, MouseButtonActionKind.PRIMARY_UP)
        self.assertFalse(changed.primary_held)
        self.assertIsNone(controller.reset(timestamp_ms=601).action)
        controller.process(MouseButtonIntent.NONE, timestamp_ms=1_000, source_index=2)
        controller.process(
            MouseButtonIntent.NONE,
            timestamp_ms=1_000 + policy.intent_release_ms,
            source_index=2,
        )
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT,
            timestamp_ms=1_001 + policy.intent_release_ms,
            source_index=2,
        )
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT,
            timestamp_ms=1_001 + policy.intent_release_ms + policy.drag_hold_ms,
            source_index=2,
        )
        stopped = controller.emergency_stop(timestamp_ms=1_700)
        self.assertEqual(stopped.action, MouseButtonActionKind.PRIMARY_UP)
        self.assertIsNone(controller.emergency_stop(timestamp_ms=1_701).action)

    def test_shutdown_releases_drag_once_and_rejects_later_processing(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=1
        )
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT,
            timestamp_ms=policy.drag_hold_ms,
            source_index=1,
        )
        shutdown = controller.shutdown(timestamp_ms=501)
        self.assertEqual(shutdown.action, MouseButtonActionKind.PRIMARY_UP)
        self.assertEqual(shutdown.state, MouseButtonState.CLOSED)
        self.assertIsNone(controller.shutdown(timestamp_ms=502).action)
        with self.assertRaises(MouseLifecycleError):
            controller.process(MouseButtonIntent.NONE, timestamp_ms=503, source_index=1)

    def test_repeated_unsafe_interruptions_preserve_drag_release_latch(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=0
        )
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT,
            timestamp_ms=policy.drag_hold_ms,
            source_index=0,
        )
        released = controller.process(
            MouseButtonIntent.AMBIGUOUS, timestamp_ms=501, source_index=0
        )
        self.assertEqual(released.action, MouseButtonActionKind.PRIMARY_UP)
        sequence = released.sequence
        self.assertIsNone(controller.reset(timestamp_ms=502).action)
        self.assertIsNone(controller.emergency_stop(timestamp_ms=503).action)
        self.assertEqual(
            controller.process(
                MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=900, source_index=1
            ).action,
            MouseButtonActionKind.SUPPRESSED,
        )
        self.assertEqual(
            sequence,
            controller.process(
                MouseButtonIntent.AMBIGUOUS, timestamp_ms=901, source_index=1
            ).sequence,
        )

    def test_secondary_latch_and_invalid_intents_do_not_mutate(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=0, source_index=0
        )
        clicked = controller.process(
            MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=120, source_index=0
        )
        self.assertEqual(clicked.action, MouseButtonActionKind.SECONDARY_CLICK)
        for value in (None, "primary", 1, True, object()):
            rejected = controller.process(value, timestamp_ms=121, source_index=0)  # type: ignore[arg-type]
            self.assertFalse(rejected.accepted)
            self.assertEqual(rejected.sequence, clicked.sequence)
        self.assertEqual(
            controller.process(
                MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=500, source_index=0
            ).action,
            MouseButtonActionKind.SUPPRESSED,
        )

    def test_invalid_safety_timestamps_leave_drag_open_for_valid_retry(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=0
        )
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=500, source_index=0
        )
        for timestamp in (None, True, -1, 1.5, "1", 499):
            with (
                self.subTest(timestamp=timestamp),
                self.assertRaises(MouseValidationError),
            ):
                controller.shutdown(timestamp_ms=timestamp)  # type: ignore[arg-type]
        self.assertEqual(
            controller.shutdown(timestamp_ms=500).action,
            MouseButtonActionKind.PRIMARY_UP,
        )

    def test_confirmed_secondary_release_rearms_a_fresh_secondary_epoch(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=0, source_index=0
        )
        self.assertEqual(
            controller.process(
                MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=120, source_index=0
            ).action,
            MouseButtonActionKind.SECONDARY_CLICK,
        )
        controller.process(MouseButtonIntent.NONE, timestamp_ms=121, source_index=0)
        released = controller.process(
            MouseButtonIntent.NONE, timestamp_ms=201, source_index=0
        )
        self.assertEqual(
            (released.state, released.intent, released.action),
            (MouseButtonState.IDLE, MouseButtonIntent.NONE, None),
        )
        controller.process(
            MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=471, source_index=0
        )
        self.assertEqual(
            controller.process(
                MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=591, source_index=0
            ).action,
            MouseButtonActionKind.SECONDARY_CLICK,
        )

    def test_cross_source_stale_timestamp_is_rejected_without_releasing_drag(
        self,
    ) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=1
        )
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=500, source_index=1
        )
        stale = controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=100, source_index=2
        )
        self.assertFalse(stale.accepted)
        self.assertEqual(
            (stale.state, stale.action, stale.sequence),
            (MouseButtonState.DRAGGING, None, 1),
        )

    def test_drag_contact_switch_requires_release_before_secondary_click(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        controller = MouseButtonController(policy)
        controller.process(
            MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=0, source_index=0
        )
        self.assertEqual(
            controller.process(
                MouseButtonIntent.PRIMARY_CONTACT, timestamp_ms=500, source_index=0
            ).action,
            MouseButtonActionKind.PRIMARY_DOWN,
        )
        switched = controller.process(
            MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=501, source_index=0
        )
        self.assertEqual(
            (switched.action, switched.state, switched.intent),
            (
                MouseButtonActionKind.PRIMARY_UP,
                MouseButtonState.IDLE,
                MouseButtonIntent.NONE,
            ),
        )
        for timestamp in (600, 851, 900):
            self.assertEqual(
                controller.process(
                    MouseButtonIntent.SECONDARY_CONTACT,
                    timestamp_ms=timestamp,
                    source_index=0,
                ).action,
                MouseButtonActionKind.SUPPRESSED,
            )
        controller.process(MouseButtonIntent.NONE, timestamp_ms=901, source_index=0)
        controller.process(MouseButtonIntent.NONE, timestamp_ms=981, source_index=0)
        controller.process(
            MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=982, source_index=0
        )
        self.assertEqual(
            controller.process(
                MouseButtonIntent.SECONDARY_CONTACT, timestamp_ms=1_102, source_index=0
            ).action,
            MouseButtonActionKind.SECONDARY_CLICK,
        )
