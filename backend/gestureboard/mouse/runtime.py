"""Optional coordinator for the already-selected recognition hand."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from gestureboard.recognition.observations import HandObservation, HandSelection

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
        self._mapper = mapper or VirtualCursorMapper(VirtualSurface(1280, 720))
        self._output = output or NullVirtualCursorOutput()
        self._max_output_hz = max_output_hz
        self._lease = windows_lease
        self._last_output_ms: float | None = None
        self._closed = False
        self._lock = RLock()
        if enabled:
            self._service.enable(timestamp_ms=0)

    def process(
        self, selected: HandSelection | HandObservation | None, *, timestamp_ms: int
    ) -> GestureMouseRuntimeResult:
        with self._lock:
            if self._closed:
                raise MouseLifecycleError("gesture mouse coordinator is closed.")
            if not self._enabled:
                return GestureMouseRuntimeResult(None, False, False)
            target = cursor_target_from_selected_hand(
                selected, timestamp_ms=timestamp_ms
            )
            if target is None:
                self._service.tracking_lost(timestamp_ms=timestamp_ms)
                self._mapper.reset()
                self._last_output_ms = None
                return GestureMouseRuntimeResult(None, False, False)
            self._service.tracking_acquired(timestamp_ms=timestamp_ms)
            if not self._service.submit_target(target):
                return GestureMouseRuntimeResult(None, False, False)
            mapping = self._mapper.map(target)
            if not mapping.emitted:
                return GestureMouseRuntimeResult(mapping, False, False)
            if mapping.source_reset:
                self._last_output_ms = None
            if self._lease is not None and not self._lease.acquire(self._owner_id):
                return GestureMouseRuntimeResult(mapping, False, False)
            minimum_interval = 1000 / self._max_output_hz
            if (
                self._last_output_ms is not None
                and timestamp_ms - self._last_output_ms + 1e-9 < minimum_interval
            ):
                return GestureMouseRuntimeResult(mapping, False, True)
            try:
                self._output.move(mapping.virtual_target)
            except MouseOutputError:
                self.emergency_stop(timestamp_ms=timestamp_ms)
                raise
            self._last_output_ms = timestamp_ms
            return GestureMouseRuntimeResult(mapping, True, False)

    def emergency_stop(self, *, timestamp_ms: int) -> None:
        with self._lock:
            self._service.emergency_stop(timestamp_ms=timestamp_ms)
            self._enabled = False
            self._mapper.reset()
            self._last_output_ms = None
            if self._lease is not None:
                self._lease.release(self._owner_id)

    def tracking_lost(self, *, timestamp_ms: int) -> None:
        """Clear transient tracking state while leaving configured enablement intact."""

        with self._lock:
            if self._closed:
                return
            self._service.tracking_lost(timestamp_ms=timestamp_ms)
            self._mapper.reset()
            self._last_output_ms = None
            if self._lease is not None:
                self._lease.release(self._owner_id)

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.emergency_stop(timestamp_ms=0)
            self._service.shutdown(timestamp_ms=0)
            self._output.close()
