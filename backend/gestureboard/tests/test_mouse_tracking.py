from __future__ import annotations

from math import inf, nan
from types import SimpleNamespace
from unittest import TestCase

from gestureboard.mouse import (
    INDEX_FINGERTIP_LANDMARK_INDEX,
    CursorTarget,
    VirtualCursorMapper,
    VirtualSurface,
    cursor_target_from_selected_hand,
)
from gestureboard.recognition.observations import (
    Handedness,
    HandObservation,
    HandSelection,
    Landmark3D,
)


def selected_hand(
    *, x: float = 0.25, y: float = 0.75, source_index: int = 3
) -> HandObservation:
    landmarks = [Landmark3D(0.0, 0.0, 0.0) for _ in range(21)]
    landmarks[INDEX_FINGERTIP_LANDMARK_INDEX] = Landmark3D(x, y, 9.0)
    return HandObservation(
        tuple(landmarks), source_index, Handedness.RIGHT, 0.9, None, 1.0, 1.0
    )


class MouseTrackingTests(TestCase):
    def test_extracts_exact_index_fingertip_and_preserves_metadata(self) -> None:
        hand = selected_hand()

        target = cursor_target_from_selected_hand(hand, timestamp_ms=42)

        self.assertEqual(target, CursorTarget(0.25, 0.75, 42, 3))

    def test_selection_no_hand_and_clamping_are_safe(self) -> None:
        self.assertIsNone(
            cursor_target_from_selected_hand(HandSelection(0, None), timestamp_ms=1)
        )
        hand = selected_hand(x=-0.01, y=1.01)

        self.assertEqual(
            cursor_target_from_selected_hand(hand, timestamp_ms=2),
            CursorTarget(0.0, 1.0, 2, 3),
        )

    def test_malformed_index_fingertip_is_rejected_without_reselection(self) -> None:
        hand = selected_hand()
        malformed = list(hand.landmarks)
        malformed[8] = SimpleNamespace(x=nan, y=0.5)
        object.__setattr__(hand, "landmarks", tuple(malformed))
        self.assertIsNone(cursor_target_from_selected_hand(hand, timestamp_ms=1))
        malformed[8] = SimpleNamespace(x=inf, y=0.5)
        object.__setattr__(hand, "landmarks", tuple(malformed))
        self.assertIsNone(cursor_target_from_selected_hand(hand, timestamp_ms=1))
        malformed[8] = SimpleNamespace(x=-inf, y=0.5)
        object.__setattr__(hand, "landmarks", tuple(malformed))
        self.assertIsNone(cursor_target_from_selected_hand(hand, timestamp_ms=1))
        malformed[8] = SimpleNamespace(x=True, y=0.5)
        object.__setattr__(hand, "landmarks", tuple(malformed))
        self.assertIsNone(cursor_target_from_selected_hand(hand, timestamp_ms=1))

    def test_missing_index_landmark_returns_no_target(self) -> None:
        hand = selected_hand()
        object.__setattr__(hand, "landmarks", hand.landmarks[:8])

        self.assertIsNone(cursor_target_from_selected_hand(hand, timestamp_ms=1))

    def test_selected_hand_adapter_maps_end_to_end_without_media_pipe(self) -> None:
        selection = HandSelection(1, selected_hand(x=1.0, y=0.0, source_index=7))
        mapper = VirtualCursorMapper(VirtualSurface(101, 51))

        result = mapper.map(cursor_target_from_selected_hand(selection, timestamp_ms=9))

        self.assertTrue(result.emitted)
        self.assertEqual(
            (
                result.virtual_target.x_px,
                result.virtual_target.y_px,
                result.virtual_target.source_index,
            ),
            (100, 0, 7),
        )
