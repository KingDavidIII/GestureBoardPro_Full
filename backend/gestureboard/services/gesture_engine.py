"""Deterministic temporal filtering and dispatch of gesture observations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from numbers import Real
from time import monotonic
from types import MappingProxyType, TracebackType

from .action_dispatcher import (
    ActionDispatcher,
    ActionDispatcherError,
    DispatchResult,
)
from .gesture_classifier import GestureLabel, GesturePrediction


class GestureEngineError(RuntimeError):
    """Raised for invalid temporal input, configuration, or dispatch failure."""


@dataclass(frozen=True, slots=True)
class GestureObservation:
    """A classifier prediction paired with MediaPipe detection confidence."""

    prediction: GesturePrediction
    detection_confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.prediction, GesturePrediction):
            raise GestureEngineError(
                "GestureObservation.prediction must be a GesturePrediction."
            )
        confidence = self.detection_confidence
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, Real)
            or not isfinite(confidence)
        ):
            raise GestureEngineError(
                "detection_confidence must be a finite real number."
            )
        if not 0.0 <= confidence <= 1.0:
            raise GestureEngineError(
                "detection_confidence must be between 0.0 and 1.0."
            )


@dataclass(frozen=True, slots=True)
class GestureRepeatPolicy:
    """Conservative repeat timing for one explicitly configured label."""

    repeat_delay: float
    repeat_interval: float

    def __post_init__(self) -> None:
        if not _is_finite_real(self.repeat_delay) or self.repeat_delay < 0.0:
            raise GestureEngineError("repeat_delay must be a non-negative number.")
        if not _is_finite_real(self.repeat_interval) or self.repeat_interval <= 0.0:
            raise GestureEngineError("repeat_interval must be greater than zero.")


@dataclass(frozen=True, slots=True)
class GestureEngineConfig:
    """Validated temporal thresholds and opt-in per-label repeat policies."""

    minimum_detection_confidence: float = 0.7
    activation_frames: int = 3
    release_frames: int = 2
    cooldown_seconds: float = 0.5
    repeat_policies: Mapping[GestureLabel, GestureRepeatPolicy] | None = None

    def __post_init__(self) -> None:
        confidence = self.minimum_detection_confidence
        if not _is_finite_real(confidence) or not 0.0 <= confidence <= 1.0:
            raise GestureEngineError(
                "minimum_detection_confidence must be between 0.0 and 1.0."
            )
        if (
            isinstance(self.activation_frames, bool)
            or not isinstance(self.activation_frames, int)
            or self.activation_frames < 1
        ):
            raise GestureEngineError("activation_frames must be at least one.")
        if (
            isinstance(self.release_frames, bool)
            or not isinstance(self.release_frames, int)
            or self.release_frames < 1
        ):
            raise GestureEngineError("release_frames must be at least one.")
        if not _is_finite_real(self.cooldown_seconds) or self.cooldown_seconds < 0.0:
            raise GestureEngineError("cooldown_seconds must be non-negative.")

        policies = dict(self.repeat_policies or {})
        for label, policy in policies.items():
            if not isinstance(label, GestureLabel) or label is GestureLabel.UNKNOWN:
                raise GestureEngineError(
                    "Repeat policies require non-UNKNOWN GestureLabel keys."
                )
            if not isinstance(policy, GestureRepeatPolicy):
                raise GestureEngineError(
                    "repeat_policies values must be GestureRepeatPolicy objects."
                )
        object.__setattr__(self, "repeat_policies", MappingProxyType(policies))


class GestureEngineDecision(StrEnum):
    """Reason a temporal observation did or did not dispatch."""

    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNKNOWN = "UNKNOWN"
    ACCUMULATING = "ACCUMULATING"
    ACTIVATED = "ACTIVATED"
    DISPATCHED = "DISPATCHED"
    UNMAPPED = "UNMAPPED"
    HELD_SUPPRESSED = "HELD_SUPPRESSED"
    COOLDOWN_SUPPRESSED = "COOLDOWN_SUPPRESSED"
    REPEAT_WAITING = "REPEAT_WAITING"
    REPEATED = "REPEATED"
    RELEASE_ACCUMULATING = "RELEASE_ACCUMULATING"
    RELEASED = "RELEASED"


@dataclass(frozen=True, slots=True)
class GestureEngineResult:
    """Immutable diagnostic snapshot after processing one observation."""

    observation: GestureObservation
    decision: GestureEngineDecision
    candidate_label: GestureLabel | None
    candidate_frame_count: int
    active_label: GestureLabel | None
    release_frame_count: int
    timestamp: float
    dispatch_result: DispatchResult | None = None

    @property
    def action_executed(self) -> bool:
        return bool(self.dispatch_result and self.dispatch_result.executed)

    @property
    def prediction(self) -> GesturePrediction:
        return self.observation.prediction

    @property
    def detection_confidence(self) -> float:
        return self.observation.detection_confidence


class GestureEngine:
    """Apply stability, release, cooldown, and repeat rules synchronously.

    Activation is committed immediately before calling the dispatcher. If the
    dispatcher fails, the gesture remains active so an uncertain keyboard
    operation is not automatically retried on the next frame.
    """

    def __init__(
        self,
        dispatcher: ActionDispatcher | None = None,
        config: GestureEngineConfig | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not callable(clock):
            raise GestureEngineError("clock must be callable.")
        self.dispatcher = dispatcher if dispatcher is not None else ActionDispatcher()
        self.config = config or GestureEngineConfig()
        self._owns_dispatcher = dispatcher is None
        self._clock = clock
        self._closed = False
        self.reset()

    def process(
        self,
        observation: GestureObservation,
        *,
        timestamp: float | None = None,
    ) -> GestureEngineResult:
        """Process one observation and return a complete decision snapshot."""

        if self._closed:
            raise GestureEngineError("Gesture engine has been closed.")
        if not isinstance(observation, GestureObservation):
            raise GestureEngineError("process() requires a GestureObservation.")
        now = self._timestamp(timestamp)
        self._last_timestamp = now

        label = observation.prediction.label
        neutral_decision: GestureEngineDecision | None = None
        if observation.detection_confidence < self.config.minimum_detection_confidence:
            neutral_decision = GestureEngineDecision.LOW_CONFIDENCE
        elif label is GestureLabel.UNKNOWN:
            neutral_decision = GestureEngineDecision.UNKNOWN

        if neutral_decision is not None:
            return self._process_neutral(observation, now, neutral_decision)
        return self._process_label(observation, now, label)

    def _process_neutral(
        self,
        observation: GestureObservation,
        now: float,
        neutral_decision: GestureEngineDecision,
    ) -> GestureEngineResult:
        self._candidate_label = None
        self._candidate_count = 0
        if self._active_label is None:
            self._release_count = 0
            return self._result(observation, now, neutral_decision)

        self._release_count += 1
        if self._release_count < self.config.release_frames:
            return self._result(
                observation, now, GestureEngineDecision.RELEASE_ACCUMULATING
            )

        self._active_label = None
        self._release_count = 0
        self._repeat_started_at = None
        self._last_repeat_at = None
        return self._result(observation, now, GestureEngineDecision.RELEASED)

    def _process_label(
        self,
        observation: GestureObservation,
        now: float,
        label: GestureLabel,
    ) -> GestureEngineResult:
        self._release_count = 0
        if label is self._active_label:
            self._candidate_label = None
            self._candidate_count = 0
            return self._process_held(observation, now, label)

        if label is self._candidate_label:
            self._candidate_count += 1
        else:
            self._candidate_label = label
            self._candidate_count = 1

        if self._candidate_count < self.config.activation_frames:
            return self._result(observation, now, GestureEngineDecision.ACCUMULATING)
        if self._cooldown_active(now):
            return self._result(
                observation, now, GestureEngineDecision.COOLDOWN_SUPPRESSED
            )
        return self._activate_and_dispatch(observation, now, label)

    def _activate_and_dispatch(
        self,
        observation: GestureObservation,
        now: float,
        label: GestureLabel,
    ) -> GestureEngineResult:
        self._active_label = label
        self._candidate_label = None
        self._candidate_count = 0
        self._repeat_started_at = now
        self._last_repeat_at = None
        dispatch_result = self._dispatch(observation.prediction, label)
        if dispatch_result.executed:
            self._last_dispatch_at = now
            decision = GestureEngineDecision.DISPATCHED
        else:
            decision = GestureEngineDecision.UNMAPPED
        return self._result(observation, now, decision, dispatch_result)

    def _process_held(
        self,
        observation: GestureObservation,
        now: float,
        label: GestureLabel,
    ) -> GestureEngineResult:
        policy = self.config.repeat_policies.get(label)  # type: ignore[union-attr]
        if policy is None:
            return self._result(observation, now, GestureEngineDecision.HELD_SUPPRESSED)
        repeat_started_at = self._repeat_started_at
        if repeat_started_at is None or now - repeat_started_at < policy.repeat_delay:
            return self._result(observation, now, GestureEngineDecision.REPEAT_WAITING)
        if (
            self._last_repeat_at is not None
            and now - self._last_repeat_at < policy.repeat_interval
        ):
            return self._result(observation, now, GestureEngineDecision.REPEAT_WAITING)
        if self._cooldown_active(now):
            return self._result(
                observation, now, GestureEngineDecision.COOLDOWN_SUPPRESSED
            )

        self._last_repeat_at = now
        dispatch_result = self._dispatch(observation.prediction, label)
        if dispatch_result.executed:
            self._last_dispatch_at = now
            decision = GestureEngineDecision.REPEATED
        else:
            decision = GestureEngineDecision.UNMAPPED
        return self._result(observation, now, decision, dispatch_result)

    def _dispatch(
        self, prediction: GesturePrediction, label: GestureLabel
    ) -> DispatchResult:
        try:
            return self.dispatcher.dispatch(prediction)
        except ActionDispatcherError as error:
            raise GestureEngineError(
                f"Action dispatch failed for gesture {label.value}."
            ) from error

    def _cooldown_active(self, now: float) -> bool:
        return (
            self._last_dispatch_at is not None
            and now - self._last_dispatch_at < self.config.cooldown_seconds
        )

    def _timestamp(self, timestamp: float | None) -> float:
        try:
            value = self._clock() if timestamp is None else timestamp
        except Exception as error:
            raise GestureEngineError("The monotonic clock failed.") from error
        if not _is_finite_real(value):
            raise GestureEngineError("timestamp must be a finite real number.")
        value = float(value)
        if self._last_timestamp is not None and value < self._last_timestamp:
            raise GestureEngineError(
                "timestamp cannot be earlier than the previous timestamp."
            )
        return value

    def _result(
        self,
        observation: GestureObservation,
        now: float,
        decision: GestureEngineDecision,
        dispatch_result: DispatchResult | None = None,
    ) -> GestureEngineResult:
        return GestureEngineResult(
            observation=observation,
            decision=decision,
            candidate_label=self._candidate_label,
            candidate_frame_count=self._candidate_count,
            active_label=self._active_label,
            release_frame_count=self._release_count,
            timestamp=now,
            dispatch_result=dispatch_result,
        )

    def reset(self) -> None:
        """Clear all temporal state without closing the dispatcher."""

        self._candidate_label: GestureLabel | None = None
        self._candidate_count = 0
        self._active_label: GestureLabel | None = None
        self._release_count = 0
        self._last_dispatch_at: float | None = None
        self._repeat_started_at: float | None = None
        self._last_repeat_at: float | None = None
        self._last_timestamp: float | None = None

    def close(self) -> None:
        """Close an internally created dispatcher no more than once."""

        if self._closed:
            return
        self._closed = True
        if self._owns_dispatcher:
            try:
                self.dispatcher.close()
            except ActionDispatcherError as error:
                raise GestureEngineError(
                    "Failed to close the owned dispatcher."
                ) from error

    def __enter__(self) -> GestureEngine:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _is_finite_real(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(value)
