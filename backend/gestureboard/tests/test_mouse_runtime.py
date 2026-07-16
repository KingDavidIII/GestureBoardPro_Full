from __future__ import annotations

from unittest import TestCase

from gestureboard.mouse import (
    GestureMouseRuntimeCoordinator,
    MouseLifecycleError,
    MouseOutputError,
    WindowsCursorOwnershipLease,
)
from gestureboard.recognition.observations import (
    Handedness,
    HandObservation,
    HandSelection,
    Landmark3D,
)


class RecordingOutput:
    def __init__(self, fail: bool = False) -> None:
        self.targets = []
        self.closed = 0
        self.fail = fail

    def move(self, target: object) -> None:
        if self.fail:
            raise MouseOutputError("failed")
        self.targets.append(target)

    def close(self) -> None:
        self.closed += 1


def hand(x: float = 0.25, y: float = 0.75, source: int = 0) -> HandSelection:
    points = [Landmark3D(0, 0, 0) for _ in range(21)]
    points[8] = Landmark3D(x, y, 0)
    return HandSelection(
        1, HandObservation(tuple(points), source, Handedness.RIGHT, 1, None, 1, 1)
    )


class GestureMouseRuntimeCoordinatorTests(TestCase):
    def test_disabled_default_never_maps_or_outputs(self) -> None:
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator("a", output=output)
        self.assertFalse(coordinator.process(hand(), timestamp_ms=0).moved)
        self.assertEqual(output.targets, [])

    def test_virtual_mapping_rate_boundaries_reset_and_source_change(self) -> None:
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "a", enabled=True, output=output, max_output_hz=10
        )
        self.assertTrue(coordinator.process(hand(), timestamp_ms=0).moved)
        self.assertTrue(coordinator.process(hand(0.5), timestamp_ms=100).moved)
        self.assertTrue(coordinator.process(hand(0.7), timestamp_ms=199).rate_limited)
        self.assertTrue(
            coordinator.process(hand(0.8, source=1), timestamp_ms=200).moved
        )
        self.assertTrue(coordinator.process(None, timestamp_ms=201).mapping is None)
        self.assertTrue(coordinator.process(hand(), timestamp_ms=202).moved)
        self.assertEqual(len(output.targets), 4)

    def test_windows_lease_denial_and_emergency_release(self) -> None:
        lease = WindowsCursorOwnershipLease()
        self.assertTrue(lease.acquire("other"))
        output = RecordingOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "a", enabled=True, output=output, windows_lease=lease
        )
        self.assertFalse(coordinator.process(hand(), timestamp_ms=0).moved)
        lease.release("other")
        self.assertTrue(coordinator.process(hand(0.5), timestamp_ms=1).moved)
        self.assertEqual(lease.owner_id, "a")
        coordinator.emergency_stop(timestamp_ms=2)
        self.assertIsNone(lease.owner_id)

    def test_output_failure_stops_and_shutdown_is_idempotent(self) -> None:
        lease = WindowsCursorOwnershipLease()
        output = RecordingOutput(fail=True)
        coordinator = GestureMouseRuntimeCoordinator(
            "a", enabled=True, output=output, windows_lease=lease
        )
        with self.assertRaises(MouseOutputError):
            coordinator.process(hand(), timestamp_ms=0)
        self.assertFalse(coordinator.enabled)
        self.assertIsNone(lease.owner_id)
        coordinator.close()
        coordinator.close()
        self.assertEqual(output.closed, 1)
        with self.assertRaises(MouseLifecycleError):
            coordinator.process(hand(), timestamp_ms=1)
