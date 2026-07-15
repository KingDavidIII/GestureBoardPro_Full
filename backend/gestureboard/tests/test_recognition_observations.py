from __future__ import annotations

import math
from types import SimpleNamespace
from unittest import TestCase

from gestureboard.recognition import (
    Handedness,
    HandObservation,
    Landmark3D,
    adapt_hands,
    select_primary,
)


def landmark(x: float, y: float, z: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


def valid_hand() -> SimpleNamespace:
    points = [landmark(0.0, 0.0) for _ in range(21)]
    for index, x in zip((5, 9, 13, 17), (1.0, 2.0, 3.0, 4.0), strict=True):
        points[index] = landmark(x, 0.0)
    return SimpleNamespace(landmark=points)


class ObservationAdapterTests(TestCase):
    def test_no_hand_invalid_hand_and_valid_hand_are_isolated(self) -> None:
        self.assertEqual(adapt_hands(SimpleNamespace()), ())
        result = SimpleNamespace(
            multi_hand_landmarks=[SimpleNamespace(landmark=[]), valid_hand()],
            multi_handedness=[],
        )
        observations = adapt_hands(result)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].source_index, 1)
        self.assertEqual(observations[0].handedness, Handedness.UNKNOWN)

    def test_handedness_copies_and_mutation_does_not_escape(self) -> None:
        source = valid_hand()
        metadata = SimpleNamespace(
            classification=[SimpleNamespace(label="Left", score=0.9)]
        )
        observation = adapt_hands(
            SimpleNamespace(multi_hand_landmarks=[source], multi_handedness=[metadata])
        )[0]
        source.landmark[0].x = 99
        self.assertEqual(observation.handedness, Handedness.LEFT)
        self.assertEqual(observation.landmarks[0], Landmark3D(0, 0, 0))
        self.assertTrue(math.isfinite(observation.palm_scale))

    def test_scale_area_translation_scale_and_mirror_invariants(self) -> None:
        base = adapt_hands(SimpleNamespace(multi_hand_landmarks=[valid_hand()]))[0]
        moved = valid_hand()
        for point in moved.landmark:
            point.x += 5
            point.y += 3
        translated = adapt_hands(SimpleNamespace(multi_hand_landmarks=[moved]))[0]
        scaled = valid_hand()
        for point in scaled.landmark:
            point.x *= 2
            point.y *= 2
        enlarged = adapt_hands(SimpleNamespace(multi_hand_landmarks=[scaled]))[0]
        self.assertAlmostEqual(base.palm_scale, translated.palm_scale)
        self.assertAlmostEqual(enlarged.palm_scale, base.palm_scale * 2)
        self.assertAlmostEqual(enlarged.palm_area, base.palm_area * 4)

    def test_nan_infinity_and_degenerate_hands_are_excluded(self) -> None:
        bad = valid_hand()
        bad.landmark[4].x = math.nan
        self.assertEqual(adapt_hands(SimpleNamespace(multi_hand_landmarks=[bad])), ())
        self.assertEqual(
            adapt_hands(
                SimpleNamespace(
                    multi_hand_landmarks=[
                        SimpleNamespace(landmark=[landmark(0, 0) for _ in range(21)])
                    ]
                )
            ),
            (),
        )

    def test_palm_formula_uses_wrist_mcp_median_and_polygon_area(self) -> None:
        source = valid_hand()
        source.landmark[5] = landmark(1, 0)
        source.landmark[9] = landmark(0, 2)
        source.landmark[13] = landmark(-3, 0)
        source.landmark[17] = landmark(0, -4)
        observed = adapt_hands(SimpleNamespace(multi_hand_landmarks=[source]))[0]
        self.assertEqual(observed.palm_scale, 2.5)  # median of 1, 2, 3, 4
        self.assertEqual(observed.palm_area, 10.0)
        mirrored = valid_hand()
        for index, point in enumerate(source.landmark):
            mirrored.landmark[index] = landmark(-point.x + 7, point.y + 3)
        reflected = adapt_hands(SimpleNamespace(multi_hand_landmarks=[mirrored]))[0]
        self.assertAlmostEqual(reflected.palm_scale, observed.palm_scale)
        self.assertAlmostEqual(reflected.palm_area, observed.palm_area)

    def test_selection_priority_is_confidence_area_then_source_index(self) -> None:
        points = tuple(Landmark3D(0, 0, 0) for _ in range(21))

        def hand(
            index: int, detected: float | None, handed: float, area: float
        ) -> HandObservation:
            return HandObservation(
                points, index, Handedness.RIGHT, handed, detected, 1, area
            )

        detection = hand(2, 0.9, 0.1, 1)
        handedness = hand(1, 0.8, 0.99, 99)
        area = hand(0, 0.8, 0.5, 2)
        tied_area = hand(3, 0.8, 0.5, 2)
        self.assertEqual(
            select_primary((handedness, detection, area)).primary_hand, detection
        )
        absent_detection = hand(4, None, 1.0, 99)
        self.assertEqual(select_primary((absent_detection, area)).primary_hand, area)
        self.assertEqual(select_primary((tied_area, area)).primary_hand, area)
        self.assertEqual(select_primary((area, tied_area)).primary_hand, area)
        selected = select_primary((detection, handedness, area))
        self.assertEqual(selected.valid_hand_count, 3)
        self.assertEqual(select_primary(()).primary_hand, None)
