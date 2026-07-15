from __future__ import annotations

import os
from math import inf, nan
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from django.test import SimpleTestCase
from mediapipe.framework.formats import landmark_pb2

from gestureboard.recognition.observations import adapt_hands, select_primary
from gestureboard.recognition.service import RecognitionService
from gestureboard.recognition.task_engine import (
    GestureTaskResult,
    MediaPipeGestureTaskError,
)
from gestureboard.services.landmark_processor import (
    LandmarkProcessor,
    LandmarkProcessorError,
)


class LandmarkProcessorTests(SimpleTestCase):
    def setUp(self) -> None:
        self.hands_api = MagicMock()
        self.hands_api.HAND_CONNECTIONS = object()
        self.hands_instance = self.hands_api.Hands.return_value
        self.draw_api = MagicMock()

        media_pipe = SimpleNamespace(
            solutions=SimpleNamespace(
                hands=self.hands_api,
                drawing_utils=self.draw_api,
                drawing_styles=MagicMock(),
            )
        )
        self.mp_patch = patch("gestureboard.services.landmark_processor.mp", media_pipe)
        self.mp_patch.start()
        self.addCleanup(self.mp_patch.stop)

    def test_process_returns_annotated_frame_and_hand_data(self) -> None:
        landmarks = [SimpleNamespace(x=0.1, y=0.2, z=-0.3)]
        classification = SimpleNamespace(label="Right", score=0.95)
        self.hands_instance.process.return_value = SimpleNamespace(
            multi_hand_landmarks=[SimpleNamespace(landmark=landmarks)],
            multi_handedness=[SimpleNamespace(classification=[classification])],
        )
        frame = np.zeros((20, 30, 3), dtype=np.uint8)

        processor = LandmarkProcessor(task_engine_factory=self._task_failure)
        annotated, hands = processor.process(frame)

        self.assertIsNot(annotated, frame)
        self.assertEqual(len(hands), 1)
        self.assertEqual(hands[0].landmarks, landmarks)
        self.assertEqual(hands[0].handedness, "Right")
        self.assertEqual(hands[0].confidence, 0.95)
        self.hands_instance.process.assert_called_once()

    def test_process_without_detections_returns_an_empty_list(self) -> None:
        self.hands_instance.process.return_value = SimpleNamespace(
            multi_hand_landmarks=None, multi_handedness=None
        )
        processor = LandmarkProcessor(task_engine_factory=self._task_failure)

        _, hands = processor.process(np.zeros((10, 10, 3), dtype=np.uint8))

        self.assertEqual(hands, [])

    def test_close_is_idempotent_and_prevents_more_processing(self) -> None:
        processor = LandmarkProcessor(task_engine_factory=self._task_failure)

        processor.close()
        processor.close()

        self.hands_instance.close.assert_called_once()
        with self.assertRaises(LandmarkProcessorError):
            processor.process(np.zeros((10, 10, 3), dtype=np.uint8))

    def test_landmark_xy_converts_normalized_coordinates(self) -> None:
        point = SimpleNamespace(x=0.5, y=0.25)

        self.assertEqual(LandmarkProcessor.landmark_xy(point, 640, 480), (320, 120))

    @staticmethod
    def _task_failure(*_args: object) -> object:
        raise MediaPipeGestureTaskError("fixture")

    def test_task_engine_branch_uses_one_rgb_call_without_legacy_hands(self) -> None:
        landmarks = self._task_landmarks(marker=1)
        category = SimpleNamespace(category_name="Right", score=0.9)
        task = MagicMock()
        task.recognize.return_value = SimpleNamespace(
            multi_hand_landmarks=[landmarks], multi_handedness=[[category]]
        )
        processor = LandmarkProcessor(task_engine_factory=lambda *_args: task)
        _, hands = processor.process(np.zeros((10, 10, 3), dtype=np.uint8))
        self.assertEqual(len(hands), 1)
        task.recognize.assert_called_once()
        self.hands_api.Hands.assert_not_called()
        processor.close()
        processor.close()
        task.close.assert_called_once()

    def test_task_landmarks_are_converted_to_renderer_protobuf_without_optional_values(
        self,
    ) -> None:
        landmarks = [
            SimpleNamespace(x=0.125, y=0.25, z=-0.5),
            SimpleNamespace(x=0.75, y=0.5, z=0.25),
        ]

        converted = LandmarkProcessor._task_landmarks_for_drawing(landmarks)

        self.assertIsInstance(converted, landmark_pb2.NormalizedLandmarkList)
        self.assertEqual(len(converted.landmark), 2)
        self.assertEqual(
            [(point.x, point.y, point.z) for point in converted.landmark],
            [(0.125, 0.25, -0.5), (0.75, 0.5, 0.25)],
        )
        for point in converted.landmark:
            self.assertFalse(point.HasField("visibility"))
            self.assertFalse(point.HasField("presence"))

    def test_task_drawing_adapter_preserves_finite_optional_values_only(self) -> None:
        landmarks = [
            SimpleNamespace(x=0.1, y=0.2, z=0.3, visibility=0.8, presence=0.9),
            SimpleNamespace(x=0.4, y=0.5, z=0.6, visibility=nan, presence=inf),
        ]

        converted = LandmarkProcessor._task_landmarks_for_drawing(landmarks)

        self.assertTrue(converted.landmark[0].HasField("visibility"))
        self.assertTrue(converted.landmark[0].HasField("presence"))
        self.assertAlmostEqual(converted.landmark[0].visibility, 0.8)
        self.assertAlmostEqual(converted.landmark[0].presence, 0.9)
        self.assertFalse(converted.landmark[1].HasField("visibility"))
        self.assertFalse(converted.landmark[1].HasField("presence"))

    def test_real_drawing_utility_accepts_task_protobuf_landmarks(self) -> None:
        import mediapipe as actual_mediapipe

        converted = LandmarkProcessor._task_landmarks_for_drawing(
            self._task_landmarks(marker=1)
        )
        frame = np.zeros((32, 32, 3), dtype=np.uint8)

        actual_mediapipe.solutions.drawing_utils.draw_landmarks(
            frame,
            converted,
            actual_mediapipe.solutions.hands.HAND_CONNECTIONS,
        )

    def test_model_path_override_and_blank_override_use_expected_paths(self) -> None:
        task = MagicMock()
        seen: list[object] = []

        def factory(path: object, _count: object) -> MagicMock:
            seen.append(path)
            return task

        with patch.dict(
            os.environ, {"GESTURE_RECOGNIZER_MODEL_PATH": "C:/fixtures/custom.task"}
        ):
            LandmarkProcessor(task_engine_factory=factory)
        with patch.dict(os.environ, {"GESTURE_RECOGNIZER_MODEL_PATH": "   "}):
            LandmarkProcessor(task_engine_factory=factory)

        self.assertEqual(str(seen[0]).replace("\\", "/"), "C:/fixtures/custom.task")
        self.assertTrue(
            str(seen[1])
            .replace("\\", "/")
            .endswith("recognition/assets/gesture_recognizer.task")
        )

    def test_startup_task_failure_falls_back_once_and_is_not_retried_per_frame(
        self,
    ) -> None:
        attempts = MagicMock(side_effect=MediaPipeGestureTaskError("unavailable"))
        landmarks = [SimpleNamespace(x=0.1, y=0.2, z=0.0)]
        self.hands_instance.process.return_value = SimpleNamespace(
            multi_hand_landmarks=[SimpleNamespace(landmark=landmarks)],
            multi_handedness=[
                SimpleNamespace(
                    classification=[SimpleNamespace(label="Right", score=0.9)]
                )
            ],
        )
        with self.assertLogs(
            "gestureboard.services.landmark_processor", level="WARNING"
        ) as logs:
            processor = LandmarkProcessor(task_engine_factory=attempts)

        processor.process(np.zeros((10, 10, 3), dtype=np.uint8))
        processor.process(np.zeros((10, 10, 3), dtype=np.uint8))
        processor.close()
        processor.close()

        self.assertEqual(attempts.call_count, 1)
        self.assertEqual(self.hands_api.Hands.call_count, 1)
        self.assertEqual(self.hands_instance.process.call_count, 2)
        self.assertEqual(self.hands_instance.close.call_count, 1)
        self.assertEqual(len(logs.output), 1)

    def test_successful_task_engine_is_reused_and_never_switches_to_legacy(
        self,
    ) -> None:
        task = MagicMock()
        task.recognize.return_value = self._task_result(
            self._task_landmarks(marker=1), None, first_score=0.9, second_score=0.0
        )
        processor = LandmarkProcessor(task_engine_factory=lambda *_args: task)
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        processor.process(frame)
        processor.process(frame)
        processor.close()
        processor.close()

        self.assertEqual(task.recognize.call_count, 2)
        self.hands_api.Hands.assert_not_called()
        task.close.assert_called_once()

    def test_task_processing_failure_does_not_reconstruct_or_fall_back(self) -> None:
        task = MagicMock()
        task.recognize.side_effect = [
            RuntimeError("task frame failure"),
            self._task_result(
                self._task_landmarks(marker=1),
                None,
                first_score=0.9,
                second_score=0.0,
            ),
        ]
        factory = MagicMock(return_value=task)
        processor = LandmarkProcessor(task_engine_factory=factory)
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        with self.assertRaisesRegex(RuntimeError, "task frame failure"):
            processor.process(frame)
        _, hands = processor.process(frame)
        processor.close()

        factory.assert_called_once()
        self.assertEqual(task.recognize.call_count, 2)
        self.assertEqual(len(hands), 1)
        self.hands_api.Hands.assert_not_called()
        task.close.assert_called_once()

    def test_task_annotation_uses_only_the_same_selected_open_palm_hand(self) -> None:
        hand_a = self._task_landmarks(marker=1, pinch=False)
        hand_b = self._task_landmarks(marker=7, pinch=True)
        task_result = self._task_result(
            hand_a, hand_b, first_score=0.9, second_score=0.8
        )
        task = MagicMock()
        task.recognize.return_value = task_result
        processor = LandmarkProcessor(task_engine_factory=lambda *_args: task)

        with (
            patch(
                "gestureboard.services.landmark_processor.adapt_hands",
                wraps=adapt_hands,
            ) as adapt,
            patch(
                "gestureboard.services.landmark_processor.select_primary",
                wraps=select_primary,
            ) as select,
        ):
            _, detected = processor.process(np.zeros((10, 10, 3), dtype=np.uint8))
        with (
            patch(
                "gestureboard.recognition.service.adapt_hands", wraps=adapt_hands
            ) as recognition_adapt,
            patch(
                "gestureboard.recognition.service.select_primary", wraps=select_primary
            ) as recognition_select,
        ):
            recognition = RecognitionService().process(
                processor.last_mediapipe_result, frame_sequence=0, timestamp_ms=0
            )

        self.assertEqual(len(detected), 1)
        self.assertEqual(
            (
                recognition.primary_hand.source_index,
                recognition.candidate.gesture_id.value,
            ),
            (0, "open_palm"),
        )
        self.assertEqual(self._drawn_landmarks()[0].x, hand_a[0].x)
        self.assertNotEqual(self._drawn_landmarks()[0].x, hand_b[0].x)
        adapt.assert_called_once_with(task_result)
        select.assert_called_once()
        recognition_adapt.assert_not_called()
        recognition_select.assert_not_called()
        self.assertEqual(self.draw_api.draw_landmarks.call_count, 1)
        task.recognize.assert_called_once()
        self.hands_api.Hands.assert_not_called()

    def test_task_annotation_uses_only_the_same_selected_pinch_hand(self) -> None:
        hand_a = self._task_landmarks(marker=1, pinch=False)
        hand_b = self._task_landmarks(marker=7, pinch=True)
        task_result = self._task_result(
            hand_a, hand_b, first_score=0.8, second_score=0.9
        )
        task = MagicMock()
        task.recognize.return_value = task_result
        processor = LandmarkProcessor(task_engine_factory=lambda *_args: task)

        _, detected = processor.process(np.zeros((10, 10, 3), dtype=np.uint8))
        recognition = RecognitionService().process(
            processor.last_mediapipe_result, frame_sequence=0, timestamp_ms=0
        )

        self.assertEqual(len(detected), 1)
        self.assertEqual(
            (
                recognition.primary_hand.source_index,
                recognition.candidate.gesture_id.value,
            ),
            (1, "pinch"),
        )
        self.assertEqual(self._drawn_landmarks()[0].x, hand_b[0].x)
        self.assertNotEqual(self._drawn_landmarks()[0].x, hand_a[0].x)
        task.recognize.assert_called_once()
        self.hands_api.Hands.assert_not_called()

    def test_task_annotation_allows_missing_world_landmarks_and_clears_on_no_hand(
        self,
    ) -> None:
        hand_a = self._task_landmarks(marker=1, pinch=False)
        first = self._task_result(hand_a, None, first_score=0.9, second_score=0.0)
        second = SimpleNamespace(
            multi_hand_landmarks=[], multi_handedness=[], hand_world_landmarks=[]
        )
        task = MagicMock()
        task.recognize.side_effect = [first, second]
        processor = LandmarkProcessor(task_engine_factory=lambda *_args: task)
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        first_annotated, first_hands = processor.process(frame)
        second_annotated, second_hands = processor.process(frame)

        self.assertEqual(len(first_hands), 1)
        self.assertEqual(second_hands, [])
        self.assertEqual(self.draw_api.draw_landmarks.call_count, 1)
        self.assertIsNot(first_annotated, frame)
        self.assertIsNot(second_annotated, first_annotated)
        self.assertTrue(np.array_equal(second_annotated, frame))
        self.assertIsNone(
            RecognitionService()
            .process(second, frame_sequence=1, timestamp_ms=1)
            .candidate
        )
        self.hands_api.Hands.assert_not_called()

    def test_malformed_task_landmarks_never_reach_annotation_or_legacy_fallback(
        self,
    ) -> None:
        malformed = self._task_landmarks(marker=1)[:20]
        task = MagicMock()
        task_result = SimpleNamespace(
            multi_hand_landmarks=[malformed],
            multi_handedness=[[SimpleNamespace(category_name="Right", score=0.9)]],
        )
        task.recognize.return_value = task_result
        processor = LandmarkProcessor(task_engine_factory=lambda *_args: task)

        _, hands = processor.process(np.zeros((10, 10, 3), dtype=np.uint8))

        self.assertEqual(hands, [])
        self.draw_api.draw_landmarks.assert_not_called()
        self.assertIsNone(
            RecognitionService()
            .process(task_result, frame_sequence=0, timestamp_ms=0)
            .candidate
        )
        self.hands_api.Hands.assert_not_called()

    def test_legacy_protobuf_landmarks_are_drawn_without_conversion(self) -> None:
        legacy = landmark_pb2.NormalizedLandmarkList()
        legacy.landmark.add(x=0.1, y=0.2, z=0.3)
        classification = SimpleNamespace(label="Right", score=0.9)
        self.hands_instance.process.return_value = SimpleNamespace(
            multi_hand_landmarks=[legacy],
            multi_handedness=[SimpleNamespace(classification=[classification])],
        )
        processor = LandmarkProcessor(task_engine_factory=self._task_failure)

        processor.process(np.zeros((10, 10, 3), dtype=np.uint8))

        self.assertIs(self.draw_api.draw_landmarks.call_args.args[1], legacy)

    def test_task_annotation_error_is_wrapped_by_the_existing_pipeline_boundary(
        self,
    ) -> None:
        from gestureboard.services.gesture_pipeline import (
            GesturePipeline,
            GesturePipelineError,
            GesturePipelineStage,
        )

        task = MagicMock()
        task_result = self._task_result(
            self._task_landmarks(marker=1), None, first_score=0.9, second_score=0.0
        )
        task.recognize.side_effect = [task_result, task_result]
        self.draw_api.draw_landmarks.side_effect = [ValueError("renderer failed"), None]
        normalizer = MagicMock()
        classifier = MagicMock()
        pipeline = GesturePipeline(
            processor=LandmarkProcessor(task_engine_factory=lambda *_args: task),
            normalizer=normalizer,
            classifier=classifier,
        )

        with self.assertRaises(GesturePipelineError) as caught:
            pipeline.process(np.zeros((10, 10, 3), dtype=np.uint8))

        self.assertEqual(
            caught.exception.stage, GesturePipelineStage.LANDMARK_PROCESSING
        )
        recovered = pipeline.process(np.zeros((10, 10, 3), dtype=np.uint8))
        self.assertEqual(recovered.hand_count, 1)
        self.assertEqual(self.draw_api.draw_landmarks.call_count, 2)
        self.assertEqual(task.recognize.call_count, 2)
        self.hands_api.Hands.assert_not_called()

    def _drawn_landmarks(self) -> list[landmark_pb2.NormalizedLandmark]:
        return list(self.draw_api.draw_landmarks.call_args.args[1].landmark)

    @staticmethod
    def _task_landmarks(*, marker: float, pinch: bool = True) -> list[SimpleNamespace]:
        points = [SimpleNamespace(x=marker, y=0.0, z=0.0) for _ in range(21)]
        for index, x, y in ((5, 0, 1), (9, 0.5, 1), (13, 0.5, 2), (17, 0, 2)):
            points[index] = SimpleNamespace(x=marker + x, y=y, z=0.0)
        for index, x in (
            (4, 0),
            (8, 0.1 if pinch else 1),
            (12, 0.5),
            (16, 1),
            (20, 1.5),
        ):
            points[index] = SimpleNamespace(x=marker + x, y=0.0, z=0.0)
        return points

    @staticmethod
    def _task_result(
        first: list[SimpleNamespace],
        second: list[SimpleNamespace] | None,
        *,
        first_score: float,
        second_score: float,
    ) -> GestureTaskResult:
        hands = [first] if second is None else [first, second]
        handedness = [[SimpleNamespace(category_name="Right", score=first_score)]]
        gestures = [[SimpleNamespace(category_name="Open_Palm", score=0.9)]]
        if second is not None:
            handedness.append(
                [SimpleNamespace(category_name="Left", score=second_score)]
            )
            gestures.append([])
        return GestureTaskResult(gestures, handedness, hands, [])
