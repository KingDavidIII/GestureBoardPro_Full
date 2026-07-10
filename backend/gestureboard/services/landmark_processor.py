"""
GestureBoard Pro
Landmark Processor

Processes camera frames using MediaPipe Hands.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any

import cv2
import mediapipe as mp
import numpy as np


@dataclass(slots=True)
class HandData:
    """
    Represents one detected hand.
    """

    landmarks: list[Any]
    handedness: str
    confidence: float


class LandmarkProcessorError(RuntimeError):
    """Raised when a frame cannot be processed for hand landmarks."""


class LandmarkProcessor:
    """
    Detects and draws hand landmarks.
    """

    def __init__(
        self,
        max_num_hands: int = 2,
        min_detection_confidence: float = 0.7,
        min_tracking_confidence: float = 0.7,
    ) -> None:
        if max_num_hands < 1:
            raise ValueError("max_num_hands must be at least 1.")
        if not 0.0 <= min_detection_confidence <= 1.0:
            raise ValueError("min_detection_confidence must be between 0 and 1.")
        if not 0.0 <= min_tracking_confidence <= 1.0:
            raise ValueError("min_tracking_confidence must be between 0 and 1.")

        self._mp_hands = mp.solutions.hands
        self._mp_draw = mp.solutions.drawing_utils
        self._mp_styles = mp.solutions.drawing_styles

        self._hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=max_num_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._closed = False

    def process(
        self,
        frame: np.ndarray,
    ) -> tuple[np.ndarray, list[HandData]]:
        """
        Process one camera frame.

        Returns
        -------
        annotated_frame
            Frame with landmarks drawn.

        hands
            List of detected hands.
        """

        if self._closed:
            raise LandmarkProcessorError("Landmark processor has been closed.")
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
            raise LandmarkProcessorError("Expected a BGR frame with three channels.")

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        results = self._hands.process(rgb)

        annotated = frame.copy()

        detected: list[HandData] = []

        hand_landmarks = getattr(results, "multi_hand_landmarks", None) or []
        handedness_data = getattr(results, "multi_handedness", None) or []

        if hand_landmarks and handedness_data:
            for landmarks, handedness in zip(
                hand_landmarks,
                handedness_data,
                strict=False,
            ):
                classifications = getattr(handedness, "classification", [])
                if not classifications:
                    continue

                classification = classifications[0]
                self._mp_draw.draw_landmarks(
                    annotated,
                    landmarks,
                    self._mp_hands.HAND_CONNECTIONS,
                    self._mp_styles.get_default_hand_landmarks_style(),
                    self._mp_styles.get_default_hand_connections_style(),
                )

                detected.append(
                    HandData(
                        landmarks=list(landmarks.landmark),
                        handedness=classification.label,
                        confidence=float(classification.score),
                    )
                )

        return annotated, detected

    @staticmethod
    def landmark_xy(
        landmark,
        width: int,
        height: int,
    ) -> tuple[int, int]:
        """
        Convert normalized coordinates to pixels.
        """

        return (int(landmark.x * width), int(landmark.y * height))

    def close(self) -> None:
        """
        Release MediaPipe resources.
        """

        if not self._closed:
            self._hands.close()
            self._closed = True

    def __enter__(self) -> LandmarkProcessor:
        """Return this processor for use as a context manager."""

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release MediaPipe resources when leaving a context manager."""

        self.close()
