"""Immutable, operating-system-neutral gesture-mouse domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class MouseValidationError(ValueError):
    """Raised when a mouse-domain value is invalid."""


class MouseLifecycleError(RuntimeError):
    """Raised when an operation is requested after service shutdown."""


class MouseOutputError(RuntimeError):
    """Raised when the configured event output port cannot accept an event."""


class MouseMode(StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class MouseEventKind(StrEnum):
    MODE_CHANGED = "mode_changed"
    CURSOR_TARGET_ACCEPTED = "cursor_target_accepted"
    CURSOR_TARGET_CLEARED = "cursor_target_cleared"
    SAFETY_RESET_REQUESTED = "safety_reset_requested"


class MouseReason(StrEnum):
    ENABLED = "enabled"
    TRACKING_ACQUIRED = "tracking_acquired"
    TRACKING_LOST = "tracking_lost"
    PAUSED = "paused"
    RESUMED = "resumed"
    DISABLED = "disabled"
    EMERGENCY_STOP = "emergency_stop"
    SHUTDOWN = "shutdown"
    TARGET_ACCEPTED = "target_accepted"
    TARGET_CLEARED = "target_cleared"


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MouseValidationError(f"{name} must be a non-negative integer.")
    return value


@dataclass(frozen=True, slots=True)
class CursorTarget:
    """A camera-space cursor target; it has no display or OS coordinates."""

    x: float
    y: float
    timestamp_ms: int
    source_index: int

    def __post_init__(self) -> None:
        for name in ("x", "y"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or not 0.0 <= value <= 1.0
            ):
                raise MouseValidationError(
                    f"{name} must be a finite real number in [0.0, 1.0]."
                )
            object.__setattr__(self, name, float(value))
        _non_negative_int(self.timestamp_ms, "timestamp_ms")
        _non_negative_int(self.source_index, "source_index")


@dataclass(frozen=True, slots=True)
class MouseEvent:
    """A sequenced internal event, never an operating-system input command."""

    sequence: int
    timestamp_ms: int
    kind: MouseEventKind
    mode: MouseMode
    reason: MouseReason
    target: CursorTarget | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1 or isinstance(self.sequence, bool):
            raise MouseValidationError("sequence must be a positive integer.")
        _non_negative_int(self.timestamp_ms, "timestamp_ms")
        if not isinstance(self.kind, MouseEventKind):
            raise MouseValidationError("kind must be a MouseEventKind.")
        if not isinstance(self.mode, MouseMode):
            raise MouseValidationError("mode must be a MouseMode.")
        if not isinstance(self.reason, MouseReason):
            raise MouseValidationError("reason must be a MouseReason.")
        if self.kind is MouseEventKind.CURSOR_TARGET_ACCEPTED:
            if not isinstance(self.target, CursorTarget):
                raise MouseValidationError("accepted cursor events require a target.")
        elif self.target is not None:
            raise MouseValidationError(
                "only accepted cursor events may include a target."
            )


@dataclass(frozen=True, slots=True)
class MouseSnapshot:
    """Immutable current service state with no mutable event history."""

    mode: MouseMode
    current_target: CursorTarget | None
    last_emitted_sequence: int
    enabled: bool
    tracking_active: bool
    closed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.mode, MouseMode):
            raise MouseValidationError("mode must be a MouseMode.")
        _non_negative_int(self.last_emitted_sequence, "last_emitted_sequence")
        if self.current_target is not None and not isinstance(
            self.current_target, CursorTarget
        ):
            raise MouseValidationError("current_target must be a CursorTarget or None.")
