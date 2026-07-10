"""Tests for synchronous runtime coordination and hand arbitration."""

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, call, patch

import numpy as np
from django.test import SimpleTestCase

from gestureboard.services.action_dispatcher import DispatchResult
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
    GestureEngineResult,
    GestureObservation,
    NeutralGestureObservation,
    NeutralObservationReason,
)
from gestureboard.services.gesture_pipeline import (
    GesturePipelineError,
    GesturePipelineResult,
    HandGestureResult,
)
from gestureboard.services.gesture_runtime import (
    GestureRuntime,
    GestureRuntimeConfig,
    GestureRuntimeError,
    GestureRuntimeStage,
    HandSelectionDecision,
    HandSelectionPolicy,
    SelectedHandIdentity,
)
from gestureboard.services.keyboard_controller import (
    KeyboardAction,
    KeyboardExecutionResult,
)


def make_prediction(label: GestureLabel = GestureLabel.POINT) -> GesturePrediction:
    state = FingerState(False, True, -1.0, 0.5)
    return GesturePrediction(
        label,
        GestureFeatures(state, state, state, state, state, 1.0, (0.5,) * 5),
    )


def make_hand(
    index: int,
    handedness: str,
    confidence: float,
    label: GestureLabel = GestureLabel.POINT,
) -> HandGestureResult:
    return HandGestureResult(
        hand_index=index,
        handedness=handedness,
        confidence=confidence,
        normalized_landmarks=(),
        prediction=make_prediction(label),
    )


def make_engine_result(
    item: GestureObservation | NeutralGestureObservation,
    *,
    decision: GestureEngineDecision = GestureEngineDecision.ACCUMULATING,
    active_label: GestureLabel | None = None,
    executed: bool = False,
    timestamp: float = 1.0,
) -> GestureEngineResult:
    dispatch_result = None
    if executed and isinstance(item, GestureObservation):
        action = KeyboardAction.tap("a")
        dispatch_result = DispatchResult(
            item.prediction.label,
            action,
            KeyboardExecutionResult(action),
        )
    return GestureEngineResult(
        observation=item,
        decision=decision,
        candidate_label=None,
        candidate_frame_count=0,
        active_label=active_label,
        release_frame_count=0,
        timestamp=timestamp,
        dispatch_result=dispatch_result,
    )


class RuntimeConfigTests(SimpleTestCase):
    def test_default_configuration_is_conservative_and_immutable(self) -> None:
        config = GestureRuntimeConfig()

        self.assertEqual(config.selection_policy, HandSelectionPolicy.FIRST_DETECTED)
        self.assertTrue(config.sticky_selection)
        with self.assertRaises(FrozenInstanceError):
            config.sticky_selection = False

    def test_preferred_handedness_validation(self) -> None:
        with self.assertRaises(GestureRuntimeError):
            GestureRuntimeConfig(
                selection_policy=HandSelectionPolicy.PREFERRED_HANDEDNESS
            )
        with self.assertRaises(GestureRuntimeError):
            GestureRuntimeConfig(
                selection_policy=HandSelectionPolicy.PREFERRED_HANDEDNESS,
                preferred_handedness=" ",
            )
        with self.assertRaises(GestureRuntimeError):
            GestureRuntimeConfig(preferred_handedness="Left")
        with self.assertRaises(GestureRuntimeError):
            GestureRuntimeConfig(sticky_selection=1)


class GestureRuntimeTests(SimpleTestCase):
    def setUp(self) -> None:
        self.pipeline = MagicMock()
        self.engine = MagicMock()
        self.frame = np.zeros((4, 6, 3), dtype=np.uint8)
        self.annotated = np.ones_like(self.frame)
        self.hand = make_hand(0, "Right", 0.91)
        self.pipeline_result = GesturePipelineResult(self.annotated, (self.hand,))
        self.pipeline.process.return_value = self.pipeline_result

        def process(item, *, timestamp=None):
            return make_engine_result(item, timestamp=timestamp or 0.0)

        self.engine.process.side_effect = process
        self.runtime = GestureRuntime(self.pipeline, self.engine)

    def test_pipeline_and_engine_coordination_preserves_exact_instances(self) -> None:
        result = self.runtime.process(self.frame, timestamp=7.5)

        self.pipeline.process.assert_called_once_with(self.frame)
        self.engine.process.assert_called_once_with(result.observation, timestamp=7.5)
        self.assertIs(result.pipeline_result, self.pipeline_result)
        self.assertIs(result.annotated_frame, self.annotated)
        self.assertIs(result.selected_hand, self.hand)
        self.assertIs(result.observation.prediction, self.hand.prediction)
        self.assertEqual(result.observation.detection_confidence, 0.91)
        self.assertEqual(result.detected_hand_count, 1)
        self.assertEqual(result.timestamp, 7.5)

    def test_one_frame_calls_pipeline_and_engine_exactly_once(self) -> None:
        self.runtime.process(self.frame)

        self.assertEqual(self.pipeline.process.call_count, 1)
        self.assertEqual(self.engine.process.call_count, 1)

    def test_no_hand_creates_explicit_neutral_observation(self) -> None:
        empty = GesturePipelineResult(self.annotated, ())
        self.pipeline.process.return_value = empty

        result = self.runtime.process(self.frame, timestamp=2)

        self.assertIs(result.pipeline_result, empty)
        self.assertIsNone(result.selected_hand)
        self.assertIsNone(result.selected_identity)
        self.assertEqual(result.selection_decision, HandSelectionDecision.NO_HANDS)
        self.assertIsInstance(result.observation, NeutralGestureObservation)
        self.assertEqual(
            result.observation.reason,
            NeutralObservationReason.NO_HAND_DETECTED,
        )
        self.engine.process.assert_called_once_with(result.observation, timestamp=2)

    def test_runtime_result_reflects_exact_engine_result_and_execution(self) -> None:
        captured = None

        def process(item, *, timestamp=None):
            nonlocal captured
            captured = make_engine_result(
                item,
                decision=GestureEngineDecision.DISPATCHED,
                active_label=GestureLabel.POINT,
                executed=True,
                timestamp=timestamp,
            )
            return captured

        self.engine.process.side_effect = process

        result = self.runtime.process(self.frame, timestamp=3)

        self.assertIs(result.engine_result, captured)
        self.assertTrue(result.action_executed)
        with self.assertRaises(FrozenInstanceError):
            result.selected_hand = None

    def test_unmapped_engine_result_remains_valid(self) -> None:
        def process(item, *, timestamp=None):
            return make_engine_result(
                item,
                decision=GestureEngineDecision.UNMAPPED,
                timestamp=timestamp,
            )

        self.engine.process.side_effect = process
        result = self.runtime.process(self.frame, timestamp=1)

        self.assertEqual(result.engine_result.decision, GestureEngineDecision.UNMAPPED)
        self.assertFalse(result.action_executed)

    def test_first_selection_does_not_reset_and_same_identity_continues(self) -> None:
        first = self.runtime.process(self.frame, timestamp=0)
        second = self.runtime.process(self.frame, timestamp=1)

        self.engine.reset.assert_not_called()
        self.assertEqual(first.selection_decision, HandSelectionDecision.FIRST_DETECTED)
        self.assertEqual(
            second.selection_decision, HandSelectionDecision.STICKY_RETAINED
        )

    def test_changed_identity_resets_before_processing_and_reports_switch(self) -> None:
        self.runtime.process(self.frame, timestamp=0)
        other = make_hand(1, "Left", 0.99)
        self.pipeline.process.return_value = GesturePipelineResult(
            self.annotated, (other,)
        )
        manager = MagicMock()
        manager.attach_mock(self.engine.reset, "reset")
        manager.attach_mock(self.engine.process, "process")

        result = self.runtime.process(self.frame, timestamp=1)

        self.assertEqual(result.selection_decision, HandSelectionDecision.HAND_SWITCHED)
        self.assertEqual(result.selected_identity, SelectedHandIdentity(1, "left"))
        self.assertEqual(manager.mock_calls[0], call.reset())
        self.assertEqual(manager.mock_calls[1][0], "process")

    def test_reset_clears_identity_without_closing_dependencies(self) -> None:
        self.runtime.process(self.frame)
        self.runtime.reset()
        self.runtime.process(self.frame)

        self.engine.reset.assert_called_once_with()
        self.pipeline.close.assert_not_called()
        self.engine.close.assert_not_called()
        self.assertEqual(
            self.engine.process.call_count,
            2,
        )


class HandSelectionTests(SimpleTestCase):
    def _run(
        self,
        config: GestureRuntimeConfig,
        hands: tuple[HandGestureResult, ...],
    ):
        pipeline = MagicMock()
        engine = MagicMock()
        pipeline.process.return_value = GesturePipelineResult(object(), hands)
        engine.process.side_effect = lambda item, timestamp=None: make_engine_result(
            item
        )
        return GestureRuntime(pipeline, engine, config).process(object())

    def test_first_detected_selects_first(self) -> None:
        hands = (make_hand(0, "Left", 0.2), make_hand(1, "Right", 0.99))
        result = self._run(GestureRuntimeConfig(), hands)
        self.assertIs(result.selected_hand, hands[0])

    def test_highest_confidence_and_tie_preserve_order(self) -> None:
        hands = (make_hand(0, "Left", 0.6), make_hand(1, "Right", 0.9))
        config = GestureRuntimeConfig(
            selection_policy=HandSelectionPolicy.HIGHEST_CONFIDENCE,
            sticky_selection=False,
        )
        self.assertIs(self._run(config, hands).selected_hand, hands[1])
        tied = (make_hand(0, "Left", 0.9), make_hand(1, "Right", 0.9))
        self.assertIs(self._run(config, tied).selected_hand, tied[0])

    def test_preferred_handedness_matches_case_insensitively_and_falls_back(
        self,
    ) -> None:
        hands = (make_hand(0, "Left", 0.8), make_hand(1, "RIGHT", 0.7))
        right = GestureRuntimeConfig(
            selection_policy=HandSelectionPolicy.PREFERRED_HANDEDNESS,
            preferred_handedness="right",
            sticky_selection=False,
        )
        missing = GestureRuntimeConfig(
            selection_policy=HandSelectionPolicy.PREFERRED_HANDEDNESS,
            preferred_handedness="unknown",
            sticky_selection=False,
        )
        self.assertIs(self._run(right, hands).selected_hand, hands[1])
        fallback = self._run(missing, hands)
        self.assertIs(fallback.selected_hand, hands[0])
        self.assertEqual(
            fallback.selection_decision, HandSelectionDecision.PREFERRED_FALLBACK
        )

    def test_sticky_selection_survives_confidence_and_order_changes(self) -> None:
        pipeline = MagicMock()
        engine = MagicMock()
        engine.process.side_effect = lambda item, timestamp=None: make_engine_result(
            item
        )
        left = make_hand(0, "Left", 0.9)
        right = make_hand(1, "Right", 0.8)
        pipeline.process.return_value = GesturePipelineResult(object(), (left, right))
        runtime = GestureRuntime(
            pipeline,
            engine,
            GestureRuntimeConfig(
                selection_policy=HandSelectionPolicy.HIGHEST_CONFIDENCE
            ),
        )
        runtime.process(object())
        left_changed = make_hand(0, "LEFT", 0.1)
        right_changed = make_hand(1, "Right", 0.99)
        pipeline.process.return_value = GesturePipelineResult(
            object(), (right_changed, left_changed)
        )

        result = runtime.process(object())

        self.assertIs(result.selected_hand, left_changed)
        self.assertEqual(
            result.selection_decision, HandSelectionDecision.STICKY_RETAINED
        )
        engine.reset.assert_not_called()


class NeutralIdentityTests(SimpleTestCase):
    def setUp(self) -> None:
        self.pipeline = MagicMock()
        self.engine = MagicMock()
        self.hand = make_hand(0, "Right", 0.9)
        self.pipeline.process.return_value = GesturePipelineResult(
            object(), (self.hand,)
        )

        def normal(item, *, timestamp=None):
            return make_engine_result(
                item,
                active_label=GestureLabel.POINT,
                timestamp=timestamp or 0,
            )

        self.engine.process.side_effect = normal
        self.runtime = GestureRuntime(self.pipeline, self.engine)
        self.runtime.process(object(), timestamp=0)

    def test_incomplete_release_retains_identity_and_same_hand_does_not_reset(
        self,
    ) -> None:
        def partial(item, *, timestamp=None):
            return make_engine_result(
                item,
                decision=GestureEngineDecision.RELEASE_ACCUMULATING,
                active_label=GestureLabel.POINT,
            )

        self.engine.process.side_effect = partial
        self.pipeline.process.return_value = GesturePipelineResult(object(), ())
        neutral = self.runtime.process(object(), timestamp=1)
        self.pipeline.process.return_value = GesturePipelineResult(
            object(), (self.hand,)
        )
        returned = self.runtime.process(object(), timestamp=2)

        self.assertEqual(neutral.selected_identity, SelectedHandIdentity(0, "right"))
        self.assertEqual(
            returned.selection_decision, HandSelectionDecision.STICKY_RETAINED
        )
        self.engine.reset.assert_not_called()

    def test_full_release_clears_identity(self) -> None:
        self.engine.process.side_effect = lambda item, timestamp=None: (
            make_engine_result(
                item,
                decision=GestureEngineDecision.RELEASED,
                active_label=None,
            )
        )
        self.pipeline.process.return_value = GesturePipelineResult(object(), ())

        result = self.runtime.process(object(), timestamp=1)

        self.assertIsNone(result.selected_identity)

    def test_different_hand_before_release_forces_reset(self) -> None:
        other = make_hand(1, "Left", 0.8)
        self.pipeline.process.return_value = GesturePipelineResult(object(), (other,))

        result = self.runtime.process(object(), timestamp=1)

        self.assertEqual(result.selection_decision, HandSelectionDecision.HAND_SWITCHED)
        self.engine.reset.assert_called_once_with()


class RuntimeErrorAndLifecycleTests(SimpleTestCase):
    def test_pipeline_failure_is_chained_and_does_not_call_engine(self) -> None:
        pipeline = MagicMock()
        engine = MagicMock()
        original = GesturePipelineError(MagicMock(), "processor failed")
        pipeline.process.side_effect = original

        with self.assertRaises(GestureRuntimeError) as caught:
            GestureRuntime(pipeline, engine).process(object())

        self.assertEqual(
            caught.exception.stage, GestureRuntimeStage.PIPELINE_PROCESSING
        )
        self.assertIs(caught.exception.__cause__, original)
        engine.process.assert_not_called()

    def test_observation_failure_is_chained_and_does_not_call_engine(self) -> None:
        pipeline = MagicMock()
        engine = MagicMock()
        bad_hand = make_hand(0, "Right", 0.9)
        object.__setattr__(bad_hand, "confidence", 2.0)
        pipeline.process.return_value = GesturePipelineResult(object(), (bad_hand,))

        with self.assertRaises(GestureRuntimeError) as caught:
            GestureRuntime(pipeline, engine).process(object())

        self.assertEqual(
            caught.exception.stage, GestureRuntimeStage.OBSERVATION_ADAPTATION
        )
        self.assertIsInstance(caught.exception.__cause__, GestureEngineError)
        engine.process.assert_not_called()

    def test_engine_failure_is_chained_with_hand_context(self) -> None:
        pipeline = MagicMock()
        engine = MagicMock()
        pipeline.process.return_value = GesturePipelineResult(
            object(), (make_hand(3, "Left", 0.9),)
        )
        original = GestureEngineError("timestamp failed")
        engine.process.side_effect = original

        with self.assertRaises(GestureRuntimeError) as caught:
            GestureRuntime(pipeline, engine).process(object())

        self.assertEqual(caught.exception.stage, GestureRuntimeStage.ENGINE_PROCESSING)
        self.assertEqual(caught.exception.identity, SelectedHandIdentity(3, "left"))
        self.assertIs(caught.exception.__cause__, original)

    def test_injected_dependencies_are_not_closed_and_closed_runtime_rejects_use(
        self,
    ) -> None:
        pipeline = MagicMock()
        engine = MagicMock()
        runtime = GestureRuntime(pipeline, engine)
        runtime.close()
        runtime.close()

        pipeline.close.assert_not_called()
        engine.close.assert_not_called()
        with self.assertRaises(GestureRuntimeError):
            runtime.process(object())
        with self.assertRaises(GestureRuntimeError):
            runtime.reset()

    def test_owned_dependencies_close_once_in_reverse_coordination_order(self) -> None:
        events = []
        pipeline = MagicMock()
        engine = MagicMock()
        engine.close.side_effect = lambda: events.append("engine")
        pipeline.close.side_effect = lambda: events.append("pipeline")
        with (
            patch(
                "gestureboard.services.gesture_runtime.GesturePipeline",
                return_value=pipeline,
            ),
            patch(
                "gestureboard.services.gesture_runtime.GestureEngine",
                return_value=engine,
            ),
        ):
            with GestureRuntime() as runtime:
                pass
            runtime.close()

        self.assertEqual(events, ["engine", "pipeline"])
        engine.close.assert_called_once_with()
        pipeline.close.assert_called_once_with()

    def test_all_owned_close_attempts_occur_when_engine_close_fails(self) -> None:
        pipeline = MagicMock()
        engine = MagicMock()
        original = GestureEngineError("close failed")
        engine.close.side_effect = original
        with (
            patch(
                "gestureboard.services.gesture_runtime.GesturePipeline",
                return_value=pipeline,
            ),
            patch(
                "gestureboard.services.gesture_runtime.GestureEngine",
                return_value=engine,
            ),
        ):
            runtime = GestureRuntime()

        with self.assertRaises(GestureRuntimeError) as caught:
            runtime.close()

        pipeline.close.assert_called_once_with()
        self.assertIs(caught.exception.__cause__, original)


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[GesturePrediction] = []

    def dispatch(self, item: GesturePrediction) -> DispatchResult:
        self.calls.append(item)
        action = KeyboardAction.tap("a")
        return DispatchResult(item.label, action, KeyboardExecutionResult(action))


class RealEngineRuntimeIntegrationTests(SimpleTestCase):
    def test_no_hand_frames_release_and_rearm_the_same_gesture(self) -> None:
        pipeline = MagicMock()
        dispatcher = FakeDispatcher()
        engine = GestureEngine(
            dispatcher,
            GestureEngineConfig(
                activation_frames=1,
                release_frames=2,
                cooldown_seconds=0,
            ),
        )
        runtime = GestureRuntime(pipeline, engine)
        hand = make_hand(0, "Right", 0.95)
        present = GesturePipelineResult(object(), (hand,))
        absent = GesturePipelineResult(object(), ())

        pipeline.process.side_effect = [
            present,
            absent,
            present,
            absent,
            absent,
            present,
        ]
        activated = runtime.process(object(), timestamp=0)
        partial = runtime.process(object(), timestamp=1)
        held = runtime.process(object(), timestamp=2)
        runtime.process(object(), timestamp=3)
        released = runtime.process(object(), timestamp=4)
        reactivated = runtime.process(object(), timestamp=5)

        self.assertEqual(
            activated.engine_result.decision, GestureEngineDecision.DISPATCHED
        )
        self.assertEqual(
            partial.engine_result.decision,
            GestureEngineDecision.RELEASE_ACCUMULATING,
        )
        self.assertEqual(
            held.engine_result.decision, GestureEngineDecision.HELD_SUPPRESSED
        )
        self.assertEqual(
            released.engine_result.decision, GestureEngineDecision.RELEASED
        )
        self.assertEqual(
            reactivated.engine_result.decision, GestureEngineDecision.DISPATCHED
        )
        self.assertEqual(len(dispatcher.calls), 2)
