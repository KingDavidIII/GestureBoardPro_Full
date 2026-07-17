"""Optional coordinator for the already-selected recognition hand."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from gestureboard.recognition.models import GestureId
from gestureboard.recognition.observations import HandObservation, HandSelection

from .button_output import MouseButtonOutputPort, NullMouseButtonOutput
from .buttons import (
    MouseButton,
    MouseButtonActionKind,
    MouseButtonController,
    MouseButtonDecision,
    MouseButtonPolicy,
    detect_button_intent,
)
from .mapping import VirtualCursorMapper, VirtualCursorMappingResult, VirtualSurface
from .models import MouseLifecycleError, MouseOutputError, MouseValidationError
from .output import NullVirtualCursorOutput, VirtualCursorOutputPort
from .ownership import WindowsCursorOwnershipLease
from .service import GestureMouseService
from .tracking import cursor_target_from_selected_hand


@dataclass(frozen=True, slots=True)
class GestureMouseRuntimeResult:
    mapping: VirtualCursorMappingResult | None
    moved: bool
    rate_limited: bool
    button_decision: MouseButtonDecision | None = None


class GestureMouseRuntimeCoordinator:
    """Coordinates one preselected hand without decoding or recognising frames."""

    def __init__(
        self,
        owner_id: str,
        *,
        enabled: bool = False,
        mapper: VirtualCursorMapper | None = None,
        output: VirtualCursorOutputPort | None = None,
        max_output_hz: float = 60.0,
        windows_lease: WindowsCursorOwnershipLease | None = None,
        button_policy: MouseButtonPolicy | None = None,
        button_output: MouseButtonOutputPort | None = None,
    ) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise MouseValidationError("owner_id must be a non-blank string.")
        if (
            isinstance(max_output_hz, bool)
            or not isinstance(max_output_hz, (int, float))
            or max_output_hz <= 0
        ):
            raise MouseValidationError("max_output_hz must be positive.")
        self._owner_id = owner_id
        self._enabled = enabled
        self._service = GestureMouseService()
        self._mapper = (
            mapper
            if mapper is not None
            else VirtualCursorMapper(VirtualSurface(1280, 720))
        )
        self._output = output if output is not None else NullVirtualCursorOutput()
        self._max_output_hz = max_output_hz
        self._lease = windows_lease
        self._button_policy = (
            button_policy if button_policy is not None else MouseButtonPolicy()
        )
        self._button_controller = MouseButtonController(self._button_policy)
        self._button_output = (
            button_output if button_output is not None else NullMouseButtonOutput()
        )
        self._last_button_decision: MouseButtonDecision | None = None
        self._last_output_ms: float | None = None
        self._last_accepted_timestamp_ms = 0
        self._closed = False
        self._lock = RLock()
        if enabled:
            self._service.enable(timestamp_ms=0)

    def process(
        self,
        selected: HandSelection | HandObservation | None,
        *,
        timestamp_ms: int,
        stable_gesture: GestureId | None = None,
    ) -> GestureMouseRuntimeResult:
        with self._lock:
            if self._closed:
                raise MouseLifecycleError("gesture mouse coordinator is closed.")
            self._accept_process_timestamp(timestamp_ms)
            if not self._enabled:
                return GestureMouseRuntimeResult(None, False, False)
            target = cursor_target_from_selected_hand(
                selected, timestamp_ms=timestamp_ms
            )
            if target is not None and self._lease is not None:
                if not self._lease.acquire(self._owner_id):
                    return GestureMouseRuntimeResult(None, False, False)
            button_decision = self._process_buttons(
                selected, timestamp_ms, stable_gesture
            )
            if target is None:
                self._service.tracking_lost(timestamp_ms=timestamp_ms)
                self._mapper.reset()
                self._last_output_ms = None
                if self._lease is not None:
                    self._lease.release(self._owner_id)
                return GestureMouseRuntimeResult(None, False, False, button_decision)
            self._service.tracking_acquired(timestamp_ms=timestamp_ms)
            if not self._service.submit_target(target):
                return GestureMouseRuntimeResult(None, False, False, button_decision)
            mapping = self._mapper.map(target)
            if not mapping.emitted:
                return GestureMouseRuntimeResult(mapping, False, False, button_decision)
            if mapping.source_reset:
                self._last_output_ms = None
            minimum_interval = 1000 / self._max_output_hz
            if (
                self._last_output_ms is not None
                and timestamp_ms - self._last_output_ms + 1e-9 < minimum_interval
            ):
                return GestureMouseRuntimeResult(mapping, False, True, button_decision)
            try:
                self._output.move(mapping.virtual_target)
            except MouseOutputError:
                self._fail_closed_after_output_error(timestamp_ms)
                raise
            self._last_output_ms = timestamp_ms
            return GestureMouseRuntimeResult(mapping, True, False, button_decision)

    def _process_buttons(
        self,
        selected: HandSelection | HandObservation | None,
        timestamp_ms: int,
        stable_gesture: GestureId | None,
    ) -> MouseButtonDecision | None:
        if not self._button_policy.buttons_enabled:
            return None
        if stable_gesture is not GestureId.POINT:
            decision = self._button_controller.emergency_stop(timestamp_ms=timestamp_ms)
            return self._finalize_button_decision(decision, timestamp_ms)
        hand = (
            selected.primary_hand if isinstance(selected, HandSelection) else selected
        )
        if not isinstance(hand, HandObservation):
            decision = self._button_controller.reset(timestamp_ms=timestamp_ms)
        elif (
            isinstance(hand.source_index, bool)
            or not isinstance(hand.source_index, int)
            or hand.source_index < 0
        ):
            decision = self._button_controller.emergency_stop(timestamp_ms=timestamp_ms)
        else:
            previous = (
                self._last_button_decision.intent
                if self._last_button_decision is not None
                else None
            )
            intent = detect_button_intent(hand, self._button_policy, previous)
            decision = self._button_controller.process(
                intent, timestamp_ms=timestamp_ms, source_index=hand.source_index
            )
        return self._finalize_button_decision(decision, timestamp_ms)

    def _finalize_button_decision(
        self, decision: MouseButtonDecision, timestamp_ms: int
    ) -> MouseButtonDecision:
        self._last_button_decision = decision
        try:
            self._apply_button_action(decision)
        except MouseOutputError:
            self._fail_closed_after_output_error(timestamp_ms)
            raise
        return decision

    def _fail_closed_after_output_error(self, timestamp_ms: int) -> None:
        """Best-effort cleanup which never replaces the originating output error."""

        for operation in (
            self._button_output.release_all,
            lambda: self._button_controller.emergency_stop(timestamp_ms=timestamp_ms),
            lambda: self._service.emergency_stop(timestamp_ms=timestamp_ms),
            self._mapper.reset,
            lambda: (
                self._lease.release(self._owner_id) if self._lease is not None else None
            ),
        ):
            if operation is None:
                continue
            try:
                result = operation()
                if isinstance(result, MouseButtonDecision):
                    self._last_button_decision = result
            except Exception:
                pass
        self._last_output_ms = None
        self._enabled = False

    def _apply_button_action(self, decision: MouseButtonDecision) -> None:
        action = decision.action
        if action is MouseButtonActionKind.PRIMARY_CLICK:
            self._button_output.button_down(MouseButton.PRIMARY)
            self._button_output.button_up(MouseButton.PRIMARY)
        elif action is MouseButtonActionKind.SECONDARY_CLICK:
            self._button_output.button_down(MouseButton.SECONDARY)
            self._button_output.button_up(MouseButton.SECONDARY)
        elif action is MouseButtonActionKind.PRIMARY_DOWN:
            self._button_output.button_down(MouseButton.PRIMARY)
        elif action is MouseButtonActionKind.PRIMARY_UP:
            self._button_output.button_up(MouseButton.PRIMARY)

    def emergency_stop(self, *, timestamp_ms: int | None = None) -> None:
        with self._lock:
            failures: list[Exception] = []
            try:
                resolved_timestamp = self._resolve_lifecycle_timestamp(timestamp_ms)
            except Exception as error:
                failures.append(error)
                resolved_timestamp = self._last_accepted_timestamp_ms
            decision = self._attempt_button_controller(
                failures, resolved_timestamp, emergency=True
            )
            if decision is not None:
                self._attempt(failures, lambda: self._apply_button_action(decision))
            self._attempt(failures, self._button_output.release_all)
            self._attempt(
                failures,
                lambda: self._service.emergency_stop(timestamp_ms=resolved_timestamp),
            )
            self._enabled = False
            self._attempt(failures, self._mapper.reset)
            self._last_output_ms = None
            if self._lease is not None:
                self._attempt(failures, lambda: self._lease.release(self._owner_id))
            if failures:
                raise failures[0]

    def tracking_lost(self, *, timestamp_ms: int | None = None) -> None:
        """Clear transient tracking state while leaving configured enablement intact."""

        with self._lock:
            if self._closed:
                return
            failures: list[Exception] = []
            try:
                resolved_timestamp = self._resolve_lifecycle_timestamp(timestamp_ms)
            except Exception as error:
                failures.append(error)
                resolved_timestamp = self._last_accepted_timestamp_ms
            decision = self._attempt_button_controller(
                failures, resolved_timestamp, emergency=False
            )
            if decision is not None:
                self._attempt(failures, lambda: self._apply_button_action(decision))
            self._attempt(failures, self._button_output.release_all)
            self._attempt(
                failures,
                lambda: self._service.tracking_lost(timestamp_ms=resolved_timestamp),
            )
            self._attempt(failures, self._mapper.reset)
            self._last_output_ms = None
            if self._lease is not None:
                self._attempt(failures, lambda: self._lease.release(self._owner_id))
            if failures:
                raise failures[0]

    def _attempt_button_controller(
        self, failures: list[Exception], timestamp_ms: int, *, emergency: bool
    ) -> MouseButtonDecision | None:
        if not self._button_policy.buttons_enabled:
            return None
        try:
            decision = (
                self._button_controller.emergency_stop(timestamp_ms=timestamp_ms)
                if emergency
                else self._button_controller.reset(timestamp_ms=timestamp_ms)
            )
        except Exception as error:
            failures.append(error)
            return None
        self._last_button_decision = decision
        return decision

    @staticmethod
    def _attempt(failures: list[Exception], operation: Callable[[], object]) -> None:
        try:
            operation()
        except Exception as error:
            failures.append(error)

    def _accept_process_timestamp(self, timestamp_ms: int) -> None:
        if (
            isinstance(timestamp_ms, bool)
            or not isinstance(timestamp_ms, int)
            or timestamp_ms < self._last_accepted_timestamp_ms
        ):
            raise MouseValidationError(
                "timestamp_ms must be a monotonic non-negative integer."
            )
        self._last_accepted_timestamp_ms = timestamp_ms

    def _resolve_lifecycle_timestamp(self, timestamp_ms: int | None) -> int:
        if timestamp_ms is None:
            return self._last_accepted_timestamp_ms
        self._accept_process_timestamp(timestamp_ms)
        return timestamp_ms

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def close(self, *, timestamp_ms: int | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            failures: list[Exception] = []
            try:
                resolved_timestamp = self._resolve_lifecycle_timestamp(timestamp_ms)
            except Exception as error:
                failures.append(error)
                resolved_timestamp = self._last_accepted_timestamp_ms
            if self._button_policy.buttons_enabled:
                try:
                    decision = self._button_controller.shutdown(
                        timestamp_ms=resolved_timestamp
                    )
                    self._last_button_decision = decision
                    self._apply_button_action(decision)
                except Exception as error:
                    failures.append(error)
            for operation in (
                self._button_output.release_all,
                self._button_output.close,
                lambda: self._service.shutdown(timestamp_ms=resolved_timestamp),
                self._output.close,
                lambda: (
                    self._lease.release(self._owner_id)
                    if self._lease is not None
                    else None
                ),
            ):
                if operation is None:
                    continue
                try:
                    operation()
                except Exception as error:
                    failures.append(error)
            self._closed = True
            if failures:
                raise failures[0]
