"""Temporal stabilisation for frame-level recognition candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from time import monotonic

from .models import GestureCandidate, GestureId, GestureTransition, TransitionKind


@dataclass(frozen=True, slots=True)
class GestureStabilizerPolicy:
    confirmation_frames: int = 3
    release_frames: int = 2
    maximum_inter_frame_gap_ms: float = 750.0
    minimum_stable_confidence: float = 0.65

    def __post_init__(self) -> None:
        if (
            isinstance(self.confirmation_frames, bool)
            or not isinstance(self.confirmation_frames, int)
            or self.confirmation_frames < 1
            or isinstance(self.release_frames, bool)
            or not isinstance(self.release_frames, int)
            or self.release_frames < 1
        ):
            raise ValueError("confirmation and release frames must be positive.")
        if (
            isinstance(self.maximum_inter_frame_gap_ms, bool)
            or not isinstance(self.maximum_inter_frame_gap_ms, (int, float))
            or not isfinite(self.maximum_inter_frame_gap_ms)
            or self.maximum_inter_frame_gap_ms <= 0
        ):
            raise ValueError("maximum_inter_frame_gap_ms must be positive and finite.")
        if (
            isinstance(self.minimum_stable_confidence, bool)
            or not isinstance(self.minimum_stable_confidence, (int, float))
            or not isfinite(self.minimum_stable_confidence)
            or not 0 <= self.minimum_stable_confidence <= 1
        ):
            raise ValueError("minimum_stable_confidence must be between 0 and 1.")


class GestureStabilizer:
    def __init__(
        self,
        policy: GestureStabilizerPolicy | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.policy = policy or GestureStabilizerPolicy()
        self._clock = clock
        self.reset()

    @property
    def stable(self) -> GestureCandidate | None:
        return self._stable

    @property
    def provisional(self) -> GestureCandidate | None:
        return self._candidate

    @property
    def last_transition(self) -> GestureTransition | None:
        return self._last_transition

    def update(
        self,
        candidate: GestureCandidate | None,
        *,
        frame_sequence: int | None = None,
        timestamp_ms: float | None = None,
    ) -> GestureTransition | None:
        now = float(self._clock() * 1000 if timestamp_ms is None else timestamp_ms)
        if not isfinite(now) or now < 0:
            raise ValueError("timestamp_ms must be finite and non-negative.")
        if frame_sequence is not None and (
            isinstance(frame_sequence, bool)
            or not isinstance(frame_sequence, int)
            or frame_sequence < 0
        ):
            raise ValueError("frame_sequence must be a non-negative integer or None.")
        if (
            self._last_sequence is not None
            and frame_sequence is not None
            and frame_sequence < self._last_sequence
        ):
            self.reset()
        if (
            self._last_time is not None
            and now - self._last_time > self.policy.maximum_inter_frame_gap_ms
        ):
            self.reset()
        self._last_time = now
        self._last_sequence = frame_sequence
        if (
            candidate is None
            or candidate.gesture_id is GestureId.UNKNOWN
            or candidate.confidence < self.policy.minimum_stable_confidence
        ):
            self._candidate = None
            self._candidate_frames = 0
            if self._stable is None:
                return None
            self._release_frames += 1
            if self._release_frames < self.policy.release_frames:
                return None
            previous = self._stable
            self._stable = None
            self._release_frames = 0
            return self._transition(
                TransitionKind.RELEASED,
                previous.gesture_id,
                None,
                previous.confidence,
                now,
                frame_sequence,
            )
        self._release_frames = 0
        if self._stable is not None and candidate.gesture_id is self._stable.gesture_id:
            return None
        if (
            self._candidate is not None
            and candidate.gesture_id is self._candidate.gesture_id
        ):
            self._candidate_frames += 1
        else:
            self._candidate = candidate
            self._candidate_frames = 1
        if self._candidate_frames < self.policy.confirmation_frames:
            return None
        previous = self._stable
        self._stable = candidate
        self._candidate = None
        self._candidate_frames = 0
        return self._transition(
            TransitionKind.ACTIVATED if previous is None else TransitionKind.CHANGED,
            previous.gesture_id if previous else None,
            candidate.gesture_id,
            candidate.confidence,
            now,
            frame_sequence,
        )

    def reset(self) -> None:
        self._stable: GestureCandidate | None = None
        self._candidate: GestureCandidate | None = None
        self._candidate_frames = 0
        self._release_frames = 0
        self._last_time: float | None = None
        self._last_sequence: int | None = None
        self._event_id = 0
        self._last_transition: GestureTransition | None = None

    def _transition(
        self,
        kind: TransitionKind,
        previous: GestureId | None,
        gesture: GestureId | None,
        confidence: float,
        timestamp_ms: float,
        sequence: int | None,
    ) -> GestureTransition:
        self._event_id += 1
        transition = GestureTransition(
            self._event_id, kind, previous, gesture, confidence, timestamp_ms, sequence
        )
        self._last_transition = transition
        return transition
