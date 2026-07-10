"""Tests for synchronous gesture pipeline orchestration."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import numpy as np
from django.test import SimpleTestCase

from gestureboard.services.gesture_pipeline import (
    GesturePipeline,
    GesturePipelineError,
    GesturePipelineStage,
)
from gestureboard.services.landmark_normalizer import NormalizedLandmark
from gestureboard.services.landmark_processor import HandData


def hand(name: str, confidence: float, token: object) -> HandData:
    return HandData(landmarks=[token], handedness=name, confidence=confidence)


class GesturePipelineTests(SimpleTestCase):
    def setUp(self) -> None:
        self.processor = MagicMock()
        self.normalizer = MagicMock()
        self.classifier = MagicMock()
        self.pipeline = GesturePipeline(
            processor=self.processor,
            normalizer=self.normalizer,
            classifier=self.classifier,
        )
        self.frame = np.zeros((8, 12, 3), dtype=np.uint8)
        self.annotated = np.ones_like(self.frame)

    def test_no_detected_hands_is_a_valid_immutable_result(self) -> None:
        self.processor.process.return_value = (self.annotated, [])

        result = self.pipeline.process(self.frame)

        self.assertIs(result.annotated_frame, self.annotated)
        self.assertEqual(result.hands, ())
        self.assertEqual(result.hand_count, 0)
        self.normalizer.normalize.assert_not_called()
        self.classifier.classify.assert_not_called()

    def test_one_hand_preserves_metadata_and_stage_outputs(self) -> None:
        raw_token = object()
        detected = hand("Right", 0.93, raw_token)
        normalized = tuple(
            NormalizedLandmark(float(index), 0.0, 0.0) for index in range(21)
        )
        prediction = SimpleNamespace(label="POINT")
        self.processor.process.return_value = (self.annotated, [detected])
        self.normalizer.normalize.return_value = normalized
        self.classifier.classify.return_value = prediction

        result = self.pipeline.process(self.frame)

        self.processor.process.assert_called_once_with(self.frame)
        self.normalizer.normalize.assert_called_once_with(detected.landmarks)
        self.classifier.classify.assert_called_once_with(normalized)
        self.assertEqual(result.hand_count, 1)
        self.assertEqual(result.hands[0].hand_index, 0)
        self.assertEqual(result.hands[0].handedness, "Right")
        self.assertEqual(result.hands[0].confidence, 0.93)
        self.assertIs(result.hands[0].normalized_landmarks, normalized)
        self.assertIs(result.hands[0].prediction, prediction)

    def test_multiple_hands_preserve_processor_order(self) -> None:
        first = hand("Left", 0.81, object())
        second = hand("Right", 0.97, object())
        first_normalized = (NormalizedLandmark(1.0, 0.0, 0.0),)
        second_normalized = (NormalizedLandmark(2.0, 0.0, 0.0),)
        first_prediction, second_prediction = object(), object()
        self.processor.process.return_value = (self.annotated, [first, second])
        self.normalizer.normalize.side_effect = [first_normalized, second_normalized]
        self.classifier.classify.side_effect = [first_prediction, second_prediction]

        result = self.pipeline.process(self.frame)

        self.assertEqual([item.hand_index for item in result.hands], [0, 1])
        self.assertEqual([item.handedness for item in result.hands], ["Left", "Right"])
        self.assertEqual([item.confidence for item in result.hands], [0.81, 0.97])
        self.assertEqual(
            self.normalizer.normalize.call_args_list,
            [call(first.landmarks), call(second.landmarks)],
        )
        self.assertEqual(
            self.classifier.classify.call_args_list,
            [call(first_normalized), call(second_normalized)],
        )
        self.assertIs(result.hands[0].prediction, first_prediction)
        self.assertIs(result.hands[1].prediction, second_prediction)

    def test_processor_failure_is_wrapped_with_cause(self) -> None:
        original = RuntimeError("MediaPipe unavailable")
        self.processor.process.side_effect = original

        with self.assertRaises(GesturePipelineError) as caught:
            self.pipeline.process(self.frame)

        self.assertEqual(
            caught.exception.stage, GesturePipelineStage.LANDMARK_PROCESSING
        )
        self.assertIsNone(caught.exception.hand_index)
        self.assertIn("MediaPipe unavailable", str(caught.exception))
        self.assertIs(caught.exception.__cause__, original)

    def test_normalizer_failure_is_wrapped_with_hand_index_and_cause(self) -> None:
        original = ValueError("scale is zero")
        self.processor.process.return_value = (
            self.annotated,
            [hand("Left", 0.8, object()), hand("Right", 0.9, object())],
        )
        self.normalizer.normalize.side_effect = [
            (NormalizedLandmark(0, 0, 0),),
            original,
        ]
        self.classifier.classify.return_value = object()

        with self.assertRaises(GesturePipelineError) as caught:
            self.pipeline.process(self.frame)

        self.assertEqual(
            caught.exception.stage, GesturePipelineStage.LANDMARK_NORMALIZATION
        )
        self.assertEqual(caught.exception.hand_index, 1)
        self.assertIn("hand 1", str(caught.exception))
        self.assertIs(caught.exception.__cause__, original)

    def test_classifier_failure_is_wrapped_with_hand_index_and_cause(self) -> None:
        original = ValueError("malformed normalized hand")
        self.processor.process.return_value = (
            self.annotated,
            [hand("Left", 0.8, object())],
        )
        self.normalizer.normalize.return_value = (NormalizedLandmark(0, 0, 0),)
        self.classifier.classify.side_effect = original

        with self.assertRaises(GesturePipelineError) as caught:
            self.pipeline.process(self.frame)

        self.assertEqual(
            caught.exception.stage, GesturePipelineStage.GESTURE_CLASSIFICATION
        )
        self.assertEqual(caught.exception.hand_index, 0)
        self.assertIn("malformed normalized hand", str(caught.exception))
        self.assertIs(caught.exception.__cause__, original)

    def test_context_manager_closes_owned_dependencies(self) -> None:
        owned_processor = MagicMock()
        owned_normalizer = MagicMock()
        owned_classifier = MagicMock()
        with (
            patch(
                "gestureboard.services.gesture_pipeline.LandmarkProcessor",
                return_value=owned_processor,
            ),
            patch(
                "gestureboard.services.gesture_pipeline.LandmarkNormalizer",
                return_value=owned_normalizer,
            ),
            patch(
                "gestureboard.services.gesture_pipeline.GestureClassifier",
                return_value=owned_classifier,
            ),
        ):
            with GesturePipeline() as pipeline:
                self.assertIs(pipeline.processor, owned_processor)

        owned_processor.close.assert_called_once_with()
        owned_normalizer.close.assert_called_once_with()
        owned_classifier.close.assert_called_once_with()

    def test_close_is_idempotent_for_owned_dependencies(self) -> None:
        owned_processor = MagicMock()
        with patch(
            "gestureboard.services.gesture_pipeline.LandmarkProcessor",
            return_value=owned_processor,
        ):
            pipeline = GesturePipeline(
                normalizer=self.normalizer,
                classifier=self.classifier,
            )
        pipeline.close()
        pipeline.close()

        owned_processor.close.assert_called_once_with()

    def test_injected_dependencies_are_never_closed(self) -> None:
        self.pipeline.close()
        self.pipeline.close()

        self.processor.close.assert_not_called()
        self.normalizer.close.assert_not_called()
        self.classifier.close.assert_not_called()

    def test_process_is_rejected_after_close(self) -> None:
        self.pipeline.close()

        with self.assertRaises(GesturePipelineError) as caught:
            self.pipeline.process(self.frame)

        self.assertEqual(caught.exception.stage, GesturePipelineStage.LIFECYCLE)
        self.assertIn("closed", str(caught.exception))
        self.processor.process.assert_not_called()
