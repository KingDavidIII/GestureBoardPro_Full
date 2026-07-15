from __future__ import annotations

import math
from unittest import TestCase

from gestureboard.recognition import (
    GestureCandidate,
    GestureId,
    GestureStabilizer,
    GestureStabilizerPolicy,
)


class StabilizerTests(TestCase):
    def test_policy_rejects_invalid_values(self) -> None:
        for kwargs in (
            {"confirmation_frames": 0},
            {"confirmation_frames": True},
            {"release_frames": 0},
            {"release_frames": True},
            {"maximum_inter_frame_gap_ms": 0},
            {"maximum_inter_frame_gap_ms": math.inf},
            {"minimum_stable_confidence": 2},
            {"maximum_inter_frame_gap_ms": math.nan},
        ):
            with self.assertRaises(ValueError):
                GestureStabilizerPolicy(**kwargs)

    def test_activation_change_and_release_emit_once(self) -> None:
        open_palm = GestureCandidate(GestureId.OPEN_PALM, 0.9, "open")
        point = GestureCandidate(GestureId.POINT, 0.9, "point")
        stabilizer = GestureStabilizer(
            GestureStabilizerPolicy(confirmation_frames=2, release_frames=2)
        )
        self.assertIsNone(stabilizer.update(open_palm))
        activated = stabilizer.update(open_palm)
        self.assertEqual((activated.event_id, activated.kind.value), (1, "activated"))
        self.assertIsNone(stabilizer.update(open_palm))
        self.assertIsNone(stabilizer.update(point))
        changed = stabilizer.update(point)
        self.assertEqual(
            (changed.event_id, changed.previous_gesture, changed.gesture),
            (2, GestureId.OPEN_PALM, GestureId.POINT),
        )
        self.assertIsNone(stabilizer.update(None))
        released = stabilizer.update(None)
        self.assertEqual(
            (released.event_id, released.kind.value, released.gesture),
            (3, "released", None),
        )
        self.assertIsNone(stabilizer.update(None))

    def test_gap_and_sequence_regression_begin_fresh_epochs(self) -> None:
        now = [0.0]
        candidate = GestureCandidate(GestureId.PINCH, 0.9, "pinch")
        stabilizer = GestureStabilizer(
            GestureStabilizerPolicy(
                confirmation_frames=2, maximum_inter_frame_gap_ms=10
            ),
            lambda: now[0],
        )
        stabilizer.update(candidate, frame_sequence=4)
        now[0] = 0.1
        self.assertIsNone(stabilizer.update(candidate, frame_sequence=5))
        self.assertIsNone(stabilizer.update(candidate, frame_sequence=1))
        self.assertIsNone(stabilizer.stable)
        self.assertEqual(stabilizer.update(candidate, frame_sequence=2).event_id, 1)

    def test_noise_low_confidence_and_replacement_remain_provisional(self) -> None:
        policy = GestureStabilizerPolicy(
            confirmation_frames=2, release_frames=2, minimum_stable_confidence=0.7
        )
        stabilizer = GestureStabilizer(policy, lambda: 0)
        palm = GestureCandidate(GestureId.OPEN_PALM, 0.9, "palm")
        point = GestureCandidate(GestureId.POINT, 0.9, "point")
        low = GestureCandidate(GestureId.PINCH, 0.6, "low")
        self.assertIsNone(stabilizer.update(palm))
        self.assertEqual(stabilizer.provisional, palm)
        self.assertIsNone(stabilizer.update(point))
        self.assertEqual(stabilizer.provisional, point)
        self.assertIsNone(stabilizer.update(low))
        self.assertIsNone(stabilizer.stable)
        self.assertIsNone(stabilizer.update(palm))
        self.assertIsNotNone(stabilizer.update(palm))
        self.assertEqual(stabilizer.stable, palm)
        self.assertIsNone(stabilizer.update(point))
        self.assertEqual(stabilizer.stable, palm)
        self.assertEqual(stabilizer.update(point).kind.value, "changed")

    def test_release_and_reset_clear_state_without_leaking_event_epoch(self) -> None:
        stabilizer = GestureStabilizer(
            GestureStabilizerPolicy(confirmation_frames=2, release_frames=2), lambda: 0
        )
        palm = GestureCandidate(GestureId.OPEN_PALM, 0.9, "palm")
        stabilizer.update(palm)
        stabilizer.update(palm)
        self.assertIsNone(stabilizer.update(None))
        released = stabilizer.update(None)
        self.assertEqual(
            (released.previous_gesture, released.gesture, released.event_id),
            (GestureId.OPEN_PALM, None, 2),
        )
        self.assertIsNone(stabilizer.update(None))
        stabilizer.reset()
        self.assertIsNone(stabilizer.stable)
        self.assertIsNone(stabilizer.provisional)
        self.assertIsNone(stabilizer.last_transition)
        self.assertIsNone(stabilizer.update(palm))
        self.assertEqual(stabilizer.update(palm).event_id, 1)

    def test_invalid_sequences_and_timestamp_are_rejected(self) -> None:
        stabilizer = GestureStabilizer(GestureStabilizerPolicy(), lambda: 0)
        candidate = GestureCandidate(GestureId.PINCH, 0.9, "pinch")
        for sequence in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                stabilizer.update(candidate, frame_sequence=sequence)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            stabilizer.update(candidate, timestamp_ms=-1)
