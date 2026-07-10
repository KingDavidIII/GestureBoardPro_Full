"""Hand-relative landmark normalization for the gesture pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import sqrt
from typing import Any

WRIST_LANDMARK_INDEX = 0
MIDDLE_FINGER_MCP_LANDMARK_INDEX = 9


@dataclass(frozen=True, slots=True)
class NormalizedLandmark:
    """A translation- and scale-normalized 3D hand landmark."""

    x: float
    y: float
    z: float


class LandmarkNormalizationError(ValueError):
    """Raised when landmark data cannot be normalized reliably."""


class LandmarkNormalizer:
    """Convert MediaPipe landmarks to wrist-relative, scale-invariant points.

    The wrist is always translated to the origin.  The distance from the wrist
    to the middle-finger MCP joint is used as the scale reference, which makes
    coordinates comparable across different hand sizes and camera distances.
    Rotation is deliberately preserved for Alpha 5 feature extraction.
    """

    def __init__(
        self,
        origin_index: int = WRIST_LANDMARK_INDEX,
        scale_index: int = MIDDLE_FINGER_MCP_LANDMARK_INDEX,
        minimum_scale: float = 1e-6,
    ) -> None:
        if origin_index < 0 or scale_index < 0:
            raise ValueError("Landmark indices must be non-negative.")
        if origin_index == scale_index:
            raise ValueError("origin_index and scale_index must be different.")
        if minimum_scale <= 0:
            raise ValueError("minimum_scale must be greater than zero.")

        self.origin_index = origin_index
        self.scale_index = scale_index
        self.minimum_scale = minimum_scale

    def normalize(
        self,
        landmarks: Sequence[Any],
    ) -> tuple[NormalizedLandmark, ...]:
        """Normalize MediaPipe-style landmarks with ``x``, ``y``, and ``z``.

        The returned immutable tuple preserves the input landmark ordering.
        """

        required_index = max(self.origin_index, self.scale_index)
        if len(landmarks) <= required_index:
            raise LandmarkNormalizationError(
                f"Expected landmarks through index {required_index}."
            )

        points = tuple(self._coordinates(landmark) for landmark in landmarks)
        origin = points[self.origin_index]
        scale_point = points[self.scale_index]
        scale = self._distance(origin, scale_point)

        if scale < self.minimum_scale:
            raise LandmarkNormalizationError(
                "Scale reference points are too close to normalize the hand."
            )

        return tuple(
            NormalizedLandmark(
                x=(x - origin[0]) / scale,
                y=(y - origin[1]) / scale,
                z=(z - origin[2]) / scale,
            )
            for x, y, z in points
        )

    @staticmethod
    def _coordinates(landmark: Any) -> tuple[float, float, float]:
        try:
            return (
                float(landmark.x),
                float(landmark.y),
                float(landmark.z),
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise LandmarkNormalizationError(
                "Each landmark must provide numeric x, y, and z coordinates."
            ) from error

    @staticmethod
    def _distance(
        first: tuple[float, float, float],
        second: tuple[float, float, float],
    ) -> float:
        """Return the Euclidean distance between two 3D points."""

        return sqrt(
            sum((right - left) ** 2 for left, right in zip(first, second, strict=True))
        )
