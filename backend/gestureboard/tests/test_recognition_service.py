from __future__ import annotations

import math
from types import SimpleNamespace
from unittest import TestCase

from gestureboard.recognition import GestureId, GestureStabilizerPolicy
from gestureboard.recognition.service import (
    RecognitionService,
    serialize_recognition,
)


def hand(*, point: bool = False) -> SimpleNamespace:
    landmarks = [SimpleNamespace(x=0.0, y=0.0, z=0.0) for _ in range(21)]
    for (mcp, pip, tip), x in zip(
        ((1, 3, 4), (5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20)),
        (-2, -1, 0, 1, 2),
        strict=True,
    ):
        landmarks[mcp] = SimpleNamespace(x=x, y=0.0, z=0.0)
        landmarks[pip] = SimpleNamespace(x=x, y=1.0, z=0.0)
        landmarks[tip] = SimpleNamespace(
            x=x, y=3.0 if not point or tip == 8 else 0.5, z=0.0
        )
    return SimpleNamespace(landmark=landmarks)


def result(*hands: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        multi_hand_landmarks=list(hands),
        multi_handedness=[
            SimpleNamespace(classification=[SimpleNamespace(label="Right", score=0.9)])
            for _ in hands
        ],
    )


class RecognitionServiceTests(TestCase):
    def test_accepts_existing_result_without_media_pipe_invocation_and_serializes_safely(
        self,
    ) -> None:
        service = RecognitionService(
            stabilizer_policy=GestureStabilizerPolicy(confirmation_frames=2),
            clock=lambda: 1,
        )
        first = service.process(result(hand()), frame_sequence=3)
        payload = serialize_recognition(first, now_ms=1000)
        self.assertEqual(
            (first.hand_count, first.candidate.gesture_id, first.stable),
            (1, GestureId.OPEN_PALM, None),
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertNotIn("landmarks", repr(payload))
        self.assertTrue(
            all(
                math.isfinite(value)
                for value in (payload["frame_sequence"], payload["hand_count"])
            )
        )

    def test_transitions_release_reset_and_no_hand_are_connection_local(self) -> None:
        service = RecognitionService(
            stabilizer_policy=GestureStabilizerPolicy(
                confirmation_frames=2, release_frames=2
            ),
            clock=lambda: 1,
        )
        service.process(result(hand()), frame_sequence=0)
        activated = service.process(result(hand()), frame_sequence=1)
        self.assertEqual(activated.transition.kind.value, "activated")
        self.assertIsNone(service.process(result(), frame_sequence=2).transition)
        released = service.process(result(), frame_sequence=3)
        self.assertEqual(
            (released.hand_count, released.candidate, released.transition.kind.value),
            (0, None, "released"),
        )
        service.reset()
        self.assertIsNone(service.process(result(hand()), frame_sequence=0).transition)
        self.assertEqual(
            service.process(result(hand()), frame_sequence=1).transition.event_id, 1
        )

    def test_stable_duration_uses_frame_timestamps_and_resets_per_interval(
        self,
    ) -> None:
        service = RecognitionService(
            stabilizer_policy=GestureStabilizerPolicy(
                confirmation_frames=1, release_frames=1
            )
        )
        initial = service.process(result(hand()), frame_sequence=0, timestamp_ms=100)
        later = service.process(result(hand()), frame_sequence=1, timestamp_ms=125)
        changed = service.process(
            result(hand(point=True)), frame_sequence=2, timestamp_ms=150
        )
        released = service.process(result(), frame_sequence=3, timestamp_ms=175)
        service.reset()
        restarted = service.process(result(hand()), frame_sequence=0, timestamp_ms=200)
        self.assertEqual(serialize_recognition(initial)["stable"]["since_ms"], 0)
        self.assertEqual(serialize_recognition(later)["stable"]["since_ms"], 25)
        self.assertGreaterEqual(
            serialize_recognition(later)["stable"]["since_ms"],
            serialize_recognition(initial)["stable"]["since_ms"],
        )
        self.assertEqual(serialize_recognition(changed)["stable"]["since_ms"], 0)
        self.assertIsNone(serialize_recognition(released)["stable"])
        self.assertEqual(serialize_recognition(restarted)["stable"]["since_ms"], 0)
