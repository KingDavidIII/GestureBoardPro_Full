from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import TestCase

from gestureboard.mouse import (
    CursorTarget,
    GestureMouseService,
    MouseEvent,
    MouseEventKind,
    MouseLifecycleError,
    MouseMode,
    MouseOutputError,
)


class RecordingPort:
    def __init__(self) -> None:
        self.events: list[MouseEvent] = []
        self.close_calls = 0

    def emit(self, event: MouseEvent) -> None:
        self.events.append(event)

    def close(self) -> None:
        self.close_calls += 1


class FailingPort(RecordingPort):
    def __init__(self, fail_kind: MouseEventKind) -> None:
        super().__init__()
        self.fail_kind = fail_kind

    def emit(self, event: MouseEvent) -> None:
        if event.kind is self.fail_kind:
            raise RuntimeError("fixture output failure")
        super().emit(event)


class GestureMouseServiceTests(TestCase):
    def setUp(self) -> None:
        self.port = RecordingPort()
        self.service = GestureMouseService(self.port, clock=lambda: 100)

    def _activate(self) -> None:
        self.assertTrue(self.service.enable(timestamp_ms=1))
        self.assertTrue(self.service.tracking_acquired(timestamp_ms=2))

    def test_initial_snapshot_and_enable_tracking_are_event_only(self) -> None:
        initial = self.service.snapshot()
        self.assertEqual(
            (initial.mode, initial.current_target, initial.last_emitted_sequence),
            (MouseMode.DISABLED, None, 0),
        )
        self.assertEqual(self.port.events, [])

        self.assertTrue(self.service.enable(timestamp_ms=11))
        self.assertTrue(self.service.tracking_acquired(timestamp_ms=12))
        self.assertFalse(self.service.enable(timestamp_ms=13))
        self.assertFalse(self.service.tracking_acquired(timestamp_ms=14))

        self.assertEqual(self.service.snapshot().mode, MouseMode.ACTIVE)
        self.assertEqual(
            [event.kind for event in self.port.events],
            [MouseEventKind.MODE_CHANGED, MouseEventKind.MODE_CHANGED],
        )
        self.assertEqual([event.timestamp_ms for event in self.port.events], [11, 12])

    def test_targets_are_accepted_once_only_while_active(self) -> None:
        target = CursorTarget(0.25, 0.75, 33, 1)
        self.assertFalse(self.service.submit_target(target))
        self.assertIsNone(self.service.snapshot().current_target)

        self._activate()
        self.assertTrue(self.service.submit_target(target))

        accepted = self.port.events[-1]
        self.assertEqual(accepted.kind, MouseEventKind.CURSOR_TARGET_ACCEPTED)
        self.assertEqual(accepted.target, target)
        self.assertEqual(self.service.snapshot().current_target, target)
        self.assertEqual(accepted.sequence, 3)

    def test_tracking_loss_pause_resume_and_disable_clear_stale_targets(self) -> None:
        first = CursorTarget(0.2, 0.3, 10, 0)
        self._activate()
        self.service.submit_target(first)
        self.assertTrue(self.service.tracking_lost(timestamp_ms=20))
        self.assertEqual(self.service.snapshot().mode, MouseMode.READY)
        self.assertIsNone(self.service.snapshot().current_target)
        self.assertFalse(self.service.submit_target(first))

        self.service.tracking_acquired(timestamp_ms=21)
        self.service.submit_target(first)
        self.assertTrue(self.service.pause(timestamp_ms=22))
        self.assertEqual(self.service.snapshot().mode, MouseMode.PAUSED)
        self.assertFalse(self.service.submit_target(first))
        self.assertTrue(self.service.resume(timestamp_ms=23))
        self.assertEqual(self.service.snapshot().mode, MouseMode.READY)
        self.assertFalse(self.service.submit_target(first))
        self.service.tracking_acquired(timestamp_ms=24)
        self.assertTrue(self.service.disable(timestamp_ms=25))
        self.assertEqual(self.service.snapshot().mode, MouseMode.DISABLED)
        self.assertIsNone(self.service.snapshot().current_target)
        self.assertIn(
            MouseEventKind.SAFETY_RESET_REQUESTED,
            [event.kind for event in self.port.events],
        )

    def test_emergency_stop_always_requests_a_reset_and_requires_reenable(self) -> None:
        self.assertFalse(self.service.emergency_stop(timestamp_ms=1))
        self.assertFalse(self.service.emergency_stop(timestamp_ms=2))
        self.assertEqual(self.service.snapshot().mode, MouseMode.DISABLED)
        self.assertEqual(
            [event.kind for event in self.port.events],
            [
                MouseEventKind.SAFETY_RESET_REQUESTED,
                MouseEventKind.SAFETY_RESET_REQUESTED,
            ],
        )
        self.assertFalse(self.service.tracking_acquired())
        self.assertTrue(self.service.enable())
        self.assertTrue(self.service.tracking_acquired())

    def test_shutdown_closes_once_and_rejects_post_close_operations(self) -> None:
        self._activate()
        self.service.submit_target(CursorTarget(0.1, 0.2, 3, 0))

        self.assertTrue(self.service.shutdown(timestamp_ms=30))
        self.assertFalse(self.service.shutdown(timestamp_ms=31))

        self.assertEqual(self.port.close_calls, 1)
        self.assertEqual(self.service.snapshot().mode, MouseMode.CLOSED)
        self.assertIsNone(self.service.snapshot().current_target)
        with self.assertRaises(MouseLifecycleError):
            self.service.enable()
        with self.assertRaises(MouseLifecycleError):
            self.service.submit_target(CursorTarget(0.1, 0.2, 4, 0))

    def test_output_failure_does_not_retain_or_duplicate_target_and_safety_remains_available(
        self,
    ) -> None:
        port = FailingPort(MouseEventKind.CURSOR_TARGET_ACCEPTED)
        service = GestureMouseService(port, clock=lambda: 5)
        service.enable()
        service.tracking_acquired()

        with self.assertRaises(MouseOutputError):
            service.submit_target(CursorTarget(0.4, 0.5, 6, 0))

        self.assertEqual(service.snapshot().mode, MouseMode.ACTIVE)
        self.assertIsNone(service.snapshot().current_target)
        self.assertNotIn(
            MouseEventKind.CURSOR_TARGET_ACCEPTED, [event.kind for event in port.events]
        )
        self.assertTrue(service.emergency_stop())
        self.assertTrue(service.shutdown())
        self.assertEqual(port.close_calls, 1)

    def test_sequences_are_unique_under_concurrent_submission_and_shutdown_is_final(
        self,
    ) -> None:
        self._activate()
        barrier = Barrier(3)

        def submit(index: int) -> bool:
            barrier.wait()
            return self.service.submit_target(CursorTarget(0.1 * index, 0.5, index, 0))

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(submit, 1)
            second = executor.submit(submit, 2)
            barrier.wait()
            self.assertIn(first.result(), {True, False})
            self.assertIn(second.result(), {True, False})

        sequences = [event.sequence for event in self.port.events]
        self.assertEqual(sequences, sorted(set(sequences)))
        self.assertTrue(self.service.shutdown())
        self.assertEqual(self.service.snapshot().mode, MouseMode.CLOSED)

    def test_shutdown_racing_submission_leaves_a_closed_safe_state(self) -> None:
        self._activate()
        barrier = Barrier(3)

        def submit() -> bool:
            barrier.wait()
            try:
                return self.service.submit_target(CursorTarget(0.2, 0.8, 9, 0))
            except MouseLifecycleError:
                return False

        def shutdown() -> bool:
            barrier.wait()
            return self.service.shutdown(timestamp_ms=10)

        with ThreadPoolExecutor(max_workers=2) as executor:
            submitted = executor.submit(submit)
            closed = executor.submit(shutdown)
            barrier.wait()
            self.assertIn(submitted.result(), {True, False})
            self.assertTrue(closed.result())

        snapshot = self.service.snapshot()
        self.assertEqual(
            (snapshot.mode, snapshot.current_target), (MouseMode.CLOSED, None)
        )
        sequences = [event.sequence for event in self.port.events]
        self.assertEqual(sequences, sorted(set(sequences)))
