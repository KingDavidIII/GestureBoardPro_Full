"""Single-call MediaPipe GestureRecognizer VIDEO task integration."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import mediapipe as mp
import numpy as np

from .geometry import extract_features
from .models import GestureCandidate, GestureId
from .observations import HandObservation, HandSelection


class MediaPipeGestureTaskError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GestureTaskResult:
    gestures: object
    handedness: object
    hand_landmarks: object
    hand_world_landmarks: object
    selection: HandSelection | None = None

    @property
    def multi_hand_landmarks(self) -> object:
        return self.hand_landmarks

    @property
    def multi_handedness(self) -> object:
        return self.handedness

    def validate(self) -> None:
        hands = tuple(self.hand_landmarks or ())
        handedness = tuple(self.handedness or ())
        gestures = tuple(self.gestures or ())
        worlds = tuple(self.hand_world_landmarks or ())
        if not hands:
            return
        if len(handedness) != len(hands):
            raise MediaPipeGestureTaskError(
                "Task handedness and landmark arrays must be aligned."
            )
        if gestures and len(gestures) != len(hands):
            raise MediaPipeGestureTaskError(
                "Task gesture and landmark arrays must be aligned."
            )
        if worlds and len(worlds) != len(hands):
            raise MediaPipeGestureTaskError(
                "Task world-landmark and landmark arrays must be aligned."
            )
        for landmarks in hands:
            if len(landmarks) != 21 or not all(
                isfinite(point.x) and isfinite(point.y) and isfinite(point.z)
                for point in landmarks
            ):
                raise MediaPipeGestureTaskError(
                    "Task image landmarks must be 21 finite values."
                )


class MediaPipeGestureTaskEngine:
    def __init__(self, model_path: str | Path, *, num_hands: int = 2) -> None:
        path = Path(model_path).expanduser().resolve()
        if path.suffix != ".task" or not path.is_file():
            raise MediaPipeGestureTaskError(
                "GestureRecognizer model must be a regular .task file."
            )
        try:
            base = mp.tasks.BaseOptions(model_asset_path=str(path))
            options = mp.tasks.vision.GestureRecognizerOptions(
                base_options=base,
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_hands=num_hands,
                min_hand_detection_confidence=0.5,
                min_hand_presence_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._recognizer = mp.tasks.vision.GestureRecognizer.create_from_options(
                options
            )
        except Exception as error:
            raise MediaPipeGestureTaskError(
                "GestureRecognizer model could not be initialised."
            ) from error
        self._last_timestamp = -1
        self._closed = False

    def recognize(self, rgb_frame: np.ndarray, timestamp_ms: int) -> GestureTaskResult:
        if self._closed:
            raise MediaPipeGestureTaskError("GestureRecognizer has been closed.")
        if (
            isinstance(timestamp_ms, bool)
            or not isinstance(timestamp_ms, int)
            or timestamp_ms <= self._last_timestamp
        ):
            raise MediaPipeGestureTaskError(
                "GestureRecognizer timestamps must be strictly increasing integers."
            )
        if (
            not isinstance(rgb_frame, np.ndarray)
            or rgb_frame.ndim != 3
            or rgb_frame.shape[2] != 3
        ):
            raise MediaPipeGestureTaskError(
                "Expected an RGB image with three channels."
            )
        result = self._recognizer.recognize_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame), timestamp_ms
        )
        self._last_timestamp = timestamp_ms
        adapted = GestureTaskResult(
            result.gestures,
            result.handedness,
            result.hand_landmarks,
            getattr(result, "hand_world_landmarks", ()),
        )
        adapted.validate()
        return adapted

    def close(self) -> None:
        if not self._closed:
            self._recognizer.close()
            self._closed = True


@dataclass(frozen=True, slots=True)
class CannedGesturePolicy:
    canned_minimum_confidence: float = 0.65
    pinch_max_thumb_index_distance: float = 0.20
    pinch_max_isolation_ratio: float = 0.40

    def __post_init__(self) -> None:
        for name in (
            "canned_minimum_confidence",
            "pinch_max_thumb_index_distance",
            "pinch_max_isolation_ratio",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative.")
        if self.canned_minimum_confidence > 1:
            raise ValueError("canned_minimum_confidence must be at most one.")


def classify_task_hand(
    result: GestureTaskResult,
    hand: HandObservation,
    policy: CannedGesturePolicy | None = None,
) -> GestureCandidate:
    """Prefer supported canned gestures, then isolated thumb-index contact."""
    policy = policy or CannedGesturePolicy()
    categories = (
        result.gestures[hand.source_index]
        if isinstance(result.gestures, (list, tuple))
        and hand.source_index < len(result.gestures)
        else ()
    )
    top = _top_category(categories)
    name = getattr(top, "category_name", None) if top is not None else None
    score = getattr(top, "score", 0.0) if top is not None else 0.0
    confident_category = (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and isfinite(score)
        and score >= policy.canned_minimum_confidence
    )
    if confident_category:
        mapping = {
            "Closed_Fist": GestureId.CLOSED_FIST,
            "Open_Palm": GestureId.OPEN_PALM,
            "Pointing_Up": GestureId.POINT,
        }
        if name in mapping:
            return GestureCandidate(
                mapping[name],
                float(score),
                f"mediapipe_{mapping[name].value}",
                hand.handedness.value,
                True,
            )
    try:
        features = extract_features(hand)
    except (IndexError, TypeError, ValueError):
        return GestureCandidate(
            GestureId.UNKNOWN,
            0.0,
            "invalid_pinch_geometry",
            hand.handedness.value,
            False,
        )
    denominator = features.nearest_non_index_thumb_distance
    if (
        all(
            isfinite(value)
            for value in (
                features.thumb_index_distance,
                denominator,
                features.pinch_isolation_ratio,
            )
        )
        and denominator > 1e-12
        and features.thumb_index_distance <= policy.pinch_max_thumb_index_distance
        and features.pinch_isolation_ratio <= policy.pinch_max_isolation_ratio
    ):
        return GestureCandidate(
            GestureId.PINCH,
            0.85,
            "isolated_thumb_index_contact",
            hand.handedness.value,
            True,
        )
    if confident_category and name is not None:
        return GestureCandidate(
            GestureId.UNKNOWN,
            0.0,
            "unsupported_canned_gesture",
            hand.handedness.value,
            False,
        )
    return GestureCandidate(
        GestureId.UNKNOWN, 0.0, "no_supported_gesture", hand.handedness.value, False
    )


def _top_category(categories: object) -> object | None:
    """Trust neither list order nor malformed category values; choose highest finite score."""
    if not isinstance(categories, (list, tuple)):
        return None
    usable = [
        category
        for category in categories
        if isinstance(getattr(category, "category_name", None), str)
        and bool(getattr(category, "category_name", "").strip())
        and isinstance(getattr(category, "score", None), (int, float))
        and not isinstance(getattr(category, "score", None), bool)
        and isfinite(category.score)
    ]
    return max(usable, key=lambda category: float(category.score), default=None)
