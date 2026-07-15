from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf, nan
from unittest import TestCase

from gestureboard.mouse import (
    CursorTarget,
    MouseMode,
    MouseSnapshot,
    MouseValidationError,
)


class MouseModelTests(TestCase):
    def test_cursor_target_accepts_boundaries_and_is_immutable(self) -> None:
        target = CursorTarget(0.0, 1.0, 7, 2)

        self.assertEqual(
            (target.x, target.y, target.timestamp_ms, target.source_index),
            (0.0, 1.0, 7, 2),
        )
        with self.assertRaises(FrozenInstanceError):
            target.x = 0.5  # type: ignore[misc]

    def test_cursor_target_rejects_invalid_coordinates_and_metadata(self) -> None:
        cases = (
            (-0.001, 0.5, 0, 0),
            (1.001, 0.5, 0, 0),
            (nan, 0.5, 0, 0),
            (inf, 0.5, 0, 0),
            (-inf, 0.5, 0, 0),
            (True, 0.5, 0, 0),
            (0.5, False, 0, 0),
            (0.5, 0.5, -1, 0),
            (0.5, 0.5, True, 0),
            (0.5, 0.5, 0, -1),
            (0.5, 0.5, 0, False),
        )
        for values in cases:
            with self.assertRaises(MouseValidationError):
                CursorTarget(*values)

    def test_snapshot_is_immutable(self) -> None:
        snapshot = MouseSnapshot(MouseMode.DISABLED, None, 0, False, False, False)

        with self.assertRaises(FrozenInstanceError):
            snapshot.mode = MouseMode.ACTIVE  # type: ignore[misc]
