"""Immutable protocol-neutral recognition domain values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from types import MappingProxyType


class GestureId(StrEnum):
    OPEN_PALM = "open_palm"
    CLOSED_FIST = "closed_fist"
    POINT = "point"
    PINCH = "pinch"
    UNKNOWN = "unknown"


class TransitionKind(StrEnum):
    ACTIVATED = "activated"
    CHANGED = "changed"
    RELEASED = "released"


def _confidence(value: float, name: str = "confidence") -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
    ):
        raise ValueError(f"{name} must be finite.")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1.")
    return float(value)


def _non_negative_int(value: int | None, name: str) -> int | None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError(f"{name} must be a non-negative integer or None.")
    return value


@dataclass(frozen=True, slots=True)
class GestureCandidate:
    """A per-frame rule result with only safe, scalar diagnostics."""

    gesture_id: GestureId
    confidence: float
    reason: str
    handedness: str | None = None
    threshold_satisfied: bool = False
    diagnostics: Mapping[str, float | int | bool | str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.gesture_id, GestureId):
            raise ValueError("gesture_id must be a GestureId.")
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be a non-empty string.")
        if self.handedness is not None and self.handedness not in {
            "left",
            "right",
            "unknown",
        }:
            raise ValueError("handedness must be left, right, unknown, or None.")
        if not isinstance(self.threshold_satisfied, bool):
            raise ValueError("threshold_satisfied must be boolean.")
        if not isinstance(self.diagnostics, Mapping):
            raise ValueError("diagnostics must be a mapping.")
        safe: dict[str, float | int | bool | str] = {}
        for key, value in self.diagnostics.items():
            if not isinstance(key, str) or not isinstance(
                value, (str, int, float, bool)
            ):
                raise ValueError("diagnostics must contain scalar values.")
            if isinstance(value, float) and not isfinite(value):
                raise ValueError("diagnostics must not contain non-finite floats.")
            safe[key] = value
        object.__setattr__(self, "diagnostics", MappingProxyType(safe))


@dataclass(frozen=True, slots=True)
class GestureTransition:
    event_id: int
    kind: TransitionKind
    previous_gesture: GestureId | None
    gesture: GestureId | None
    confidence: float
    timestamp_ms: float
    frame_sequence: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.event_id, bool)
            or not isinstance(self.event_id, int)
            or self.event_id < 1
        ):
            raise ValueError("event_id must be a positive integer.")
        if not isinstance(self.kind, TransitionKind):
            raise ValueError("kind must be a TransitionKind.")
        if self.previous_gesture is not None and not isinstance(
            self.previous_gesture, GestureId
        ):
            raise ValueError("previous_gesture must be a GestureId or None.")
        if self.gesture is not None and not isinstance(self.gesture, GestureId):
            raise ValueError("gesture must be a GestureId or None.")
        if self.kind is TransitionKind.ACTIVATED and (
            self.previous_gesture is not None or self.gesture is None
        ):
            raise ValueError(
                "activated transitions require no previous gesture and a current gesture."
            )
        if self.kind is TransitionKind.CHANGED and (
            self.previous_gesture is None or self.gesture is None
        ):
            raise ValueError(
                "changed transitions require previous and current gestures."
            )
        if self.kind is TransitionKind.RELEASED and (
            self.previous_gesture is None or self.gesture is not None
        ):
            raise ValueError(
                "released transitions require previous gesture and no current gesture."
            )
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        if (
            isinstance(self.timestamp_ms, bool)
            or not isinstance(self.timestamp_ms, (int, float))
            or not isfinite(self.timestamp_ms)
            or self.timestamp_ms < 0
        ):
            raise ValueError("timestamp_ms must be finite and non-negative.")
        object.__setattr__(self, "timestamp_ms", float(self.timestamp_ms))
        _non_negative_int(self.frame_sequence, "frame_sequence")
