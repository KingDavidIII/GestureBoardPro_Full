"""Per-stream recognition bridge for one already-produced task result."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from types import MappingProxyType

from .classifier import GestureClassifierPolicy, classify
from .models import GestureCandidate, GestureTransition
from .observations import HandObservation, HandSelection, adapt_hands, select_primary
from .stabilizer import GestureStabilizer, GestureStabilizerPolicy
from .task_engine import CannedGesturePolicy, GestureTaskResult, classify_task_hand

feature_logger = logging.getLogger("gestureboard.recognition")


@dataclass(frozen=True, slots=True)
class RecognitionFrameResult:
    schema_version: int
    frame_sequence: int
    hand_count: int
    primary_hand: HandObservation | None
    candidate: GestureCandidate | None
    stable: GestureCandidate | None
    transition: GestureTransition | None
    stable_since_ms: float | None
    confirmed_frames: int
    timestamp_ms: float


class RecognitionService:
    """Adapt task landmarks once and preserve existing stabilisation semantics."""

    def __init__(
        self,
        *,
        classifier_policy: GestureClassifierPolicy | None = None,
        canned_policy: CannedGesturePolicy | None = None,
        stabilizer_policy: GestureStabilizerPolicy | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.classifier_policy = classifier_policy or GestureClassifierPolicy()
        self.stabilizer = GestureStabilizer(stabilizer_policy, clock)
        self._clock = clock
        self.canned_policy = canned_policy or CannedGesturePolicy()
        self._stable_since_ms: float | None = None
        self._confirmed_frames = 0

    def process(
        self, result: object, *, frame_sequence: int, timestamp_ms: float | None = None
    ) -> RecognitionFrameResult:
        if (
            isinstance(frame_sequence, bool)
            or not isinstance(frame_sequence, int)
            or frame_sequence < 0
        ):
            raise ValueError("frame_sequence must be a non-negative integer.")
        now = float(self._clock() * 1000 if timestamp_ms is None else timestamp_ms)
        if not isfinite(now) or now < 0:
            raise ValueError("timestamp_ms must be finite and non-negative.")
        cached_selection = getattr(result, "selection", None)
        selection = (
            cached_selection
            if isinstance(cached_selection, HandSelection)
            else select_primary(adapt_hands(result))
        )
        primary = selection.primary_hand
        candidate = (
            (
                classify_task_hand(result, primary, self.canned_policy)
                if isinstance(result, GestureTaskResult)
                else classify(primary, self.classifier_policy)
            )
            if primary is not None
            else None
        )
        transition = self.stabilizer.update(
            candidate, frame_sequence=frame_sequence, timestamp_ms=now
        )
        stable = self.stabilizer.stable
        if stable is None:
            self._stable_since_ms = None
            self._confirmed_frames = 0
        elif transition is not None and transition.gesture is stable.gesture_id:
            self._stable_since_ms = now
            self._confirmed_frames = self.stabilizer.policy.confirmation_frames
        else:
            self._confirmed_frames += 1
        return RecognitionFrameResult(
            1,
            frame_sequence,
            selection.valid_hand_count,
            primary,
            candidate,
            stable,
            transition,
            self._stable_since_ms,
            self._confirmed_frames,
            now,
        )

    def reset(self) -> None:
        self.stabilizer.reset()
        self._stable_since_ms = None
        self._confirmed_frames = 0


def serialize_recognition(
    result: RecognitionFrameResult, *, now_ms: float | None = None
) -> Mapping[str, object]:
    now = result.timestamp_ms if now_ms is None else now_ms
    duration = (
        0.0
        if result.stable_since_ms is None
        else max(0.0, float(now) - result.stable_since_ms)
    )
    return MappingProxyType(
        {
            "schema_version": 1,
            "frame_sequence": result.frame_sequence,
            "hand_count": result.hand_count,
            "primary_hand": (
                None
                if result.primary_hand is None
                else {
                    "handedness": result.primary_hand.handedness.value,
                    "confidence": result.primary_hand.handedness_confidence,
                }
            ),
            "candidate": (
                None
                if result.candidate is None
                else {
                    "gesture_id": result.candidate.gesture_id.value,
                    "confidence": result.candidate.confidence,
                    "reason": result.candidate.reason,
                }
            ),
            "stable": (
                None
                if result.stable is None
                else {
                    "gesture_id": result.stable.gesture_id.value,
                    "confidence": result.stable.confidence,
                    "confirmed_frames": result.confirmed_frames,
                    "since_ms": duration,
                }
            ),
            "transition": (
                None
                if result.transition is None
                else {
                    "event_id": result.transition.event_id,
                    "kind": result.transition.kind.value,
                    "previous_gesture": (
                        None
                        if result.transition.previous_gesture is None
                        else result.transition.previous_gesture.value
                    ),
                    "gesture": (
                        None
                        if result.transition.gesture is None
                        else result.transition.gesture.value
                    ),
                    "confidence": result.transition.confidence,
                }
            ),
        }
    )
