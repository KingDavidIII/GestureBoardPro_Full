"""Scale-, translation-, and mirror-tolerant hand geometry."""

from __future__ import annotations

from dataclasses import dataclass
from math import acos, hypot, isfinite

from .observations import HandObservation, Landmark3D


@dataclass(frozen=True, slots=True)
class HandFeatures:
    """Scalar features only; never retain landmarks or third-party objects."""

    thumb_extended: bool
    index_extended: bool
    middle_extended: bool
    ring_extended: bool
    little_extended: bool
    thumb_index_distance: float
    extended_finger_count: int
    extended_non_thumb_count: int
    folded_finger_count: int
    thumb_extension_score: float = 0.0
    index_extension_score: float = 0.0
    middle_extension_score: float = 0.0
    ring_extension_score: float = 0.0
    little_extension_score: float = 0.0
    thumb_opposition: float = 0.0
    thumb_angle_degrees: float = 0.0
    index_angle_degrees: float = 0.0
    middle_angle_degrees: float = 0.0
    ring_angle_degrees: float = 0.0
    little_angle_degrees: float = 0.0
    thumb_tip_to_mcp_distance: float = 0.0
    index_tip_to_mcp_distance: float = 0.0
    middle_tip_to_mcp_distance: float = 0.0
    ring_tip_to_mcp_distance: float = 0.0
    little_tip_to_mcp_distance: float = 0.0
    palm_scale: float = 0.0
    thumb_pip_to_mcp_distance: float = 0.0
    index_pip_to_mcp_distance: float = 0.0
    middle_pip_to_mcp_distance: float = 0.0
    ring_pip_to_mcp_distance: float = 0.0
    little_pip_to_mcp_distance: float = 0.0
    thumb_middle_distance: float = 0.0
    thumb_ring_distance: float = 0.0
    thumb_little_distance: float = 0.0
    nearest_non_index_thumb_distance: float = 0.0
    pinch_isolation_ratio: float = 0.0
    index_pip_angle: float = 0.0
    index_dip_angle: float = 0.0

    def __post_init__(self) -> None:
        flags = (
            self.thumb_extended,
            self.index_extended,
            self.middle_extended,
            self.ring_extended,
            self.little_extended,
        )
        if not all(isinstance(flag, bool) for flag in flags):
            raise ValueError("extension flags must be boolean.")
        if (
            self.extended_finger_count != sum(flags)
            or self.extended_non_thumb_count != sum(flags[1:])
            or self.folded_finger_count != 5 - self.extended_finger_count
        ):
            raise ValueError("finger counts must agree with extension flags.")
        numeric = tuple(
            value
            for value in self.__dataclass_fields__
            if value
            not in {
                "thumb_extended",
                "index_extended",
                "middle_extended",
                "ring_extended",
                "little_extended",
                "extended_finger_count",
                "extended_non_thumb_count",
                "folded_finger_count",
            }
        )
        for name in numeric:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative.")
        for name in (
            "thumb_extension_score",
            "index_extension_score",
            "middle_extension_score",
            "ring_extension_score",
            "little_extension_score",
            "thumb_opposition",
        ):
            if getattr(self, name) > 1:
                raise ValueError(f"{name} must be at most one.")
        for name in (
            "thumb_angle_degrees",
            "index_angle_degrees",
            "middle_angle_degrees",
            "ring_angle_degrees",
            "little_angle_degrees",
        ):
            if getattr(self, name) > 180:
                raise ValueError(f"{name} must be no greater than 180 degrees.")

    @property
    def index_extension_margin(self) -> float:
        return self.index_tip_to_mcp_distance - self.index_pip_to_mcp_distance

    @property
    def middle_extension_margin(self) -> float:
        return self.middle_tip_to_mcp_distance - self.middle_pip_to_mcp_distance

    @property
    def ring_extension_margin(self) -> float:
        return self.ring_tip_to_mcp_distance - self.ring_pip_to_mcp_distance

    @property
    def little_extension_margin(self) -> float:
        return self.little_tip_to_mcp_distance - self.little_pip_to_mcp_distance


def _distance(first: Landmark3D, second: Landmark3D) -> float:
    return hypot(hypot(first.x - second.x, first.y - second.y), first.z - second.z)


def _joint_angle(mcp: Landmark3D, pip: Landmark3D, tip: Landmark3D) -> float:
    first = (mcp.x - pip.x, mcp.y - pip.y, mcp.z - pip.z)
    second = (tip.x - pip.x, tip.y - pip.y, tip.z - pip.z)
    first_length = hypot(hypot(*first[:2]), first[2])
    second_length = hypot(hypot(*second[:2]), second[2])
    if first_length == 0 or second_length == 0:
        return 0.0
    cosine = sum(a * b for a, b in zip(first, second, strict=True)) / (
        first_length * second_length
    )
    return acos(max(-1.0, min(1.0, cosine))) * 180.0 / 3.141592653589793


def extract_features(hand: HandObservation) -> HandFeatures:
    """Extract finite, normalised scalar features from an immutable observation."""
    if not isinstance(hand, HandObservation):
        raise ValueError("hand must be a HandObservation.")
    scale = hand.palm_scale
    if not isfinite(scale) or scale <= 0:
        raise ValueError("hand palm scale must be finite and positive.")
    points = hand.landmarks

    def feature(
        mcp: int, pip: int, tip: int
    ) -> tuple[bool, float, float, float, float]:
        tip_distance = _distance(points[tip], points[mcp]) / scale
        pip_distance = _distance(points[pip], points[mcp]) / scale
        # The score is deliberately bounded, while the distance retains its useful magnitude.
        score = min(1.0, tip_distance / max(pip_distance * 1.25, 1e-12))
        return (
            tip_distance > pip_distance,
            score,
            _joint_angle(points[mcp], points[pip], points[tip]),
            tip_distance,
            pip_distance,
        )

    thumb, thumb_score, thumb_angle, thumb_distance, thumb_pip_distance = feature(
        1, 3, 4
    )
    index, index_score, index_angle, index_distance, index_pip_distance = feature(
        5, 6, 8
    )
    middle, middle_score, middle_angle, middle_distance, middle_pip_distance = feature(
        9, 10, 12
    )
    ring, ring_score, ring_angle, ring_distance, ring_pip_distance = feature(13, 14, 16)
    little, little_score, little_angle, little_distance, little_pip_distance = feature(
        17, 18, 20
    )
    flags = (thumb, index, middle, ring, little)
    thumb_index_distance = _distance(points[4], points[8]) / scale
    thumb_middle_distance = _distance(points[4], points[12]) / scale
    thumb_ring_distance = _distance(points[4], points[16]) / scale
    thumb_little_distance = _distance(points[4], points[20]) / scale
    nearest_non_index_thumb_distance = min(
        thumb_middle_distance, thumb_ring_distance, thumb_little_distance
    )
    pinch_isolation_ratio = thumb_index_distance / max(
        nearest_non_index_thumb_distance, 1e-12
    )
    if not isfinite(pinch_isolation_ratio):
        pinch_isolation_ratio = 0.0
    return HandFeatures(
        *flags,
        thumb_index_distance,
        sum(flags),
        sum(flags[1:]),
        5 - sum(flags),
        thumb_score,
        index_score,
        middle_score,
        ring_score,
        little_score,
        min(1.0, thumb_index_distance),
        thumb_angle,
        index_angle,
        middle_angle,
        ring_angle,
        little_angle,
        thumb_distance,
        index_distance,
        middle_distance,
        ring_distance,
        little_distance,
        scale,
        thumb_pip_distance,
        index_pip_distance,
        middle_pip_distance,
        ring_pip_distance,
        little_pip_distance,
        thumb_middle_distance,
        thumb_ring_distance,
        thumb_little_distance,
        nearest_non_index_thumb_distance,
        pinch_isolation_ratio,
        _joint_angle(points[5], points[6], points[7]),
        _joint_angle(points[6], points[7], points[8]),
    )
