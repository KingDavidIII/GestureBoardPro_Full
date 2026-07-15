"""Immutable adaptation of MediaPipe-style hand result containers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from math import hypot, isfinite
from statistics import median


class Handedness(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Landmark3D:
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        for value in (self.x, self.y, self.z):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not isfinite(value)
            ):
                raise ValueError("landmark coordinates must be finite.")


@dataclass(frozen=True, slots=True)
class HandObservation:
    landmarks: tuple[Landmark3D, ...]
    source_index: int
    handedness: Handedness
    handedness_confidence: float
    detection_confidence: float | None
    palm_scale: float
    palm_area: float
    frame_sequence: int | None = None
    timestamp_ms: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.landmarks, tuple) or not all(
            isinstance(point, Landmark3D) for point in self.landmarks
        ):
            raise ValueError("landmarks must be an immutable Landmark3D tuple.")
        if len(self.landmarks) != 21:
            raise ValueError("a hand requires exactly 21 landmarks.")
        if (
            isinstance(self.source_index, bool)
            or not isinstance(self.source_index, int)
            or self.source_index < 0
        ):
            raise ValueError("source_index must be a non-negative integer.")
        if not isinstance(self.handedness, Handedness):
            raise ValueError("handedness must be a Handedness value.")
        for value in (self.handedness_confidence, self.detection_confidence):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("confidence must be in [0, 1].")
        if (
            isinstance(self.palm_scale, bool)
            or not isinstance(self.palm_scale, (int, float))
            or not isfinite(self.palm_scale)
            or self.palm_scale <= 0
        ):
            raise ValueError("palm_scale must be positive.")
        if (
            isinstance(self.palm_area, bool)
            or not isinstance(self.palm_area, (int, float))
            or not isfinite(self.palm_area)
            or self.palm_area < 0
        ):
            raise ValueError("palm_area must be non-negative.")
        if self.frame_sequence is not None and (
            isinstance(self.frame_sequence, bool)
            or not isinstance(self.frame_sequence, int)
            or self.frame_sequence < 0
        ):
            raise ValueError("frame_sequence must be a non-negative integer or None.")
        if self.timestamp_ms is not None and (
            not isinstance(self.timestamp_ms, (int, float))
            or isinstance(self.timestamp_ms, bool)
            or not isfinite(self.timestamp_ms)
            or self.timestamp_ms < 0
        ):
            raise ValueError("timestamp_ms must be finite and non-negative or None.")


@dataclass(frozen=True, slots=True)
class HandSelection:
    valid_hand_count: int
    primary_hand: HandObservation | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.valid_hand_count, bool)
            or not isinstance(self.valid_hand_count, int)
            or self.valid_hand_count < 0
        ):
            raise ValueError("valid_hand_count must be a non-negative integer.")
        if self.primary_hand is not None and not isinstance(
            self.primary_hand, HandObservation
        ):
            raise ValueError("primary_hand must be a HandObservation or None.")
        if self.valid_hand_count == 0 and self.primary_hand is not None:
            raise ValueError("an empty selection cannot have a primary hand.")


def adapt_hands(result: object) -> tuple[HandObservation, ...]:
    hands = getattr(result, "multi_hand_landmarks", None) or ()
    handedness = getattr(result, "multi_handedness", None) or ()
    observations: list[HandObservation] = []
    for index, hand in enumerate(hands):
        try:
            raw_points = getattr(hand, "landmark", hand)
            points = tuple(
                Landmark3D(point.x, point.y, point.z) for point in raw_points
            )
            label, confidence = _handedness(
                handedness[index] if index < len(handedness) else None
            )
            scale = median(
                hypot(points[0].x - points[i].x, points[0].y - points[i].y)
                for i in (5, 9, 13, 17)
            )
            area = (
                abs(
                    sum(
                        points[i].x * points[j].y - points[j].x * points[i].y
                        for i, j in ((0, 5), (5, 9), (9, 13), (13, 17), (17, 0))
                    )
                )
                / 2
            )
            observations.append(
                HandObservation(points, index, label, confidence, None, scale, area)
            )
        except (AttributeError, IndexError, TypeError, ValueError):
            continue
    return tuple(observations)


def select_primary(hands: Iterable[HandObservation]) -> HandSelection:
    valid = tuple(hands)
    primary = max(
        valid,
        key=lambda hand: (
            (
                hand.detection_confidence
                if hand.detection_confidence is not None
                else -1.0
            ),
            hand.handedness_confidence,
            hand.palm_area,
            -hand.source_index,
        ),
        default=None,
    )
    return HandSelection(len(valid), primary)


def _handedness(value: object) -> tuple[Handedness, float]:
    classification = getattr(value, "classification", ()) if value is not None else ()
    item = (
        classification[0]
        if classification
        else (value[0] if isinstance(value, (list, tuple)) and value else None)
    )
    label = str(
        getattr(item, "label", getattr(item, "category_name", "unknown"))
    ).lower()
    confidence = getattr(item, "score", 0.0)
    return (
        (
            Handedness(label)
            if label in {"left", "right", "unknown"}
            else Handedness.UNKNOWN
        ),
        (
            float(confidence)
            if isinstance(confidence, (int, float))
            and isfinite(confidence)
            and 0 <= confidence <= 1
            else 0.0
        ),
    )
