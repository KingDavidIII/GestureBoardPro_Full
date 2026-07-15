"""Explicit lifecycle reducer for the transport-neutral gesture mouse."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import MouseLifecycleError, MouseMode, MouseReason


class MouseCommand(StrEnum):
    ENABLE = "enable"
    TRACKING_ACQUIRED = "tracking_acquired"
    TRACKING_LOST = "tracking_lost"
    PAUSE = "pause"
    RESUME = "resume"
    DISABLE = "disable"
    EMERGENCY_STOP = "emergency_stop"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class MouseTransition:
    mode: MouseMode
    changed: bool
    clear_target: bool
    safety_reset: bool
    reason: MouseReason


class MouseStateMachine:
    """Small stateful reducer; callers own locking and event delivery."""

    def __init__(self) -> None:
        self._mode = MouseMode.DISABLED

    @property
    def mode(self) -> MouseMode:
        return self._mode

    def apply(self, command: MouseCommand) -> MouseTransition:
        if not isinstance(command, MouseCommand):
            raise TypeError("command must be a MouseCommand.")
        if self._mode is MouseMode.CLOSED and command is not MouseCommand.SHUTDOWN:
            raise MouseLifecycleError("gesture mouse has been shut down.")

        previous = self._mode
        if command is MouseCommand.ENABLE:
            next_mode = MouseMode.READY if previous is MouseMode.DISABLED else previous
            transition = MouseTransition(
                next_mode,
                next_mode is not previous,
                False,
                False,
                MouseReason.ENABLED,
            )
        elif command is MouseCommand.TRACKING_ACQUIRED:
            next_mode = MouseMode.ACTIVE if previous is MouseMode.READY else previous
            transition = MouseTransition(
                next_mode,
                next_mode is not previous,
                False,
                False,
                MouseReason.TRACKING_ACQUIRED,
            )
        elif command is MouseCommand.TRACKING_LOST:
            next_mode = MouseMode.READY if previous is MouseMode.ACTIVE else previous
            transition = MouseTransition(
                next_mode,
                next_mode is not previous,
                previous is MouseMode.ACTIVE,
                previous is MouseMode.ACTIVE,
                MouseReason.TRACKING_LOST,
            )
        elif command is MouseCommand.PAUSE:
            next_mode = (
                MouseMode.PAUSED
                if previous in {MouseMode.READY, MouseMode.ACTIVE}
                else previous
            )
            transition = MouseTransition(
                next_mode,
                next_mode is not previous,
                previous is MouseMode.ACTIVE,
                next_mode is MouseMode.PAUSED and next_mode is not previous,
                MouseReason.PAUSED,
            )
        elif command is MouseCommand.RESUME:
            next_mode = MouseMode.READY if previous is MouseMode.PAUSED else previous
            transition = MouseTransition(
                next_mode,
                next_mode is not previous,
                False,
                False,
                MouseReason.RESUMED,
            )
        elif command is MouseCommand.DISABLE:
            next_mode = (
                MouseMode.DISABLED
                if previous in {MouseMode.READY, MouseMode.ACTIVE, MouseMode.PAUSED}
                else previous
            )
            transition = MouseTransition(
                next_mode,
                next_mode is not previous,
                previous is MouseMode.ACTIVE,
                next_mode is not previous,
                MouseReason.DISABLED,
            )
        elif command is MouseCommand.EMERGENCY_STOP:
            transition = MouseTransition(
                MouseMode.DISABLED,
                previous is not MouseMode.DISABLED,
                previous is MouseMode.ACTIVE,
                True,
                MouseReason.EMERGENCY_STOP,
            )
        else:
            transition = MouseTransition(
                MouseMode.CLOSED,
                previous is not MouseMode.CLOSED,
                previous is MouseMode.ACTIVE,
                previous is not MouseMode.CLOSED,
                MouseReason.SHUTDOWN,
            )
        self._mode = transition.mode
        return transition
