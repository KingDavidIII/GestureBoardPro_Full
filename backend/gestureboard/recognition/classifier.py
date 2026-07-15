"""Centralised deterministic rule-based gesture classification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType

from .geometry import HandFeatures, extract_features
from .models import GestureCandidate, GestureId
from .observations import HandObservation


@dataclass(frozen=True, slots=True)
class FallbackClassificationDiagnostics:
    """Finite, coordinate-free diagnostics for the deterministic fallback only."""

    values: Mapping[str, float | int | bool | str]

    def __post_init__(self) -> None:
        safe: dict[str, float | int | bool | str] = {}
        for name, value in self.values.items():
            if not isinstance(name, str) or not name:
                raise ValueError("diagnostic names must be non-empty strings.")
            if isinstance(value, float) and not isfinite(value):
                raise ValueError("diagnostic floats must be finite.")
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError("diagnostics must contain only scalar values.")
            safe[name] = value
        object.__setattr__(self, "values", MappingProxyType(safe))

    def as_log_fields(self) -> Mapping[str, float | int | bool | str]:
        return self.values


def _probability(value: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{name} must be finite and in [0, 1].")
    return float(value)


@dataclass(frozen=True, slots=True)
class GestureClassifierPolicy:
    minimum_usable_hand_confidence: float = 0.5
    minimum_classification_confidence: float = 0.65
    pinch_distance_threshold: float = 0.45
    fist_maximum_extended_fingers: int = 1
    open_palm_minimum_extended_non_thumb_fingers: int = 4
    point_index_min_extension_margin: float = 0.15
    point_folded_finger_max_extension_margin: float = 0.03

    def __post_init__(self) -> None:
        _probability(
            self.minimum_usable_hand_confidence, "minimum_usable_hand_confidence"
        )
        _probability(
            self.minimum_classification_confidence, "minimum_classification_confidence"
        )
        if (
            isinstance(self.pinch_distance_threshold, bool)
            or not isinstance(self.pinch_distance_threshold, (int, float))
            or not isfinite(self.pinch_distance_threshold)
            or self.pinch_distance_threshold <= 0
        ):
            raise ValueError("pinch_distance_threshold must be positive and finite.")
        if (
            isinstance(self.fist_maximum_extended_fingers, bool)
            or not isinstance(self.fist_maximum_extended_fingers, int)
            or not 0 <= self.fist_maximum_extended_fingers <= 5
        ):
            raise ValueError(
                "fist_maximum_extended_fingers must be between zero and five."
            )
        if (
            isinstance(self.open_palm_minimum_extended_non_thumb_fingers, bool)
            or not isinstance(self.open_palm_minimum_extended_non_thumb_fingers, int)
            or not 1 <= self.open_palm_minimum_extended_non_thumb_fingers <= 4
        ):
            raise ValueError(
                "open_palm_minimum_extended_non_thumb_fingers must be between one and four."
            )
        if (
            self.fist_maximum_extended_fingers
            >= self.open_palm_minimum_extended_non_thumb_fingers + 1
        ):
            raise ValueError("fist and open-palm count thresholds overlap.")
        for name in (
            "point_index_min_extension_margin",
            "point_folded_finger_max_extension_margin",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative.")


def _diagnostics(features: HandFeatures) -> dict[str, float | int | bool]:
    return {
        "thumb_index_distance": features.thumb_index_distance,
        "extended_finger_count": features.extended_finger_count,
        "extended_non_thumb_count": features.extended_non_thumb_count,
        "folded_finger_count": features.folded_finger_count,
    }


def classify(
    features: HandFeatures | HandObservation | None,
    policy: GestureClassifierPolicy | None = None,
) -> GestureCandidate | None:
    """Classify a feature set or observation, using pinch-first deterministic precedence."""
    candidate, _ = classify_with_diagnostics(features, policy=policy)
    return candidate


def classify_with_diagnostics(
    features: HandFeatures | HandObservation | None,
    policy: GestureClassifierPolicy | None = None,
    *,
    frame_sequence: int = 0,
    handedness: str | None = None,
) -> tuple[GestureCandidate | None, FallbackClassificationDiagnostics | None]:
    """Classify once and retain scalar diagnostics for the service logging boundary."""
    if features is None:
        return None, None
    policy = policy or GestureClassifierPolicy()
    observation = features if isinstance(features, HandObservation) else None
    if observation is not None:
        if observation.handedness_confidence < policy.minimum_usable_hand_confidence:
            return (
                GestureCandidate(
                    GestureId.UNKNOWN,
                    0.0,
                    "hand_confidence_below_usable_threshold",
                    observation.handedness.value,
                    False,
                ),
                None,
            )
        features = extract_features(observation)
    if not isinstance(features, HandFeatures):
        raise ValueError("features must be HandFeatures, HandObservation, or None.")
    candidate_handedness = (
        observation.handedness.value if observation is not None else handedness
    )
    diagnostics = _diagnostics(features)
    pinch_predicate = features.thumb_index_distance <= policy.pinch_distance_threshold
    point_epsilon = 1e-12
    point_predicate = (
        features.index_extension_margin + point_epsilon
        >= policy.point_index_min_extension_margin
        and features.middle_extension_margin
        <= policy.point_folded_finger_max_extension_margin + point_epsilon
        and features.ring_extension_margin
        <= policy.point_folded_finger_max_extension_margin + point_epsilon
        and features.little_extension_margin
        <= policy.point_folded_finger_max_extension_margin + point_epsilon
    )
    fist_predicate = (
        features.extended_finger_count <= policy.fist_maximum_extended_fingers
    )
    open_palm_predicate = (
        features.extended_non_thumb_count
        >= policy.open_palm_minimum_extended_non_thumb_fingers
    )
    if pinch_predicate:
        gesture, confidence, reason = GestureId.PINCH, 0.9, "thumb_index_distance"
    elif point_predicate:
        gesture, confidence, reason = GestureId.POINT, 0.85, "index_extended"
    elif fist_predicate:
        gesture, confidence, reason = GestureId.CLOSED_FIST, 0.8, "folded_fingers"
    elif open_palm_predicate:
        gesture, confidence, reason = GestureId.OPEN_PALM, 0.85, "extended_fingers"
    else:
        gesture, confidence, reason = GestureId.UNKNOWN, 0.0, "no_rule"
    threshold_satisfied = (
        gesture is not GestureId.UNKNOWN
        and confidence >= policy.minimum_classification_confidence
    )
    candidate = GestureCandidate(
        GestureId.UNKNOWN if not threshold_satisfied else gesture,
        confidence if isfinite(confidence) else 0.0,
        (
            "classification_below_threshold"
            if not threshold_satisfied and gesture is not GestureId.UNKNOWN
            else reason
        ),
        candidate_handedness,
        threshold_satisfied,
        diagnostics,
    )
    return candidate, FallbackClassificationDiagnostics(
        {
            **diagnostics,
            "pinch_predicate": pinch_predicate,
            "point_predicate": point_predicate,
            "closed_fist_predicate": fist_predicate,
            "open_palm_predicate": open_palm_predicate,
            "final_candidate": candidate.gesture_id.value,
        }
    )
