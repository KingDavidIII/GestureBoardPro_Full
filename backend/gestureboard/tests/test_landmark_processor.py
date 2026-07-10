from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from django.test import SimpleTestCase

from gestureboard.services.landmark_processor import (
    LandmarkProcessor,
    LandmarkProcessorError,
)


class LandmarkProcessorTests(SimpleTestCase):
    def setUp(self) -> None:
        self.hands_api = MagicMock()
        self.hands_api.HAND_CONNECTIONS = object()
        self.hands_instance = self.hands_api.Hands.return_value

        media_pipe = SimpleNamespace(
            solutions=SimpleNamespace(
                hands=self.hands_api,
                drawing_utils=MagicMock(),
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

        processor = LandmarkProcessor()
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
        processor = LandmarkProcessor()

        _, hands = processor.process(np.zeros((10, 10, 3), dtype=np.uint8))

        self.assertEqual(hands, [])

    def test_close_is_idempotent_and_prevents_more_processing(self) -> None:
        processor = LandmarkProcessor()

        processor.close()
        processor.close()

        self.hands_instance.close.assert_called_once()
        with self.assertRaises(LandmarkProcessorError):
            processor.process(np.zeros((10, 10, 3), dtype=np.uint8))

    def test_landmark_xy_converts_normalized_coordinates(self) -> None:
        point = SimpleNamespace(x=0.5, y=0.25)

        self.assertEqual(LandmarkProcessor.landmark_xy(point, 640, 480), (320, 120))
