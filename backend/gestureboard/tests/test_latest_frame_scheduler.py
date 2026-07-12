"""Deterministic tests for the bounded latest-frame scheduler."""

import asyncio
from dataclasses import FrozenInstanceError

from django.test import SimpleTestCase

from gestureboard.services.latest_frame_scheduler import (
    FrameSchedulerMetrics,
    LatestFrameScheduler,
    LatestFrameSchedulerError,
)


class ControlledOffload:
    def __init__(self) -> None:
        self.started: asyncio.Queue[tuple[object, bytes, asyncio.Future[object]]] = (
            asyncio.Queue()
        )
        self.active = 0
        self.maximum_active = 0

    async def __call__(self, processor, payload):
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        future = asyncio.get_running_loop().create_future()
        await self.started.put((processor, payload, future))
        try:
            value = await future
            if isinstance(value, Exception):
                raise value
            return processor(value)
        finally:
            self.active -= 1


async def setup_scheduler(*, clock=lambda: 0.0):
    results = []
    errors = []
    offload = ControlledOffload()
    scheduler = LatestFrameScheduler(
        lambda payload: payload,
        lambda result, metrics: results.append((result, metrics)),
        lambda error, metrics: errors.append((error, metrics)),
        clock=clock,
        offload=offload,
    )
    scheduler.start()
    return scheduler, offload, results, errors


class LatestFrameSchedulerTests(SimpleTestCase):
    async def test_one_submitted_frame_is_processed(self) -> None:
        scheduler, offload, results, _ = await setup_scheduler()
        scheduler.submit(b"one")
        _, _, completion = await offload.started.get()
        completion.set_result(b"one")
        await asyncio.sleep(0)
        self.assertEqual(results[0][0], b"one")
        await scheduler.close()

    async def test_start_is_idempotent_and_only_one_worker_processes(self) -> None:
        scheduler, offload, _, _ = await setup_scheduler()
        scheduler.start()
        scheduler.start()
        scheduler.submit(b"one")
        _, _, completion = await offload.started.get()
        completion.set_result(b"one")
        await asyncio.sleep(0)
        self.assertEqual(offload.maximum_active, 1)
        await scheduler.close()

    async def test_processing_is_strictly_sequential(self) -> None:
        scheduler, offload, _, _ = await setup_scheduler()
        scheduler.submit(b"one")
        _, _, first = await offload.started.get()
        scheduler.submit(b"two")
        self.assertEqual(offload.maximum_active, 1)
        first.set_result(b"one")
        await offload.started.get()
        self.assertEqual(offload.maximum_active, 1)
        await scheduler.close()

    async def test_pending_frame_is_retained_during_processing(self) -> None:
        scheduler, offload, _, _ = await setup_scheduler()
        scheduler.submit(b"one")
        await offload.started.get()
        scheduler.submit(b"two")
        self.assertEqual(scheduler.metrics.pending_frames, 1)
        await scheduler.close()

    async def test_newest_frame_replaces_pending_frame(self) -> None:
        scheduler, offload, results, _ = await setup_scheduler()
        scheduler.submit(b"one")
        _, _, first = await offload.started.get()
        scheduler.submit(b"two")
        scheduler.submit(b"three")
        first.set_result(b"one")
        _, payload, last = await offload.started.get()
        self.assertEqual(payload, b"three")
        last.set_result(b"three")
        await asyncio.sleep(0)
        self.assertEqual([item[0] for item in results], [b"one", b"three"])
        await scheduler.close()

    async def test_one_then_four_processes_only_one_and_four(self) -> None:
        scheduler, offload, results, _ = await setup_scheduler()
        scheduler.submit(b"1")
        _, _, first = await offload.started.get()
        for payload in (b"2", b"3", b"4"):
            scheduler.submit(payload)
        first.set_result(b"1")
        _, retained, last = await offload.started.get()
        last.set_result(retained)
        await asyncio.sleep(0)
        self.assertEqual([value for value, _ in results], [b"1", b"4"])
        await scheduler.close()

    async def test_dropped_count_is_exact(self) -> None:
        scheduler, offload, _, _ = await setup_scheduler()
        scheduler.submit(b"1")
        await offload.started.get()
        for payload in (b"2", b"3", b"4"):
            scheduler.submit(payload)
        self.assertEqual(scheduler.metrics.dropped_frames, 2)
        await scheduler.close()

    async def test_received_counts_every_submission(self) -> None:
        scheduler, _, _, _ = await setup_scheduler()
        for payload in (b"1", b"2", b"3"):
            scheduler.submit(payload)
        self.assertEqual(scheduler.metrics.received_frames, 3)
        await scheduler.close()

    async def test_processed_counts_completed_attempts(self) -> None:
        scheduler, offload, _, _ = await setup_scheduler()
        scheduler.submit(b"one")
        _, _, completion = await offload.started.get()
        self.assertEqual(scheduler.metrics.processed_frames, 0)
        completion.set_result(b"one")
        await asyncio.sleep(0)
        self.assertEqual(scheduler.metrics.processed_frames, 1)
        await scheduler.close()

    async def test_failure_increments_both_completion_counters(self) -> None:
        scheduler, offload, _, errors = await setup_scheduler()
        scheduler.submit(b"bad")
        _, _, completion = await offload.started.get()
        completion.set_result(ValueError("bad"))
        await asyncio.sleep(0)
        self.assertEqual(scheduler.metrics.processed_frames, 1)
        self.assertEqual(scheduler.metrics.processing_failures, 1)
        self.assertEqual(str(errors[0][0]), "bad")
        await scheduler.close()

    async def test_processing_continues_after_failure(self) -> None:
        scheduler, offload, results, _ = await setup_scheduler()
        scheduler.submit(b"bad")
        _, _, first = await offload.started.get()
        scheduler.submit(b"good")
        first.set_result(ValueError("bad"))
        _, _, second = await offload.started.get()
        second.set_result(b"good")
        await asyncio.sleep(0)
        self.assertEqual(results[0][0], b"good")
        await scheduler.close()

    async def test_pending_depth_never_exceeds_one(self) -> None:
        scheduler, _, _, _ = await setup_scheduler()
        for index in range(10):
            scheduler.submit(str(index).encode())
            self.assertIn(scheduler.metrics.pending_frames, (0, 1))
        await scheduler.close()

    async def test_queue_delay_uses_injected_clock(self) -> None:
        values = iter((1.0, 1.25, 1.75))
        scheduler, offload, results, _ = await setup_scheduler(
            clock=lambda: next(values)
        )
        scheduler.submit(b"one")
        _, _, completion = await offload.started.get()
        completion.set_result(b"one")
        await asyncio.sleep(0)
        self.assertEqual(results[0][1].queue_delay_ms, 250.0)
        await scheduler.close()

    async def test_processing_duration_uses_injected_clock(self) -> None:
        values = iter((1.0, 1.25, 1.75))
        scheduler, offload, results, _ = await setup_scheduler(
            clock=lambda: next(values)
        )
        scheduler.submit(b"one")
        _, _, completion = await offload.started.get()
        completion.set_result(b"one")
        await asyncio.sleep(0)
        self.assertEqual(results[0][1].processing_time_ms, 500.0)
        await scheduler.close()

    async def test_close_clears_pending(self) -> None:
        scheduler, _, _, _ = await setup_scheduler()
        scheduler.submit(b"one")
        await scheduler.close()
        self.assertEqual(scheduler.metrics.pending_frames, 0)

    async def test_close_rejects_submission(self) -> None:
        scheduler, _, _, _ = await setup_scheduler()
        await scheduler.close()
        with self.assertRaises(LatestFrameSchedulerError):
            scheduler.submit(b"late")

    async def test_close_is_idempotent(self) -> None:
        scheduler, _, _, _ = await setup_scheduler()
        await scheduler.close()
        await scheduler.close()

    async def test_no_callback_after_close(self) -> None:
        scheduler, offload, results, errors = await setup_scheduler()
        scheduler.submit(b"one")
        await offload.started.get()
        await scheduler.close()
        self.assertEqual(results, [])
        self.assertEqual(errors, [])

    async def test_only_immutable_bytes_are_accepted(self) -> None:
        scheduler, _, _, _ = await setup_scheduler()
        with self.assertRaises(LatestFrameSchedulerError):
            scheduler.submit(bytearray(b"mutable"))  # type: ignore[arg-type]
        await scheduler.close()

    def test_metrics_snapshot_is_immutable(self) -> None:
        metrics = FrameSchedulerMetrics()
        with self.assertRaises(FrozenInstanceError):
            metrics.received_frames = 1  # type: ignore[misc]
