"""Safe event-only gesture-mouse service with no operating-system effects."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from time import monotonic
from typing import Protocol

from .models import (
    CursorTarget,
    MouseEvent,
    MouseEventKind,
    MouseLifecycleError,
    MouseMode,
    MouseOutputError,
    MouseReason,
    MouseSnapshot,
    MouseValidationError,
)
from .state_machine import MouseCommand, MouseStateMachine, MouseTransition


class MouseOutputPort(Protocol):
    """Receives internal events only; it has no cursor or button API."""

    def emit(self, event: MouseEvent) -> None: ...

    def close(self) -> None: ...


class NullMouseOutputPort:
    """Production-safe default: records nothing and produces no OS input."""

    def __init__(self) -> None:
        self._closed = False

    def emit(self, event: MouseEvent) -> None:
        if self._closed:
            raise MouseOutputError("mouse output port has been closed.")
        del event

    def close(self) -> None:
        self._closed = True


class GestureMouseService:
    """Own mouse mode and camera-space targets without runtime integration."""

    def __init__(
        self,
        output_port: MouseOutputPort | None = None,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._output_port = output_port or NullMouseOutputPort()
        self._clock = clock or (lambda: int(monotonic() * 1000))
        self._state_machine = MouseStateMachine()
        self._current_target: CursorTarget | None = None
        self._next_sequence = 1
        self._last_emitted_sequence = 0
        self._output_closed = False
        self._lock = RLock()

    def snapshot(self) -> MouseSnapshot:
        with self._lock:
            mode = self._state_machine.mode
            return MouseSnapshot(
                mode=mode,
                current_target=self._current_target,
                last_emitted_sequence=self._last_emitted_sequence,
                enabled=mode in {MouseMode.READY, MouseMode.ACTIVE, MouseMode.PAUSED},
                tracking_active=mode is MouseMode.ACTIVE,
                closed=mode is MouseMode.CLOSED,
            )

    def enable(self, *, timestamp_ms: int | None = None) -> bool:
        return self._apply(MouseCommand.ENABLE, timestamp_ms=timestamp_ms)

    def tracking_acquired(self, *, timestamp_ms: int | None = None) -> bool:
        return self._apply(MouseCommand.TRACKING_ACQUIRED, timestamp_ms=timestamp_ms)

    def tracking_lost(self, *, timestamp_ms: int | None = None) -> bool:
        return self._apply(MouseCommand.TRACKING_LOST, timestamp_ms=timestamp_ms)

    def pause(self, *, timestamp_ms: int | None = None) -> bool:
        return self._apply(MouseCommand.PAUSE, timestamp_ms=timestamp_ms)

    def resume(self, *, timestamp_ms: int | None = None) -> bool:
        return self._apply(MouseCommand.RESUME, timestamp_ms=timestamp_ms)

    def disable(self, *, timestamp_ms: int | None = None) -> bool:
        return self._apply(MouseCommand.DISABLE, timestamp_ms=timestamp_ms)

    def emergency_stop(self, *, timestamp_ms: int | None = None) -> bool:
        return self._apply(MouseCommand.EMERGENCY_STOP, timestamp_ms=timestamp_ms)

    def shutdown(self, *, timestamp_ms: int | None = None) -> bool:
        with self._lock:
            transition = self._state_machine.apply(MouseCommand.SHUTDOWN)
            if not transition.changed:
                return False
            self._clear_target(transition, timestamp_ms)
            try:
                self._emit(MouseEventKind.MODE_CHANGED, transition.reason, timestamp_ms)
                self._emit(
                    MouseEventKind.SAFETY_RESET_REQUESTED,
                    transition.reason,
                    timestamp_ms,
                )
            finally:
                self._close_output()
            return True

    def submit_target(
        self, target: CursorTarget, *, timestamp_ms: int | None = None
    ) -> bool:
        if not isinstance(target, CursorTarget):
            raise MouseValidationError("target must be a CursorTarget.")
        with self._lock:
            self._ensure_open("submit a cursor target")
            if self._state_machine.mode is not MouseMode.ACTIVE:
                return False
            try:
                self._emit(
                    MouseEventKind.CURSOR_TARGET_ACCEPTED,
                    MouseReason.TARGET_ACCEPTED,
                    timestamp_ms if timestamp_ms is not None else target.timestamp_ms,
                    target,
                )
            except MouseOutputError:
                self._current_target = None
                raise
            self._current_target = target
            return True

    def _apply(self, command: MouseCommand, *, timestamp_ms: int | None) -> bool:
        with self._lock:
            self._ensure_open(command.value)
            transition = self._state_machine.apply(command)
            if not transition.changed and not transition.safety_reset:
                return False
            self._clear_target(transition, timestamp_ms)
            if transition.changed:
                self._emit(MouseEventKind.MODE_CHANGED, transition.reason, timestamp_ms)
            if transition.safety_reset:
                self._emit(
                    MouseEventKind.SAFETY_RESET_REQUESTED,
                    transition.reason,
                    timestamp_ms,
                )
            return transition.changed

    def _clear_target(
        self, transition: MouseTransition, timestamp_ms: int | None
    ) -> None:
        if transition.clear_target and self._current_target is not None:
            self._current_target = None
            self._emit(
                MouseEventKind.CURSOR_TARGET_CLEARED,
                MouseReason.TARGET_CLEARED,
                timestamp_ms,
            )

    def _emit(
        self,
        kind: MouseEventKind,
        reason: MouseReason,
        timestamp_ms: int | None,
        target: CursorTarget | None = None,
    ) -> None:
        timestamp = self._event_timestamp(timestamp_ms)
        event = MouseEvent(
            self._next_sequence,
            timestamp,
            kind,
            self._state_machine.mode,
            reason,
            target,
        )
        self._next_sequence += 1
        try:
            self._output_port.emit(event)
        except MouseOutputError:
            raise
        except Exception as error:
            raise MouseOutputError(
                "mouse output port failed to receive an event."
            ) from error
        self._last_emitted_sequence = event.sequence

    def _close_output(self) -> None:
        if self._output_closed:
            return
        self._output_closed = True
        try:
            self._output_port.close()
        except MouseOutputError:
            raise
        except Exception as error:
            raise MouseOutputError("mouse output port failed to close.") from error

    def _event_timestamp(self, explicit: int | None) -> int:
        value = self._clock() if explicit is None else explicit
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MouseValidationError(
                "event timestamp_ms must be a non-negative integer."
            )
        return value

    def _ensure_open(self, operation: str) -> None:
        if self._state_machine.mode is MouseMode.CLOSED:
            raise MouseLifecycleError(
                f"cannot {operation} after gesture mouse shutdown."
            )
