"""
GestureBoard Pro
Landmark Processor

Processes camera frames using MediaPipe Hands.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from time import monotonic
from types import TracebackType
from typing import Any

import cv2
import mediapipe as mp
import numpy as np
from gestureboard.recognition.observations import adapt_hands, select_primary
from gestureboard.recognition.task_engine import (
    GestureTaskResult,
    MediaPipeGestureTaskEngine,
    MediaPipeGestureTaskError,
)
from mediapipe.framework.formats import landmark_pb2

logger = logging.getLogger(__name__)


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
        task_engine_factory: (
            Callable[[Path, int], MediaPipeGestureTaskEngine] | None
        ) = None,
        legacy_hands_factory: Callable[[], Any] | None = None,
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

        self._task_engine: MediaPipeGestureTaskEngine | None = None
        self._hands = None
        default_model_path = (
            Path(__file__).resolve().parents[1]
            / "recognition"
            / "assets"
            / "gesture_recognizer.task"
        )
        configured_model_path = os.environ.get(
            "GESTURE_RECOGNIZER_MODEL_PATH", ""
        ).strip()
        model_path = (
            Path(configured_model_path) if configured_model_path else default_model_path
        )
        task_factory = task_engine_factory or (
            lambda path, count: MediaPipeGestureTaskEngine(path, num_hands=count)
        )
        try:
            self._task_engine = task_factory(model_path, max_num_hands)
        except MediaPipeGestureTaskError as error:
            logger.warning(
                "Gesture task unavailable; using deterministic Hands fallback: %s",
                error,
            )
            self._hands = (
                legacy_hands_factory()
                if legacy_hands_factory
                else self._mp_hands.Hands(
                    static_image_mode=False,
                    max_num_hands=max_num_hands,
                    min_detection_confidence=min_detection_confidence,
                    min_tracking_confidence=min_tracking_confidence,
                )
            )
        self._task_timestamp_ms = -1
        self._last_mediapipe_result: Any | None = None
        self._closed = False

    @property
    def last_mediapipe_result(self) -> Any | None:
        """The current frame's result for immediate downstream consumers only."""
        return self._last_mediapipe_result

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

        if self._task_engine is not None:
            requested = int(monotonic() * 1000)
            self._task_timestamp_ms = max(requested, self._task_timestamp_ms + 1)
            results = self._task_engine.recognize(rgb, self._task_timestamp_ms)
        else:
            if self._hands is None:
                raise LandmarkProcessorError("No landmark engine is available.")
            results = self._hands.process(rgb)
        annotated = frame.copy()

        detected: list[HandData] = []

        hand_landmarks = getattr(results, "multi_hand_landmarks", None) or []
        handedness_data = getattr(results, "multi_handedness", None) or []

        if self._task_engine is not None:
            selection = select_primary(adapt_hands(results))
            if isinstance(results, GestureTaskResult):
                results = replace(results, selection=selection)
            self._last_mediapipe_result = results
            primary = selection.primary_hand
            if primary is not None:
                landmarks = hand_landmarks[primary.source_index]
                self._mp_draw.draw_landmarks(
                    annotated,
                    self._task_landmarks_for_drawing(landmarks),
                    self._mp_hands.HAND_CONNECTIONS,
                    self._mp_styles.get_default_hand_landmarks_style(),
                    self._mp_styles.get_default_hand_connections_style(),
                )
                detected.append(
                    HandData(
                        landmarks=list(primary.landmarks),
                        handedness=primary.handedness.value,
                        confidence=primary.handedness_confidence,
                    )
                )
        elif hand_landmarks and handedness_data:
            for landmarks, handedness in zip(
                hand_landmarks,
                handedness_data,
                strict=False,
            ):
                classifications = getattr(handedness, "classification", None)
                classification = (
                    classifications[0]
                    if classifications
                    else (handedness[0] if handedness else None)
                )
                if classification is None:
                    continue
                self._mp_draw.draw_landmarks(
                    annotated,
                    landmarks,
                    self._mp_hands.HAND_CONNECTIONS,
                    self._mp_styles.get_default_hand_landmarks_style(),
                    self._mp_styles.get_default_hand_connections_style(),
                )

                detected.append(
                    HandData(
                        landmarks=list(getattr(landmarks, "landmark", landmarks)),
                        handedness=classification.label,
                        confidence=float(classification.score),
                    )
                )

        if self._task_engine is None:
            self._last_mediapipe_result = results

        return annotated, detected

    @staticmethod
    def _task_landmarks_for_drawing(
        landmarks: object,
    ) -> landmark_pb2.NormalizedLandmarkList:
        """Adapt one selected Task hand for the legacy MediaPipe renderer."""

        drawing_landmarks = landmark_pb2.NormalizedLandmarkList()
        for landmark in landmarks:
            rendered = drawing_landmarks.landmark.add()
            rendered.x = float(landmark.x)
            rendered.y = float(landmark.y)
            rendered.z = float(landmark.z)
            for field in ("visibility", "presence"):
                value = getattr(landmark, field, None)
                if value is None:
                    continue
                numeric = float(value)
                if isfinite(numeric):
                    setattr(rendered, field, numeric)
        return drawing_landmarks

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
            if self._task_engine is not None:
                self._task_engine.close()
            elif self._hands is not None:
                self._hands.close()
            self._last_mediapipe_result = None
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
