from __future__ import annotations

import math
from unittest import TestCase

from gestureboard.recognition import (
    GestureCandidate,
    GestureId,
    GestureTransition,
    Handedness,
    HandObservation,
    HandSelection,
    Landmark3D,
    TransitionKind,
)


def landmarks() -> tuple[Landmark3D, ...]:
    return tuple(Landmark3D(index, index / 10, 0) for index in range(21))


def observation(**changes: object) -> HandObservation:
    values: dict[str, object] = dict(
        landmarks=landmarks(),
        source_index=0,
        handedness=Handedness.LEFT,
        handedness_confidence=0.8,
        detection_confidence=None,
        palm_scale=1.0,
        palm_area=1.0,
    )
    values.update(changes)
    return HandObservation(**values)  # type: ignore[arg-type]


class RecognitionModelTests(TestCase):
    def test_landmark_accepts_finite_values_and_rejects_non_finite_values(self) -> None:
        point = Landmark3D(1, -2, 3.5)
        self.assertEqual((point.x, point.y, point.z), (1, -2, 3.5))
        for value in (math.nan, math.inf, -math.inf):
            for coordinates in ((value, 0, 0), (0, value, 0), (0, 0, value)):
                with self.assertRaises(ValueError):
                    Landmark3D(*coordinates)
        with self.assertRaises(AttributeError):
            point.x = 4  # type: ignore[misc]

    def test_observation_validates_shape_scalars_and_immutable_storage(self) -> None:
        mutable = list(landmarks())
        stored = observation(
            landmarks=tuple(mutable),
            detection_confidence=0.5,
            frame_sequence=3,
            timestamp_ms=0,
        )
        mutable[0] = Landmark3D(99, 99, 99)
        self.assertEqual(stored.landmarks[0], Landmark3D(0, 0, 0))
        self.assertEqual(stored.detection_confidence, 0.5)
        for bad_landmarks in (landmarks()[:20], landmarks() + (Landmark3D(0, 0, 0),)):
            with self.assertRaises(ValueError):
                observation(landmarks=bad_landmarks)
        for value in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                observation(source_index=value)
        for value in (math.nan, math.inf, -math.inf, -0.1, 1.1, True):
            with self.assertRaises(ValueError):
                observation(handedness_confidence=value)
            with self.assertRaises(ValueError):
                observation(detection_confidence=value)
        for value in (0, -1, math.nan, math.inf, True):
            with self.assertRaises(ValueError):
                observation(palm_scale=value)
        for value in (-1, math.nan, math.inf, True):
            with self.assertRaises(ValueError):
                observation(palm_area=value)
        for value in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                observation(frame_sequence=value)
        for value in (-1, math.nan, math.inf, True):
            with self.assertRaises(ValueError):
                observation(timestamp_ms=value)
        with self.assertRaises(AttributeError):
            stored.palm_scale = 4  # type: ignore[misc]

    def test_selection_validates_count_primary_and_is_immutable(self) -> None:
        primary = observation()
        self.assertEqual(HandSelection(1, primary).primary_hand, primary)
        for count in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                HandSelection(count, None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            HandSelection(0, primary)
        with self.assertRaises(ValueError):
            HandSelection(1, object())  # type: ignore[arg-type]
        empty = HandSelection(0, None)
        with self.assertRaises(AttributeError):
            empty.valid_hand_count = 2  # type: ignore[misc]

    def test_candidate_is_canonical_immutable_and_copies_safe_diagnostics(self) -> None:
        source = {"distance": 0.4, "matched": True}
        candidate = GestureCandidate(
            GestureId.PINCH, 0.9, "thumb_index_distance", "left", True, source
        )
        source["distance"] = 99
        self.assertEqual(candidate.diagnostics["distance"], 0.4)
        with self.assertRaises(TypeError):
            candidate.diagnostics["distance"] = 2  # type: ignore[index]
        for gesture in ("pinch", object()):
            with self.assertRaises(ValueError):
                GestureCandidate(gesture, 0.5, "reason")  # type: ignore[arg-type]
        for confidence in (math.nan, math.inf, -math.inf, -1, 2):
            with self.assertRaises(ValueError):
                GestureCandidate(GestureId.PINCH, confidence, "reason")
        with self.assertRaises(ValueError):
            GestureCandidate(GestureId.PINCH, 0.5, "", "left", 1)  # type: ignore[arg-type]
        with self.assertRaises(AttributeError):
            candidate.reason = "changed"  # type: ignore[misc]

    def test_transition_requires_semantic_kind_shape_and_is_immutable(self) -> None:
        activated = GestureTransition(
            1, TransitionKind.ACTIVATED, None, GestureId.POINT, 0.9, 0, 2
        )
        changed = GestureTransition(
            2, TransitionKind.CHANGED, GestureId.POINT, GestureId.PINCH, 0.8, 1
        )
        released = GestureTransition(
            3, TransitionKind.RELEASED, GestureId.PINCH, None, 0.8, 2
        )
        self.assertEqual(
            (activated.kind, changed.kind, released.kind),
            (TransitionKind.ACTIVATED, TransitionKind.CHANGED, TransitionKind.RELEASED),
        )
        for args in (
            (1, TransitionKind.ACTIVATED, GestureId.PINCH, GestureId.POINT),
            (1, TransitionKind.CHANGED, None, GestureId.POINT),
            (1, TransitionKind.RELEASED, GestureId.PINCH, GestureId.POINT),
        ):
            with self.assertRaises(ValueError):
                GestureTransition(*args, 0.5, 0)  # type: ignore[arg-type]
        for event_id in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                GestureTransition(
                    event_id, TransitionKind.ACTIVATED, None, GestureId.POINT, 0.5, 0
                )  # type: ignore[arg-type]
        for value in (math.nan, math.inf, -math.inf, -1):
            with self.assertRaises(ValueError):
                GestureTransition(
                    1, TransitionKind.ACTIVATED, None, GestureId.POINT, 0.5, value
                )
        with self.assertRaises(AttributeError):
            released.event_id = 4  # type: ignore[misc]
