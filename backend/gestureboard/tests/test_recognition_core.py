"""Deterministic unit coverage for pure Alpha 9 recognition primitives."""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest import TestCase

from gestureboard.recognition import (
    GestureCandidate,
    GestureId,
    GestureStabilizer,
    GestureStabilizerPolicy,
    Handedness,
    HandObservation,
    Landmark3D,
    adapt_hands,
    classify,
    extract_features,
    select_primary,
)


def points() -> tuple[Landmark3D, ...]:
    return tuple(
        Landmark3D(float(index % 5) / 10, float(index // 5) / 10, 0.0)
        for index in range(21)
    )


def hand(index: int = 0, confidence: float = 0.8, area: float = 1.0) -> HandObservation:
    return HandObservation(
        points(), index, Handedness.RIGHT, confidence, None, 1.0, area
    )


class RecognitionCoreTests(TestCase):
    def test_landmark_and_observation_validation_are_immutable(self) -> None:
        landmark = Landmark3D(0, 0, 0)
        with self.assertRaises(ValueError):
            Landmark3D(math.nan, 0, 0)
        with self.assertRaises(ValueError):
            HandObservation(points()[:20], 0, Handedness.LEFT, 0.5, None, 1, 0)
        with self.assertRaises(ValueError):
            HandObservation(points(), 0.5, Handedness.LEFT, 0.5, None, 1, 0)  # type: ignore[arg-type]
        with self.assertRaises(AttributeError):
            landmark.x = 2  # type: ignore[misc]

    def test_adapter_excludes_malformed_hands_and_selects_deterministically(
        self,
    ) -> None:
        valid = SimpleNamespace(
            landmark=[SimpleNamespace(x=p.x, y=p.y, z=p.z) for p in points()]
        )
        invalid = SimpleNamespace(landmark=[])
        result = SimpleNamespace(
            multi_hand_landmarks=[invalid, valid], multi_handedness=[]
        )
        observations = adapt_hands(result)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].source_index, 1)
        self.assertEqual(
            select_primary(
                (hand(2, 0.8, 2), hand(1, 0.8, 2))
            ).primary_hand.source_index,
            1,
        )

    def test_geometry_and_classifier_are_translation_scale_tolerant(self) -> None:
        observation = hand()
        features = extract_features(observation)
        self.assertTrue(math.isfinite(features.thumb_index_distance))
        self.assertIsNotNone(classify(features))
        translated = tuple(
            Landmark3D(point.x + 5, point.y - 3, point.z + 1) for point in points()
        )
        translated_features = extract_features(
            HandObservation(translated, 0, Handedness.RIGHT, 0.8, None, 1, 1)
        )
        self.assertEqual(
            features.extended_finger_count, translated_features.extended_finger_count
        )

    def test_stabilizer_activates_changes_releases_and_resets(self) -> None:
        now = [0.0]
        stabilizer = GestureStabilizer(
            GestureStabilizerPolicy(confirmation_frames=2, release_frames=2),
            lambda: now[0],
        )
        palm = GestureCandidate(GestureId.OPEN_PALM, 0.9, "fixture")
        self.assertIsNone(stabilizer.update(palm))
        activated = stabilizer.update(palm)
        self.assertEqual(activated.event_id, 1)
        self.assertIsNone(stabilizer.update(palm))
        self.assertIsNone(stabilizer.update(None))
        released = stabilizer.update(None)
        self.assertEqual(released.kind.value, "released")
        stabilizer.reset()
        self.assertIsNone(stabilizer.update(palm))
        self.assertEqual(stabilizer.update(palm).event_id, 1)

    def test_stabilizer_suppresses_noise_changes_and_sequence_regressions(self) -> None:
        stabilizer = GestureStabilizer(
            GestureStabilizerPolicy(confirmation_frames=2, release_frames=2), lambda: 0
        )
        palm = GestureCandidate(GestureId.OPEN_PALM, 0.9, "palm")
        pinch = GestureCandidate(GestureId.PINCH, 0.9, "pinch")
        self.assertIsNone(stabilizer.update(palm, frame_sequence=4))
        self.assertIsNotNone(stabilizer.update(palm, frame_sequence=5))
        self.assertIsNone(stabilizer.update(pinch, frame_sequence=6))
        changed = stabilizer.update(pinch, frame_sequence=7)
        self.assertEqual(changed.kind.value, "changed")
        self.assertEqual(changed.previous_gesture, GestureId.OPEN_PALM)
        self.assertIsNone(stabilizer.update(palm, frame_sequence=1))
        self.assertIsNone(stabilizer.stable)

    def test_selection_confidence_area_and_empty_ordering(self) -> None:
        self.assertIsNone(select_primary(()).primary_hand)
        detected = HandObservation(points(), 0, Handedness.LEFT, 0.5, 0.9, 1, 1)
        handed = HandObservation(points(), 1, Handedness.RIGHT, 0.99, 0.8, 1, 99)
        self.assertEqual(
            select_primary((handed, detected)).primary_hand.source_index, 0
        )
        self.assertEqual(
            select_primary(
                (hand(4, 0.5, 1), hand(3, 0.5, 2))
            ).primary_hand.source_index,
            3,
        )
