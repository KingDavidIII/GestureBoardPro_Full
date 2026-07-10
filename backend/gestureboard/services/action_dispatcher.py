"""Translate gesture predictions into explicitly configured keyboard actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType, TracebackType

from .gesture_classifier import GestureLabel, GesturePrediction
from .keyboard_controller import (
    KeyboardAction,
    KeyboardController,
    KeyboardControllerError,
    KeyboardExecutionResult,
)


class ActionDispatcherError(RuntimeError):
    """Raised when a configured gesture action cannot be dispatched."""


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Outcome of translating one gesture into at most one keyboard action."""

    gesture_label: GestureLabel
    action: KeyboardAction | None
    execution_result: KeyboardExecutionResult | None

    @property
    def executed(self) -> bool:
        return self.execution_result is not None


class ActionDispatcher:
    """Dispatch immutable gesture-to-action mappings through a controller."""

    def __init__(
        self,
        actions: Mapping[GestureLabel, KeyboardAction] | None = None,
        controller: KeyboardController | None = None,
    ) -> None:
        copied_actions = dict(actions or {})
        for label, action in copied_actions.items():
            if not isinstance(label, GestureLabel):
                raise ActionDispatcherError(
                    "Action mapping keys must be GestureLabel values."
                )
            if not isinstance(action, KeyboardAction):
                raise ActionDispatcherError(
                    "Action mapping values must be KeyboardAction values."
                )
        self.actions = MappingProxyType(copied_actions)
        self.controller = controller if controller is not None else KeyboardController()
        self._owns_controller = controller is None
        self._closed = False

    def dispatch(self, gesture: GesturePrediction | GestureLabel) -> DispatchResult:
        """Execute the single configured action for a prediction or label."""

        if self._closed:
            raise ActionDispatcherError("Action dispatcher has been closed.")
        label = self._gesture_label(gesture)
        action = self.actions.get(label)
        if label is GestureLabel.UNKNOWN or action is None:
            return DispatchResult(
                gesture_label=label,
                action=None,
                execution_result=None,
            )

        try:
            execution_result = self.controller.execute(action)
        except KeyboardControllerError as error:
            raise ActionDispatcherError(
                f"Failed to dispatch {label.value} using action {action!r}."
            ) from error
        return DispatchResult(
            gesture_label=label,
            action=action,
            execution_result=execution_result,
        )

    @staticmethod
    def _gesture_label(gesture: GesturePrediction | GestureLabel) -> GestureLabel:
        label = (
            gesture
            if isinstance(gesture, GestureLabel)
            else getattr(gesture, "label", None)
        )
        if not isinstance(label, GestureLabel):
            raise ActionDispatcherError(
                "dispatch() requires a GestureLabel or GesturePrediction."
            )
        return label

    def close(self) -> None:
        """Close an internally created controller, at most once."""

        if self._closed:
            return
        self._closed = True
        if self._owns_controller:
            try:
                self.controller.close()
            except KeyboardControllerError as error:
                raise ActionDispatcherError(
                    "Failed to close the owned keyboard controller."
                ) from error

    def __enter__(self) -> ActionDispatcher:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
