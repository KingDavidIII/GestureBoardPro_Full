"""Deterministic classification of normalized hand landmarks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite, sqrt
from typing import Any

LANDMARK_COUNT = 21


class GestureClassifierError(ValueError):
    """Raised when landmarks or classifier configuration are invalid."""


class GestureLabel(StrEnum):
    """Gestures supported by the Alpha 5 classifier."""

    UNKNOWN = "UNKNOWN"
    OPEN_PALM = "OPEN_PALM"
    FIST = "FIST"
    POINT = "POINT"
    PEACE = "PEACE"
    PINCH = "PINCH"


@dataclass(frozen=True, slots=True)
class GestureClassifierConfig:
    """Named geometric thresholds for deterministic classification."""

    extended_alignment: float = 0.75
    extended_distance_ratio: float = 1.12
    folded_alignment: float = 0.35
    folded_distance_ratio: float = 0.92
    pinch_distance: float = 0.35

    def __post_init__(self) -> None:
        values = (
            self.extended_alignment,
            self.extended_distance_ratio,
            self.folded_alignment,
            self.folded_distance_ratio,
            self.pinch_distance,
        )
        if not all(isfinite(value) for value in values):
            raise GestureClassifierError("Configuration values must be finite.")
        if not -1.0 <= self.folded_alignment < self.extended_alignment <= 1.0:
            raise GestureClassifierError(
                "Alignment thresholds must be ordered within [-1, 1]."
            )
        if not 0.0 < self.folded_distance_ratio < self.extended_distance_ratio:
            raise GestureClassifierError(
                "Distance ratios must be positive and ordered."
            )
        if self.pinch_distance <= 0.0:
            raise GestureClassifierError("pinch_distance must be greater than zero.")


@dataclass(frozen=True, slots=True)
class FingerState:
    """Geometric state of one finger."""

    extended: bool
    folded: bool
    alignment: float
    tip_distance: float


@dataclass(frozen=True, slots=True)
class GestureFeatures:
    """Reusable features extracted from one normalized hand."""

    thumb: FingerState
    index: FingerState
    middle: FingerState
    ring: FingerState
    little: FingerState
    thumb_index_distance: float
    fingertip_distances: tuple[float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class GesturePrediction:
    """Immutable classifier result with the features behind the decision."""

    label: GestureLabel
    features: GestureFeatures


Point = tuple[float, float, float]


class GestureClassifier:
    """Classify one normalized 21-landmark hand using explicit rules.

    Rule precedence is PINCH, PEACE, POINT, OPEN_PALM, FIST, then UNKNOWN.
    This order is part of the deterministic public behaviour.
    """

    _FINGERS = (
        (1, 2, 3, 4),
        (5, 6, 7, 8),
        (9, 10, 11, 12),
        (13, 14, 15, 16),
        (17, 18, 19, 20),
    )

    def __init__(self, config: GestureClassifierConfig | None = None) -> None:
        self.config = config or GestureClassifierConfig()

    def classify(self, landmarks: Sequence[Any]) -> GesturePrediction:
        """Return a deterministic prediction for normalized 3D landmarks."""

        points = self._validate(landmarks)
        features = self._extract_features(points)
        states = (
            features.thumb,
            features.index,
            features.middle,
            features.ring,
            features.little,
        )
        thumb, index, middle, ring, little = states

        if features.thumb_index_distance <= self.config.pinch_distance:
            label = GestureLabel.PINCH
        elif index.extended and middle.extended and ring.folded and little.folded:
            label = GestureLabel.PEACE
        elif index.extended and middle.folded and ring.folded and little.folded:
            label = GestureLabel.POINT
        elif all(state.extended for state in states):
            label = GestureLabel.OPEN_PALM
        elif all(state.folded for state in states):
            label = GestureLabel.FIST
        else:
            label = GestureLabel.UNKNOWN
        return GesturePrediction(label=label, features=features)

    def extract_features(self, landmarks: Sequence[Any]) -> GestureFeatures:
        """Extract finger states and normalized-space fingertip distances."""

        points = self._validate(landmarks)
        return self._extract_features(points)

    def _extract_features(self, points: tuple[Point, ...]) -> GestureFeatures:
        wrist = points[0]
        states = tuple(
            self._finger_state(points, wrist, indices) for indices in self._FINGERS
        )
        tip_indices = (4, 8, 12, 16, 20)
        tip_distances = tuple(
            self._distance(wrist, points[index]) for index in tip_indices
        )
        return GestureFeatures(
            thumb=states[0],
            index=states[1],
            middle=states[2],
            ring=states[3],
            little=states[4],
            thumb_index_distance=self._distance(points[4], points[8]),
            fingertip_distances=tip_distances,
        )

    def _finger_state(
        self, points: tuple[Point, ...], wrist: Point, indices: tuple[int, ...]
    ) -> FingerState:
        _, mcp_index, pip_index, tip_index = indices
        mcp, pip, tip = points[mcp_index], points[pip_index], points[tip_index]
        alignment = self._cosine(self._subtract(pip, mcp), self._subtract(tip, pip))
        tip_distance = self._distance(wrist, tip)
        pip_distance = self._distance(wrist, pip)
        ratio = tip_distance / pip_distance if pip_distance else 0.0
        return FingerState(
            extended=(
                alignment >= self.config.extended_alignment
                and ratio >= self.config.extended_distance_ratio
            ),
            folded=(
                alignment <= self.config.folded_alignment
                or ratio <= self.config.folded_distance_ratio
            ),
            alignment=alignment,
            tip_distance=tip_distance,
        )

    @staticmethod
    def _validate(landmarks: Sequence[Any]) -> tuple[Point, ...]:
        try:
            count = len(landmarks)
        except TypeError as error:
            raise GestureClassifierError(
                "Landmarks must be a sized sequence."
            ) from error
        if count != LANDMARK_COUNT:
            raise GestureClassifierError(
                f"Expected exactly {LANDMARK_COUNT} landmarks; received {count}."
            )
        points: list[Point] = []
        for landmark in landmarks:
            try:
                point = (float(landmark.x), float(landmark.y), float(landmark.z))
            except (AttributeError, TypeError, ValueError) as error:
                raise GestureClassifierError(
                    "Each landmark must provide numeric x, y, and z coordinates."
                ) from error
            if not all(isfinite(coordinate) for coordinate in point):
                raise GestureClassifierError("Landmark coordinates must be finite.")
            points.append(point)
        return tuple(points)

    @staticmethod
    def _subtract(left: Point, right: Point) -> Point:
        return tuple(a - b for a, b in zip(left, right, strict=True))  # type: ignore[return-value]

    @staticmethod
    def _distance(left: Point, right: Point) -> float:
        return sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))

    @staticmethod
    def _cosine(left: Point, right: Point) -> float:
        left_length = sqrt(sum(value * value for value in left))
        right_length = sqrt(sum(value * value for value in right))
        if left_length == 0.0 or right_length == 0.0:
            return -1.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (
            left_length * right_length
        )
