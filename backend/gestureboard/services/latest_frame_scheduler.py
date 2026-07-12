"""Bounded latest-frame scheduling for synchronous frame processors."""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

ResultT = TypeVar("ResultT")
ResultCallback = Callable[[ResultT, "FrameSchedulerMetrics"], Awaitable[None] | None]
ErrorCallback = Callable[[Exception, "FrameSchedulerMetrics"], Awaitable[None] | None]
Offload = Callable[[Callable[[bytes], ResultT], bytes], Awaitable[ResultT]]


class LatestFrameSchedulerError(RuntimeError):
    """Raised when the scheduler lifecycle or submission contract is violated."""


@dataclass(frozen=True, slots=True)
class FrameSchedulerMetrics:
    """Immutable connection-local scheduler counters and latest attempt timings."""

    received_frames: int = 0
    processed_frames: int = 0
    dropped_frames: int = 0
    processing_failures: int = 0
    pending_frames: int = 0
    queue_delay_ms: float = 0.0
    processing_time_ms: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "received_frames",
            "processed_frames",
            "dropped_frames",
            "processing_failures",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise LatestFrameSchedulerError(
                    f"{name} must be a non-negative integer."
                )
        if self.pending_frames not in (0, 1):
            raise LatestFrameSchedulerError("pending_frames must be 0 or 1.")
        for name in ("queue_delay_ms", "processing_time_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise LatestFrameSchedulerError(
                    f"{name} must be finite and non-negative."
                )
            if not math.isfinite(value) or value < 0:
                raise LatestFrameSchedulerError(
                    f"{name} must be finite and non-negative."
                )


@dataclass(frozen=True, slots=True)
class _PendingFrame:
    payload: bytes
    submitted_at: float


async def _to_thread(processor: Callable[[bytes], ResultT], payload: bytes) -> ResultT:
    return await asyncio.to_thread(processor, payload)


class LatestFrameScheduler(Generic[ResultT]):
    """Process one frame at a time while retaining only the newest pending frame.

    ``processed_frames`` counts every processor attempt that returns or raises.
    ``dropped_frames`` counts pending frames replaced by newer submissions.
    Timings describe the latest completed attempt and use a monotonic clock.
    """

    def __init__(
        self,
        processor: Callable[[bytes], ResultT],
        on_result: ResultCallback[ResultT],
        on_error: ErrorCallback,
        *,
        clock: Callable[[], float] = time.monotonic,
        offload: Offload[ResultT] = _to_thread,
    ) -> None:
        if not callable(processor) or not callable(on_result) or not callable(on_error):
            raise LatestFrameSchedulerError("Processor and callbacks must be callable.")
        if not callable(clock) or not callable(offload):
            raise LatestFrameSchedulerError(
                "Clock and offload adapter must be callable."
            )
        self._processor = processor
        self._on_result = on_result
        self._on_error = on_error
        self._clock = clock
        self._offload = offload
        self._pending: _PendingFrame | None = None
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False
        self._received = 0
        self._processed = 0
        self._dropped = 0
        self._failures = 0
        self._queue_delay_ms = 0.0
        self._processing_time_ms = 0.0

    @property
    def metrics(self) -> FrameSchedulerMetrics:
        return self._snapshot()

    def start(self) -> None:
        if self._closed:
            raise LatestFrameSchedulerError("Cannot start a closed scheduler.")
        if self._started:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError as error:
            raise LatestFrameSchedulerError(
                "Scheduler must be started from a running event loop."
            ) from error
        self._started = True
        self._worker = asyncio.create_task(self._run(), name="latest-frame-worker")

    def submit(self, payload: bytes) -> FrameSchedulerMetrics:
        if self._closed:
            raise LatestFrameSchedulerError("Cannot submit to a closed scheduler.")
        if not self._started:
            raise LatestFrameSchedulerError(
                "Scheduler must be started before submission."
            )
        if not isinstance(payload, bytes):
            raise LatestFrameSchedulerError("Frame payload must be immutable bytes.")
        self._received += 1
        if self._pending is not None:
            self._dropped += 1
        self._pending = _PendingFrame(bytes(payload), self._now())
        self._wake.set()
        return self._snapshot()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending = None
        self._wake.set()
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        try:
            while not self._closed:
                await self._wake.wait()
                self._wake.clear()
                if self._closed:
                    return
                pending = self._pending
                self._pending = None
                if pending is None:
                    continue
                started_at = self._now()
                queue_delay = max(0.0, (started_at - pending.submitted_at) * 1000.0)
                try:
                    result = await self._offload(self._processor, pending.payload)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._complete(started_at, queue_delay, failed=True)
                    if not self._closed:
                        await self._notify(self._on_error, error, self._snapshot())
                else:
                    self._complete(started_at, queue_delay, failed=False)
                    if not self._closed:
                        await self._notify(self._on_result, result, self._snapshot())
                if self._pending is not None:
                    self._wake.set()
        except asyncio.CancelledError:
            raise

    def _complete(self, started_at: float, queue_delay: float, *, failed: bool) -> None:
        self._processed += 1
        if failed:
            self._failures += 1
        self._queue_delay_ms = queue_delay
        self._processing_time_ms = max(0.0, (self._now() - started_at) * 1000.0)

    async def _notify(self, callback: Callable[..., object], *args: object) -> None:
        try:
            value = callback(*args)
            if inspect.isawaitable(value):
                await value
        except asyncio.CancelledError:
            raise
        except Exception:
            # Transport callback failures are isolated from mailbox processing.
            return

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise LatestFrameSchedulerError("Clock must return a finite number.")
        value = float(value)
        if not math.isfinite(value):
            raise LatestFrameSchedulerError("Clock must return a finite number.")
        return value

    def _snapshot(self) -> FrameSchedulerMetrics:
        return FrameSchedulerMetrics(
            received_frames=self._received,
            processed_frames=self._processed,
            dropped_frames=self._dropped,
            processing_failures=self._failures,
            pending_frames=int(self._pending is not None),
            queue_delay_ms=self._queue_delay_ms,
            processing_time_ms=self._processing_time_ms,
        )
