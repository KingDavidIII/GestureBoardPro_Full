"""Pure, timestamp-driven button intent and safe click/drag decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import hypot, isfinite

from gestureboard.recognition.observations import HandObservation

from .models import MouseLifecycleError, MouseValidationError


class MouseButton(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class MouseButtonIntent(StrEnum):
    NONE = "none"
    PRIMARY_CONTACT = "primary_contact"
    SECONDARY_CONTACT = "secondary_contact"
    AMBIGUOUS = "ambiguous"


class MouseButtonState(StrEnum):
    IDLE = "idle"
    PRIMARY_PENDING = "primary_pending"
    DRAGGING = "dragging"
    SECONDARY_LATCHED = "secondary_latched"
    CLOSED = "closed"


class MouseButtonActionKind(StrEnum):
    PRIMARY_CLICK = "primary_click"
    SECONDARY_CLICK = "secondary_click"
    PRIMARY_DOWN = "primary_down"
    PRIMARY_UP = "primary_up"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True, slots=True)
class MouseButtonPolicy:
    contact_activation_threshold: float = 0.20
    contact_release_threshold: float = 0.25
    contact_isolation_ratio: float = 0.40
    intent_activation_ms: int = 120
    intent_release_ms: int = 80
    click_cooldown_ms: int = 350
    drag_hold_ms: int = 500
    buttons_enabled: bool = False
    drag_enabled: bool = False

    def __post_init__(self) -> None:
        values = (
            self.contact_activation_threshold,
            self.contact_release_threshold,
            self.contact_isolation_ratio,
        )
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
            for v in values
        ):
            raise MouseValidationError("button thresholds must be finite numbers.")
        if (
            not 0 <= self.contact_activation_threshold < self.contact_release_threshold
            or not 0 < self.contact_isolation_ratio <= 1
        ):
            raise MouseValidationError("button contact thresholds are invalid.")
        for value in (
            self.intent_activation_ms,
            self.intent_release_ms,
            self.click_cooldown_ms,
            self.drag_hold_ms,
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 < value <= 10_000
            ):
                raise MouseValidationError(
                    "button durations must be bounded non-negative integers."
                )
        if (
            self.drag_hold_ms < self.intent_activation_ms
            or not isinstance(self.buttons_enabled, bool)
            or not isinstance(self.drag_enabled, bool)
        ):
            raise MouseValidationError("button policy is invalid.")


@dataclass(frozen=True, slots=True)
class MouseButtonDecision:
    accepted: bool
    action: MouseButtonActionKind | None
    state: MouseButtonState
    timestamp_ms: int
    source_index: int | None
    intent: MouseButtonIntent
    primary_held: bool
    secondary_held: bool
    sequence: int


def detect_button_intent(
    hand: HandObservation | None,
    policy: MouseButtonPolicy,
    previous_intent: MouseButtonIntent = MouseButtonIntent.NONE,
) -> MouseButtonIntent:
    if not isinstance(hand, HandObservation):
        return MouseButtonIntent.AMBIGUOUS
    try:
        thumb, index, middle = (hand.landmarks[i] for i in (4, 8, 12))
        scale = hand.palm_scale
        if not isfinite(scale) or scale <= 0:
            return MouseButtonIntent.AMBIGUOUS
        for point in (thumb, index, middle):
            if (
                isinstance(point.x, bool)
                or isinstance(point.y, bool)
                or not isinstance(point.x, (int, float))
                or not isinstance(point.y, (int, float))
                or not isfinite(point.x)
                or not isfinite(point.y)
            ):
                return MouseButtonIntent.AMBIGUOUS
        distances = tuple(
            hypot(thumb.x - point.x, thumb.y - point.y) / scale
            for point in (index, middle)
        )
        if not all(isfinite(v) for v in distances):
            return MouseButtonIntent.AMBIGUOUS
    except (AttributeError, IndexError, TypeError):
        return MouseButtonIntent.AMBIGUOUS
    primary_distance, secondary_distance = distances
    primary_threshold = (
        policy.contact_release_threshold
        if previous_intent is MouseButtonIntent.PRIMARY_CONTACT
        else policy.contact_activation_threshold
    )
    secondary_threshold = (
        policy.contact_release_threshold
        if previous_intent is MouseButtonIntent.SECONDARY_CONTACT
        else policy.contact_activation_threshold
    )
    primary = (
        primary_distance <= primary_threshold
        and primary_distance <= secondary_distance * policy.contact_isolation_ratio
    )
    secondary = (
        secondary_distance <= secondary_threshold
        and secondary_distance <= primary_distance * policy.contact_isolation_ratio
    )
    primary_in_range = primary_distance <= primary_threshold
    secondary_in_range = secondary_distance <= secondary_threshold
    if primary and secondary or (primary_in_range and secondary_in_range):
        return MouseButtonIntent.AMBIGUOUS
    if (primary_in_range or secondary_in_range) and not (primary or secondary):
        return MouseButtonIntent.AMBIGUOUS
    return (
        MouseButtonIntent.PRIMARY_CONTACT
        if primary
        else (
            MouseButtonIntent.SECONDARY_CONTACT if secondary else MouseButtonIntent.NONE
        )
    )


class MouseButtonController:
    def __init__(self, policy: MouseButtonPolicy | None = None) -> None:
        self.policy = policy or MouseButtonPolicy()
        self._state = MouseButtonState.IDLE
        self._intent = MouseButtonIntent.NONE
        self._started: int | None = None
        self._primary_armed = False
        self._cooldown_until = 0
        self._release_started: int | None = None
        self._source: int | None = None
        self._sequence = 0
        self._last_timestamps: dict[int | None, int] = {}
        self._last_controller_timestamp = 0
        self._requires_release = False
        self._suppressed_release_started: int | None = None

    def process(
        self, intent: MouseButtonIntent, *, timestamp_ms: int, source_index: int | None
    ) -> MouseButtonDecision:
        if self._state is MouseButtonState.CLOSED:
            raise MouseLifecycleError("mouse button controller is shut down.")
        if not isinstance(intent, MouseButtonIntent):
            return self._rejected(timestamp_ms, source_index)
        if (
            isinstance(source_index, bool)
            or not isinstance(source_index, int)
            or source_index < 0
        ):
            return self._rejected(timestamp_ms, source_index)
        if (
            isinstance(timestamp_ms, bool)
            or not isinstance(timestamp_ms, int)
            or timestamp_ms < 0
        ):
            return self._rejected(timestamp_ms, source_index)
        if timestamp_ms < self._last_controller_timestamp:
            return self._rejected(timestamp_ms, source_index)
        last_timestamp = self._last_timestamps.get(source_index)
        if last_timestamp is not None and timestamp_ms < last_timestamp:
            return self._rejected(timestamp_ms, source_index)
        self._last_timestamps[source_index] = timestamp_ms
        self._last_controller_timestamp = max(
            self._last_controller_timestamp, timestamp_ms
        )
        if (
            not self.policy.buttons_enabled
            or intent is MouseButtonIntent.AMBIGUOUS
            or source_index != self._source
            and self._source is not None
        ):
            return self._reset(timestamp_ms=timestamp_ms)
        self._source = source_index
        if self._requires_release:
            if intent is not MouseButtonIntent.NONE:
                self._suppressed_release_started = None
                return self._decision(MouseButtonActionKind.SUPPRESSED, timestamp_ms)
            if self._suppressed_release_started is None:
                self._suppressed_release_started = timestamp_ms
                return self._decision(None, timestamp_ms)
            if (
                timestamp_ms - self._suppressed_release_started
                < self.policy.intent_release_ms
            ):
                return self._decision(None, timestamp_ms)
            self._requires_release = False
            self._suppressed_release_started = None
            self._state = MouseButtonState.IDLE
            self._intent = MouseButtonIntent.NONE
            self._started = None
            self._primary_armed = False
            self._release_started = None
            return self._decision(None, timestamp_ms)
        if (
            intent is not MouseButtonIntent.NONE
            and intent is not self._intent
            and timestamp_ms < self._cooldown_until
        ):
            self._requires_release = True
            return self._decision(MouseButtonActionKind.SUPPRESSED, timestamp_ms)
        if intent is MouseButtonIntent.NONE and self._intent in {
            MouseButtonIntent.PRIMARY_CONTACT,
            MouseButtonIntent.SECONDARY_CONTACT,
        }:
            if self._release_started is None:
                self._release_started = timestamp_ms
                return self._decision(None, timestamp_ms)
            if timestamp_ms - self._release_started < self.policy.intent_release_ms:
                return self._decision(None, timestamp_ms)
            previous = self._intent
            was_primary_armed = self._primary_armed
            self._intent, self._started, self._release_started = (
                MouseButtonIntent.NONE,
                None,
                None,
            )
            self._cooldown_until = timestamp_ms + self.policy.click_cooldown_ms
            if self._state is MouseButtonState.DRAGGING:
                self._state = MouseButtonState.IDLE
                return self._decision(MouseButtonActionKind.PRIMARY_UP, timestamp_ms)
            self._state = MouseButtonState.IDLE
            if previous is MouseButtonIntent.SECONDARY_CONTACT:
                return self._decision(None, timestamp_ms)
            if not was_primary_armed:
                return self._decision(None, timestamp_ms)
            self._primary_armed = False
            return self._decision(
                MouseButtonActionKind.PRIMARY_CLICK,
                timestamp_ms,
            )
        if intent is self._intent:
            self._release_started = None
        if intent is not self._intent:
            if (
                self._state is MouseButtonState.DRAGGING
                and intent is not MouseButtonIntent.PRIMARY_CONTACT
            ):
                return self._force_release_drag(timestamp_ms)
            self._intent, self._started = intent, timestamp_ms
            self._primary_armed = False
            if self._state is MouseButtonState.DRAGGING:
                self._state = MouseButtonState.IDLE
                self._cooldown_until = timestamp_ms + self.policy.click_cooldown_ms
                return self._decision(MouseButtonActionKind.PRIMARY_UP, timestamp_ms)
            self._state = (
                MouseButtonState.PRIMARY_PENDING
                if intent is MouseButtonIntent.PRIMARY_CONTACT
                else MouseButtonState.IDLE
            )
            return self._decision(None, timestamp_ms)
        elapsed = timestamp_ms - (
            self._started if self._started is not None else timestamp_ms
        )
        if timestamp_ms < self._cooldown_until:
            return self._decision(MouseButtonActionKind.SUPPRESSED, timestamp_ms)
        if (
            intent is MouseButtonIntent.PRIMARY_CONTACT
            and self._state is MouseButtonState.PRIMARY_PENDING
            and elapsed >= self.policy.intent_activation_ms
        ):
            self._primary_armed = True
        if (
            intent is MouseButtonIntent.PRIMARY_CONTACT
            and elapsed >= self.policy.drag_hold_ms
            and self.policy.drag_enabled
            and self._state is MouseButtonState.PRIMARY_PENDING
        ):
            self._state = MouseButtonState.DRAGGING
            self._primary_armed = False
            return self._decision(MouseButtonActionKind.PRIMARY_DOWN, timestamp_ms)
        if (
            intent is MouseButtonIntent.SECONDARY_CONTACT
            and elapsed >= self.policy.intent_activation_ms
            and self._state is MouseButtonState.IDLE
        ):
            self._state = MouseButtonState.SECONDARY_LATCHED
            self._cooldown_until = timestamp_ms + self.policy.click_cooldown_ms
            self._requires_release = True
            return self._decision(MouseButtonActionKind.SECONDARY_CLICK, timestamp_ms)
        return self._decision(None, timestamp_ms)

    def reset(self, *, timestamp_ms: int) -> MouseButtonDecision:
        self._validate_safety_timestamp(timestamp_ms)
        return self._reset(timestamp_ms=timestamp_ms)

    def _reset(self, *, timestamp_ms: int) -> MouseButtonDecision:
        if self._state is MouseButtonState.CLOSED:
            return self._decision(None, timestamp_ms)
        if self._state is MouseButtonState.DRAGGING:
            return self._force_release_drag(timestamp_ms)
        was_secondary_latched = self._state is MouseButtonState.SECONDARY_LATCHED
        (
            self._state,
            self._intent,
            self._started,
            self._primary_armed,
            self._source,
            self._release_started,
        ) = (
            MouseButtonState.IDLE,
            MouseButtonIntent.NONE,
            None,
            False,
            None,
            None,
        )
        self._last_timestamps.clear()
        self._requires_release = self._requires_release or was_secondary_latched
        self._suppressed_release_started = None
        return self._decision(None, timestamp_ms)

    def _force_release_drag(self, timestamp_ms: int) -> MouseButtonDecision:
        if self._state is not MouseButtonState.DRAGGING:
            return self._decision(None, timestamp_ms)
        self._state = MouseButtonState.IDLE
        self._intent = MouseButtonIntent.NONE
        self._started = None
        self._primary_armed = False
        self._release_started = None
        self._source = None
        self._requires_release = True
        self._suppressed_release_started = None
        self._cooldown_until = timestamp_ms + self.policy.click_cooldown_ms
        return self._decision(MouseButtonActionKind.PRIMARY_UP, timestamp_ms)

    def emergency_stop(self, *, timestamp_ms: int) -> MouseButtonDecision:
        return self.reset(timestamp_ms=timestamp_ms)

    def shutdown(self, *, timestamp_ms: int) -> MouseButtonDecision:
        self._validate_safety_timestamp(timestamp_ms)
        if self._state is MouseButtonState.CLOSED:
            return self._decision(None, timestamp_ms)
        decision = self.reset(timestamp_ms=timestamp_ms)
        self._state = MouseButtonState.CLOSED
        return MouseButtonDecision(
            decision.accepted,
            decision.action,
            self._state,
            timestamp_ms,
            None,
            MouseButtonIntent.NONE,
            False,
            False,
            decision.sequence,
        )

    def _validate_safety_timestamp(self, timestamp_ms: object) -> None:
        if (
            isinstance(timestamp_ms, bool)
            or not isinstance(timestamp_ms, int)
            or timestamp_ms < self._last_controller_timestamp
        ):
            raise MouseValidationError(
                "safety timestamp must be a non-negative monotonic integer."
            )
        self._last_controller_timestamp = timestamp_ms

    def _rejected(
        self, timestamp_ms: int, source_index: int | None
    ) -> MouseButtonDecision:
        return MouseButtonDecision(
            False,
            None,
            self._state,
            timestamp_ms,
            source_index,
            self._intent,
            self._state is MouseButtonState.DRAGGING,
            False,
            self._sequence,
        )

    def _decision(
        self, action: MouseButtonActionKind | None, timestamp_ms: int
    ) -> MouseButtonDecision:
        if action in {
            MouseButtonActionKind.PRIMARY_CLICK,
            MouseButtonActionKind.SECONDARY_CLICK,
            MouseButtonActionKind.PRIMARY_DOWN,
            MouseButtonActionKind.PRIMARY_UP,
        }:
            self._sequence += 1
        return MouseButtonDecision(
            True,
            action,
            self._state,
            timestamp_ms,
            self._source,
            self._intent,
            self._state is MouseButtonState.DRAGGING,
            False,
            self._sequence,
        )
