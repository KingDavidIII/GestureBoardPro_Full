"""Deterministic tests for temporal gesture observation handling."""

from dataclasses import FrozenInstanceError
from math import inf, nan
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from gestureboard.services.action_dispatcher import (
    ActionDispatcherError,
    DispatchResult,
)
from gestureboard.services.gesture_classifier import (
    FingerState,
    GestureFeatures,
    GestureLabel,
    GesturePrediction,
)
from gestureboard.services.gesture_engine import (
    GestureEngine,
    GestureEngineConfig,
    GestureEngineDecision,
    GestureEngineError,
    GestureObservation,
    GestureRepeatPolicy,
    NeutralGestureObservation,
    NeutralObservationReason,
)
from gestureboard.services.keyboard_controller import (
    KeyboardAction,
    KeyboardExecutionResult,
)


def prediction(label: GestureLabel) -> GesturePrediction:
    state = FingerState(False, True, -1.0, 0.5)
    features = GestureFeatures(
        thumb=state,
        index=state,
        middle=state,
        ring=state,
        little=state,
        thumb_index_distance=1.0,
        fingertip_distances=(0.5,) * 5,
    )
    return GesturePrediction(label=label, features=features)


def observation(
    label: GestureLabel,
    confidence: float = 0.95,
) -> GestureObservation:
    return GestureObservation(prediction(label), confidence)


class FakeDispatcher:
    def __init__(self, mapped: set[GestureLabel] | None = None) -> None:
        self.mapped = mapped if mapped is not None else {GestureLabel.OPEN_PALM}
        self.predictions: list[GesturePrediction] = []
        self.error: ActionDispatcherError | None = None
        self.close_count = 0

    def dispatch(self, item: GesturePrediction) -> DispatchResult:
        self.predictions.append(item)
        if self.error is not None:
            raise self.error
        action = KeyboardAction.tap("a") if item.label in self.mapped else None
        execution = KeyboardExecutionResult(action) if action is not None else None
        return DispatchResult(item.label, action, execution)

    def close(self) -> None:
        self.close_count += 1


class GestureObservationAndConfigTests(SimpleTestCase):
    def test_valid_observation_preserves_explicit_detection_confidence(self) -> None:
        item = observation(GestureLabel.POINT, 0.82)

        self.assertEqual(item.detection_confidence, 0.82)
        self.assertEqual(item.prediction.label, GestureLabel.POINT)

    def test_invalid_detection_confidence_is_rejected(self) -> None:
        invalid = (-0.1, 1.1, nan, inf, "0.8", True)
        for confidence in invalid:
            with self.subTest(confidence=confidence):
                with self.assertRaisesRegex(GestureEngineError, "detection_confidence"):
                    GestureObservation(prediction(GestureLabel.POINT), confidence)

    def test_observation_requires_real_prediction(self) -> None:
        with self.assertRaisesRegex(GestureEngineError, "GesturePrediction"):
            GestureObservation(MagicMock(), 0.9)

    def test_valid_configuration_is_immutable_and_protects_mapping(self) -> None:
        policies = {
            GestureLabel.POINT: GestureRepeatPolicy(1.0, 0.25),
        }
        config = GestureEngineConfig(repeat_policies=policies)
        policies.clear()

        self.assertIn(GestureLabel.POINT, config.repeat_policies)
        with self.assertRaises(TypeError):
            config.repeat_policies[GestureLabel.PEACE] = GestureRepeatPolicy(1, 1)
        with self.assertRaises(FrozenInstanceError):
            config.activation_frames = 9

    def test_invalid_base_configuration_is_rejected(self) -> None:
        invalid = (
            {"minimum_detection_confidence": -0.1},
            {"minimum_detection_confidence": 1.1},
            {"minimum_detection_confidence": True},
            {"activation_frames": 0},
            {"activation_frames": 1.5},
            {"release_frames": 0},
            {"release_frames": 1.5},
            {"cooldown_seconds": -0.1},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(GestureEngineError):
                    GestureEngineConfig(**values)

    def test_invalid_repeat_settings_are_rejected(self) -> None:
        for delay, interval in ((-1, 1), (0, 0), (0, -1), (nan, 1), (1, inf)):
            with self.subTest(delay=delay, interval=interval):
                with self.assertRaises(GestureEngineError):
                    GestureRepeatPolicy(delay, interval)
        with self.assertRaises(GestureEngineError):
            GestureEngineConfig(
                repeat_policies={GestureLabel.UNKNOWN: GestureRepeatPolicy(1, 1)}
            )


class GestureEngineTests(SimpleTestCase):
    def setUp(self) -> None:
        self.dispatcher = FakeDispatcher(
            {GestureLabel.OPEN_PALM, GestureLabel.POINT, GestureLabel.PEACE}
        )
        self.config = GestureEngineConfig(
            minimum_detection_confidence=0.7,
            activation_frames=2,
            release_frames=2,
            cooldown_seconds=0.0,
        )
        self.engine = GestureEngine(self.dispatcher, self.config)

    def test_low_confidence_resets_candidate_and_does_not_dispatch(self) -> None:
        self.engine.process(observation(GestureLabel.POINT), timestamp=0)
        result = self.engine.process(observation(GestureLabel.POINT, 0.4), timestamp=1)

        self.assertEqual(result.decision, GestureEngineDecision.LOW_CONFIDENCE)
        self.assertIsNone(result.candidate_label)
        self.assertEqual(result.candidate_frame_count, 0)
        self.assertEqual(self.dispatcher.predictions, [])

    def test_unknown_resets_candidate_and_does_not_dispatch(self) -> None:
        self.engine.process(observation(GestureLabel.POINT), timestamp=0)
        result = self.engine.process(observation(GestureLabel.UNKNOWN), timestamp=1)

        self.assertEqual(result.decision, GestureEngineDecision.UNKNOWN)
        self.assertIsNone(result.candidate_label)
        self.assertEqual(self.dispatcher.predictions, [])

    def test_activation_requires_consecutive_frames(self) -> None:
        first = self.engine.process(observation(GestureLabel.POINT), timestamp=0)
        second_item = observation(GestureLabel.POINT)
        second = self.engine.process(second_item, timestamp=1)

        self.assertEqual(first.decision, GestureEngineDecision.ACCUMULATING)
        self.assertEqual(first.candidate_frame_count, 1)
        self.assertEqual(second.decision, GestureEngineDecision.DISPATCHED)
        self.assertEqual(second.active_label, GestureLabel.POINT)
        self.assertIs(self.dispatcher.predictions[0], second_item.prediction)
        self.assertIs(second.prediction, second_item.prediction)
        self.assertEqual(second.detection_confidence, 0.95)

    def test_label_change_starts_independent_candidate_count(self) -> None:
        self.engine.process(observation(GestureLabel.POINT), timestamp=0)
        changed = self.engine.process(observation(GestureLabel.PEACE), timestamp=1)

        self.assertEqual(changed.decision, GestureEngineDecision.ACCUMULATING)
        self.assertEqual(changed.candidate_label, GestureLabel.PEACE)
        self.assertEqual(changed.candidate_frame_count, 1)

    def test_continuously_held_gesture_dispatches_only_once(self) -> None:
        self.engine.process(observation(GestureLabel.POINT), timestamp=0)
        self.engine.process(observation(GestureLabel.POINT), timestamp=1)
        held = self.engine.process(observation(GestureLabel.POINT), timestamp=2)
        held_again = self.engine.process(observation(GestureLabel.POINT), timestamp=3)

        self.assertEqual(held.decision, GestureEngineDecision.HELD_SUPPRESSED)
        self.assertEqual(held_again.decision, GestureEngineDecision.HELD_SUPPRESSED)
        self.assertEqual(len(self.dispatcher.predictions), 1)

    def test_release_requires_configured_neutral_frames(self) -> None:
        self._activate(GestureLabel.POINT)
        partial = self.engine.process(observation(GestureLabel.UNKNOWN), timestamp=2)
        resumed = self.engine.process(observation(GestureLabel.POINT), timestamp=3)

        self.assertEqual(partial.decision, GestureEngineDecision.RELEASE_ACCUMULATING)
        self.assertEqual(partial.release_frame_count, 1)
        self.assertEqual(resumed.decision, GestureEngineDecision.HELD_SUPPRESSED)
        self.assertEqual(resumed.release_frame_count, 0)

    def test_full_release_rearms_same_gesture(self) -> None:
        self._activate(GestureLabel.POINT)
        self.engine.process(observation(GestureLabel.UNKNOWN), timestamp=2)
        released = self.engine.process(observation(GestureLabel.UNKNOWN), timestamp=3)
        self.engine.process(observation(GestureLabel.POINT), timestamp=4)
        dispatched = self.engine.process(observation(GestureLabel.POINT), timestamp=5)

        self.assertEqual(released.decision, GestureEngineDecision.RELEASED)
        self.assertIsNone(released.active_label)
        self.assertEqual(dispatched.decision, GestureEngineDecision.DISPATCHED)
        self.assertEqual(len(self.dispatcher.predictions), 2)

    def test_direct_transition_requires_new_stability_sequence(self) -> None:
        self._activate(GestureLabel.POINT)
        first = self.engine.process(observation(GestureLabel.PEACE), timestamp=2)
        second = self.engine.process(observation(GestureLabel.PEACE), timestamp=3)

        self.assertEqual(first.decision, GestureEngineDecision.ACCUMULATING)
        self.assertEqual(first.active_label, GestureLabel.POINT)
        self.assertEqual(second.decision, GestureEngineDecision.DISPATCHED)
        self.assertEqual(second.active_label, GestureLabel.PEACE)

    def test_unmapped_dispatch_result_is_preserved(self) -> None:
        self.engine.process(observation(GestureLabel.FIST), timestamp=0)
        result = self.engine.process(observation(GestureLabel.FIST), timestamp=1)

        self.assertEqual(result.decision, GestureEngineDecision.UNMAPPED)
        self.assertIsNotNone(result.dispatch_result)
        self.assertFalse(result.action_executed)

    def test_reset_clears_state_without_closing_dispatcher(self) -> None:
        self._activate(GestureLabel.POINT)
        self.engine.reset()
        result = self.engine.process(observation(GestureLabel.POINT), timestamp=0)

        self.assertEqual(result.decision, GestureEngineDecision.ACCUMULATING)
        self.assertIsNone(result.active_label)
        self.assertEqual(self.dispatcher.close_count, 0)

    def test_dispatch_failure_is_chained_and_activation_is_committed(self) -> None:
        original = ActionDispatcherError("controller failed")
        self.dispatcher.error = original
        self.engine.process(observation(GestureLabel.POINT), timestamp=0)

        with self.assertRaises(GestureEngineError) as caught:
            self.engine.process(observation(GestureLabel.POINT), timestamp=1)

        self.assertIn("POINT", str(caught.exception))
        self.assertIs(caught.exception.__cause__, original)
        self.dispatcher.error = None
        held = self.engine.process(observation(GestureLabel.POINT), timestamp=2)
        self.assertEqual(held.decision, GestureEngineDecision.HELD_SUPPRESSED)

    def test_result_objects_are_immutable(self) -> None:
        result = self.engine.process(observation(GestureLabel.POINT), timestamp=0)
        with self.assertRaises(FrozenInstanceError):
            result.timestamp = 10

    def _activate(self, label: GestureLabel) -> None:
        self.engine.process(observation(label), timestamp=0)
        self.engine.process(observation(label), timestamp=1)


class CooldownAndRepeatTests(SimpleTestCase):
    def test_cooldown_keeps_candidate_valid_until_dispatch_allowed(self) -> None:
        dispatcher = FakeDispatcher({GestureLabel.POINT, GestureLabel.PEACE})
        engine = GestureEngine(
            dispatcher,
            GestureEngineConfig(
                activation_frames=1,
                release_frames=1,
                cooldown_seconds=2.0,
            ),
        )
        engine.process(observation(GestureLabel.POINT), timestamp=0)
        suppressed = engine.process(observation(GestureLabel.PEACE), timestamp=1)
        dispatched = engine.process(observation(GestureLabel.PEACE), timestamp=2)

        self.assertEqual(suppressed.decision, GestureEngineDecision.COOLDOWN_SUPPRESSED)
        self.assertEqual(suppressed.candidate_label, GestureLabel.PEACE)
        self.assertEqual(suppressed.active_label, GestureLabel.POINT)
        self.assertEqual(dispatched.decision, GestureEngineDecision.DISPATCHED)
        self.assertEqual(dispatched.active_label, GestureLabel.PEACE)

    def test_repeating_is_disabled_without_explicit_policy(self) -> None:
        dispatcher = FakeDispatcher()
        engine = GestureEngine(
            dispatcher,
            GestureEngineConfig(activation_frames=1, cooldown_seconds=0),
        )
        engine.process(observation(GestureLabel.OPEN_PALM), timestamp=0)
        result = engine.process(observation(GestureLabel.OPEN_PALM), timestamp=100)

        self.assertEqual(result.decision, GestureEngineDecision.HELD_SUPPRESSED)
        self.assertEqual(len(dispatcher.predictions), 1)

    def test_repeat_delay_interval_and_cooldown_are_enforced(self) -> None:
        dispatcher = FakeDispatcher()
        engine = GestureEngine(
            dispatcher,
            GestureEngineConfig(
                activation_frames=1,
                cooldown_seconds=0.5,
                repeat_policies={GestureLabel.OPEN_PALM: GestureRepeatPolicy(2.0, 1.0)},
            ),
        )
        engine.process(observation(GestureLabel.OPEN_PALM), timestamp=0)
        waiting_delay = engine.process(
            observation(GestureLabel.OPEN_PALM), timestamp=1.9
        )
        repeated = engine.process(observation(GestureLabel.OPEN_PALM), timestamp=2.0)
        waiting_interval = engine.process(
            observation(GestureLabel.OPEN_PALM), timestamp=2.9
        )
        repeated_again = engine.process(
            observation(GestureLabel.OPEN_PALM), timestamp=3.0
        )

        self.assertEqual(waiting_delay.decision, GestureEngineDecision.REPEAT_WAITING)
        self.assertEqual(repeated.decision, GestureEngineDecision.REPEATED)
        self.assertEqual(
            waiting_interval.decision, GestureEngineDecision.REPEAT_WAITING
        )
        self.assertEqual(repeated_again.decision, GestureEngineDecision.REPEATED)
        self.assertEqual(len(dispatcher.predictions), 3)

    def test_cooldown_can_delay_an_otherwise_due_repeat(self) -> None:
        dispatcher = FakeDispatcher()
        engine = GestureEngine(
            dispatcher,
            GestureEngineConfig(
                activation_frames=1,
                cooldown_seconds=2,
                repeat_policies={GestureLabel.OPEN_PALM: GestureRepeatPolicy(0.5, 0.5)},
            ),
        )
        engine.process(observation(GestureLabel.OPEN_PALM), timestamp=0)
        suppressed = engine.process(observation(GestureLabel.OPEN_PALM), timestamp=1)
        repeated = engine.process(observation(GestureLabel.OPEN_PALM), timestamp=2)

        self.assertEqual(suppressed.decision, GestureEngineDecision.COOLDOWN_SUPPRESSED)
        self.assertEqual(suppressed.active_label, GestureLabel.OPEN_PALM)
        self.assertEqual(repeated.decision, GestureEngineDecision.REPEATED)

    def test_release_resets_repeat_delay(self) -> None:
        dispatcher = FakeDispatcher()
        engine = GestureEngine(
            dispatcher,
            GestureEngineConfig(
                activation_frames=1,
                release_frames=1,
                cooldown_seconds=0,
                repeat_policies={GestureLabel.OPEN_PALM: GestureRepeatPolicy(2, 1)},
            ),
        )
        engine.process(observation(GestureLabel.OPEN_PALM), timestamp=0)
        engine.process(observation(GestureLabel.UNKNOWN), timestamp=1)
        engine.process(observation(GestureLabel.OPEN_PALM), timestamp=2)
        waiting = engine.process(observation(GestureLabel.OPEN_PALM), timestamp=3)

        self.assertEqual(waiting.decision, GestureEngineDecision.REPEAT_WAITING)

    def test_gesture_without_policy_never_inherits_another_policy(self) -> None:
        dispatcher = FakeDispatcher({GestureLabel.POINT, GestureLabel.PEACE})
        engine = GestureEngine(
            dispatcher,
            GestureEngineConfig(
                activation_frames=1,
                cooldown_seconds=0,
                repeat_policies={GestureLabel.POINT: GestureRepeatPolicy(0, 1)},
            ),
        )
        engine.process(observation(GestureLabel.PEACE), timestamp=0)
        held = engine.process(observation(GestureLabel.PEACE), timestamp=10)

        self.assertEqual(held.decision, GestureEngineDecision.HELD_SUPPRESSED)


class NeutralGestureObservationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.dispatcher = FakeDispatcher({GestureLabel.POINT})
        self.engine = GestureEngine(
            self.dispatcher,
            GestureEngineConfig(
                activation_frames=2,
                release_frames=2,
                cooldown_seconds=0,
            ),
        )

    def test_inactive_no_hand_is_preserved_and_never_dispatched(self) -> None:
        neutral = NeutralGestureObservation()

        result = self.engine.process(neutral, timestamp=0)

        self.assertIs(result.observation, neutral)
        self.assertEqual(result.decision, GestureEngineDecision.NO_HAND)
        self.assertIsNone(result.prediction)
        self.assertIsNone(result.detection_confidence)
        self.assertEqual(self.dispatcher.predictions, [])

    def test_no_hand_resets_accumulating_candidate(self) -> None:
        self.engine.process(observation(GestureLabel.POINT), timestamp=0)
        result = self.engine.process(NeutralGestureObservation(), timestamp=1)

        self.assertEqual(result.decision, GestureEngineDecision.NO_HAND)
        self.assertIsNone(result.candidate_label)
        self.assertEqual(result.candidate_frame_count, 0)

    def test_no_hand_contributes_to_release_without_immediate_rearm(self) -> None:
        self._activate()
        partial = self.engine.process(NeutralGestureObservation(), timestamp=2)
        returned = self.engine.process(observation(GestureLabel.POINT), timestamp=3)

        self.assertEqual(partial.decision, GestureEngineDecision.RELEASE_ACCUMULATING)
        self.assertEqual(returned.decision, GestureEngineDecision.HELD_SUPPRESSED)
        self.assertEqual(len(self.dispatcher.predictions), 1)

    def test_full_no_hand_release_allows_same_gesture_again(self) -> None:
        self._activate()
        self.engine.process(NeutralGestureObservation(), timestamp=2)
        released = self.engine.process(NeutralGestureObservation(), timestamp=3)
        self.engine.process(observation(GestureLabel.POINT), timestamp=4)
        dispatched = self.engine.process(observation(GestureLabel.POINT), timestamp=5)

        self.assertEqual(released.decision, GestureEngineDecision.RELEASED)
        self.assertEqual(dispatched.decision, GestureEngineDecision.DISPATCHED)
        self.assertEqual(len(self.dispatcher.predictions), 2)

    def test_full_no_hand_release_resets_repeat_timing(self) -> None:
        engine = GestureEngine(
            self.dispatcher,
            GestureEngineConfig(
                activation_frames=2,
                release_frames=2,
                cooldown_seconds=0,
                repeat_policies={GestureLabel.POINT: GestureRepeatPolicy(2, 1)},
            ),
        )
        engine.process(observation(GestureLabel.POINT), timestamp=0)
        engine.process(observation(GestureLabel.POINT), timestamp=1)
        engine.process(NeutralGestureObservation(), timestamp=2)
        engine.process(NeutralGestureObservation(), timestamp=3)
        engine.process(observation(GestureLabel.POINT), timestamp=4)
        engine.process(observation(GestureLabel.POINT), timestamp=5)
        waiting = engine.process(observation(GestureLabel.POINT), timestamp=6)

        self.assertEqual(waiting.decision, GestureEngineDecision.REPEAT_WAITING)

    def test_neutral_reason_is_typed_and_immutable(self) -> None:
        neutral = NeutralGestureObservation(NeutralObservationReason.NO_HAND_DETECTED)
        with self.assertRaises(FrozenInstanceError):
            neutral.reason = NeutralObservationReason.NO_HAND_DETECTED
        with self.assertRaises(GestureEngineError):
            NeutralGestureObservation("NO_HAND_DETECTED")

    def test_timestamp_validation_applies_to_neutral_input(self) -> None:
        self.engine.process(NeutralGestureObservation(), timestamp=2)
        with self.assertRaisesRegex(GestureEngineError, "earlier"):
            self.engine.process(NeutralGestureObservation(), timestamp=1)

    def _activate(self) -> None:
        self.engine.process(observation(GestureLabel.POINT), timestamp=0)
        self.engine.process(observation(GestureLabel.POINT), timestamp=1)


class TimestampAndLifecycleTests(SimpleTestCase):
    def test_injected_clock_is_used(self) -> None:
        dispatcher = FakeDispatcher()
        clock = MagicMock(return_value=12.5)
        engine = GestureEngine(dispatcher, clock=clock)

        result = engine.process(observation(GestureLabel.POINT))

        self.assertEqual(result.timestamp, 12.5)
        clock.assert_called_once_with()

    def test_malformed_and_non_monotonic_timestamps_are_rejected(self) -> None:
        engine = GestureEngine(FakeDispatcher())
        for value in (nan, inf, "1", True):
            with self.subTest(value=value):
                engine.reset()
                with self.assertRaisesRegex(GestureEngineError, "timestamp"):
                    engine.process(observation(GestureLabel.POINT), timestamp=value)
        engine.process(observation(GestureLabel.POINT), timestamp=2)
        with self.assertRaisesRegex(GestureEngineError, "earlier"):
            engine.process(observation(GestureLabel.POINT), timestamp=1)

    def test_injected_dispatcher_is_not_closed_and_process_is_rejected(self) -> None:
        dispatcher = FakeDispatcher()
        engine = GestureEngine(dispatcher)
        engine.close()
        engine.close()

        self.assertEqual(dispatcher.close_count, 0)
        with self.assertRaisesRegex(GestureEngineError, "closed"):
            engine.process(observation(GestureLabel.POINT), timestamp=0)

    def test_context_manager_closes_owned_dispatcher_once(self) -> None:
        owned = MagicMock()
        with patch(
            "gestureboard.services.gesture_engine.ActionDispatcher",
            return_value=owned,
        ):
            with GestureEngine() as engine:
                self.assertIs(engine.dispatcher, owned)
            engine.close()

        owned.close.assert_called_once_with()
