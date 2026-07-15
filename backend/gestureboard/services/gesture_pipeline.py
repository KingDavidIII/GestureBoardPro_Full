"""Synchronous orchestration of the gesture recognition services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType

import numpy as np

from .gesture_classifier import GestureClassifier, GesturePrediction
from .landmark_normalizer import LandmarkNormalizer, NormalizedLandmark
from .landmark_processor import LandmarkProcessor


class GesturePipelineStage(StrEnum):
    """Stages that can fail while processing a frame."""

    LANDMARK_PROCESSING = "landmark processing"
    LANDMARK_NORMALIZATION = "landmark normalization"
    GESTURE_CLASSIFICATION = "gesture classification"
    LIFECYCLE = "lifecycle"


class GesturePipelineError(RuntimeError):
    """Raised when a pipeline stage cannot complete."""

    def __init__(
        self,
        stage: GesturePipelineStage,
        message: str,
        *,
        hand_index: int | None = None,
    ) -> None:
        self.stage = stage
        self.hand_index = hand_index
        context = f" during hand {hand_index}" if hand_index is not None else ""
        super().__init__(f"{stage.value}{context}: {message}")


@dataclass(frozen=True, slots=True)
class HandGestureResult:
    """Recognition result for one hand, in processor output order."""

    hand_index: int
    handedness: str
    confidence: float
    normalized_landmarks: tuple[NormalizedLandmark, ...]
    prediction: GesturePrediction


@dataclass(frozen=True, slots=True)
class GesturePipelineResult:
    """Recognition results and annotated image for one frame."""

    annotated_frame: np.ndarray
    hands: tuple[HandGestureResult, ...]
    mediapipe_result: object | None = None

    @property
    def hand_count(self) -> int:
        """Return the number of successfully processed hands."""

        return len(self.hands)


class GesturePipeline:
    """Run processor, normalizer, and classifier synchronously for one frame."""

    def __init__(
        self,
        processor: LandmarkProcessor | None = None,
        normalizer: LandmarkNormalizer | None = None,
        classifier: GestureClassifier | None = None,
    ) -> None:
        self.processor = processor if processor is not None else LandmarkProcessor()
        self.normalizer = normalizer if normalizer is not None else LandmarkNormalizer()
        self.classifier = classifier if classifier is not None else GestureClassifier()
        self._owned_dependencies = tuple(
            dependency
            for dependency, injected in (
                (self.processor, processor),
                (self.normalizer, normalizer),
                (self.classifier, classifier),
            )
            if injected is None
        )
        self._closed = False

    def process(self, frame: np.ndarray) -> GesturePipelineResult:
        """Process one BGR frame and return ordered, structured hand results."""

        if self._closed:
            raise GesturePipelineError(
                GesturePipelineStage.LIFECYCLE,
                "cannot process a frame after the pipeline has been closed",
            )

        try:
            annotated_frame, detected_hands = self.processor.process(frame)
        except Exception as error:
            raise GesturePipelineError(
                GesturePipelineStage.LANDMARK_PROCESSING,
                str(error) or type(error).__name__,
            ) from error

        results: list[HandGestureResult] = []
        for hand_index, hand in enumerate(detected_hands):
            try:
                normalized = self.normalizer.normalize(hand.landmarks)
            except Exception as error:
                raise GesturePipelineError(
                    GesturePipelineStage.LANDMARK_NORMALIZATION,
                    str(error) or type(error).__name__,
                    hand_index=hand_index,
                ) from error

            try:
                prediction = self.classifier.classify(normalized)
            except Exception as error:
                raise GesturePipelineError(
                    GesturePipelineStage.GESTURE_CLASSIFICATION,
                    str(error) or type(error).__name__,
                    hand_index=hand_index,
                ) from error

            results.append(
                HandGestureResult(
                    hand_index=hand_index,
                    handedness=hand.handedness,
                    confidence=hand.confidence,
                    normalized_landmarks=normalized,
                    prediction=prediction,
                )
            )

        return GesturePipelineResult(
            annotated_frame=annotated_frame,
            hands=tuple(results),
            mediapipe_result=getattr(self.processor, "last_mediapipe_result", None),
        )

    def close(self) -> None:
        """Close pipeline-owned dependencies exactly once."""

        if self._closed:
            return
        for dependency in self._owned_dependencies:
            close = getattr(dependency, "close", None)
            if callable(close):
                close()
        self._closed = True

    def __enter__(self) -> GesturePipeline:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
