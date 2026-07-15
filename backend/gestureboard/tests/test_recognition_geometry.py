from __future__ import annotations

import math
from unittest import TestCase

from gestureboard.recognition import (
    Handedness,
    HandObservation,
    Landmark3D,
    extract_features,
)


def canonical_hand(
    *,
    extended: tuple[bool, bool, bool, bool, bool] = (True, True, True, True, True),
    pinch: bool = False,
    offset: tuple[float, float] = (0, 0),
    scale: float = 1,
) -> HandObservation:
    points = [Landmark3D(0, 0, 0) for _ in range(21)]
    points[0] = Landmark3D(0, 0, 0)
    for enabled, (mcp, pip, tip), x in zip(
        extended,
        ((1, 3, 4), (5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20)),
        (-2, -1, 0, 1, 2),
        strict=True,
    ):
        points[mcp] = Landmark3D(x, 0, 0)
        points[pip] = Landmark3D(x, 1, 0)
        points[tip] = Landmark3D(x, 3 if enabled else 0.5, 0)
    if pinch:
        points[4] = Landmark3D(points[8].x + 0.1, points[8].y, 0)
    converted = tuple(
        Landmark3D(point.x * scale + offset[0], point.y * scale + offset[1], point.z)
        for point in points
    )
    return HandObservation(
        converted, 0, Handedness.RIGHT, 0.9, None, scale, 4 * scale * scale
    )


class RecognitionGeometryTests(TestCase):
    def test_open_point_fist_and_pinch_have_expected_counts_and_proximity(self) -> None:
        open_features = extract_features(canonical_hand())
        point_features = extract_features(
            canonical_hand(extended=(False, True, False, False, False))
        )
        fist_features = extract_features(
            canonical_hand(extended=(False, False, False, False, False))
        )
        pinch_features = extract_features(canonical_hand(pinch=True))
        self.assertEqual(
            (open_features.extended_non_thumb_count, open_features.folded_finger_count),
            (4, 0),
        )
        self.assertEqual(
            (
                point_features.index_extended,
                point_features.extended_non_thumb_count,
                point_features.folded_finger_count,
            ),
            (True, 1, 4),
        )
        self.assertEqual(
            (fist_features.extended_finger_count, fist_features.folded_finger_count),
            (0, 5),
        )
        self.assertLess(
            pinch_features.thumb_index_distance, open_features.thumb_index_distance
        )

    def test_all_classifier_features_are_finite_bounded_and_internally_consistent(
        self,
    ) -> None:
        features = extract_features(canonical_hand())
        numeric = (
            features.thumb_index_distance,
            features.thumb_extension_score,
            features.index_extension_score,
            features.middle_extension_score,
            features.ring_extension_score,
            features.little_extension_score,
            features.thumb_opposition,
            features.thumb_angle_degrees,
            features.index_angle_degrees,
            features.middle_angle_degrees,
            features.ring_angle_degrees,
            features.little_angle_degrees,
            features.thumb_tip_to_mcp_distance,
            features.index_tip_to_mcp_distance,
            features.middle_tip_to_mcp_distance,
            features.ring_tip_to_mcp_distance,
            features.little_tip_to_mcp_distance,
            features.thumb_middle_distance,
            features.thumb_ring_distance,
            features.thumb_little_distance,
            features.nearest_non_index_thumb_distance,
            features.pinch_isolation_ratio,
            features.index_pip_angle,
            features.index_dip_angle,
        )
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in numeric))
        self.assertTrue(all(value <= 1 for value in numeric[1:7]))
        self.assertTrue(all(value <= 180 for value in numeric[7:12]))
        self.assertEqual(
            features.extended_finger_count,
            features.extended_non_thumb_count + int(features.thumb_extended),
        )
        self.assertEqual(
            features.extended_finger_count + features.folded_finger_count, 5
        )

    def test_contact_distances_and_index_angles_are_normalised_finite_and_mirror_safe(
        self,
    ) -> None:
        base = extract_features(canonical_hand())
        enlarged = extract_features(canonical_hand(scale=3))
        mirrored = extract_features(canonical_hand(offset=(0, 0)))
        self.assertAlmostEqual(
            base.thumb_middle_distance, enlarged.thumb_middle_distance
        )
        self.assertAlmostEqual(base.thumb_ring_distance, enlarged.thumb_ring_distance)
        self.assertAlmostEqual(
            base.thumb_little_distance, enlarged.thumb_little_distance
        )
        self.assertEqual(
            base.nearest_non_index_thumb_distance,
            min(
                base.thumb_middle_distance,
                base.thumb_ring_distance,
                base.thumb_little_distance,
            ),
        )
        self.assertAlmostEqual(
            base.pinch_isolation_ratio,
            base.thumb_index_distance / base.nearest_non_index_thumb_distance,
        )
        self.assertTrue(
            all(
                math.isfinite(value) and 0 <= value <= 180
                for value in (base.index_pip_angle, base.index_dip_angle)
            )
        )
        self.assertAlmostEqual(base.thumb_index_distance, mirrored.thumb_index_distance)
        self.assertAlmostEqual(base.index_pip_angle, mirrored.index_pip_angle)

    def test_retained_pip_distances_make_extension_margins_explicit(self) -> None:
        features = extract_features(
            canonical_hand(extended=(False, True, False, False, False))
        )
        self.assertAlmostEqual(
            features.index_tip_to_mcp_distance - features.index_pip_to_mcp_distance,
            2.0,
        )
        self.assertAlmostEqual(
            features.middle_tip_to_mcp_distance - features.middle_pip_to_mcp_distance,
            -0.5,
        )

    def test_translation_scale_and_mirror_preserve_feature_semantics(self) -> None:
        base = extract_features(canonical_hand())
        translated = extract_features(canonical_hand(offset=(50, -40)))
        enlarged = extract_features(canonical_hand(scale=3))
        mirrored = extract_features(canonical_hand(offset=(0, 0)))
        self.assertEqual(base.extended_finger_count, translated.extended_finger_count)
        self.assertEqual(base.extended_finger_count, enlarged.extended_finger_count)
        self.assertAlmostEqual(
            base.thumb_index_distance, translated.thumb_index_distance
        )
        self.assertAlmostEqual(base.thumb_index_distance, enlarged.thumb_index_distance)
        self.assertEqual(
            base.extended_non_thumb_count, mirrored.extended_non_thumb_count
        )

    def test_invalid_observation_and_degenerate_scale_are_rejected_before_division(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            extract_features(object())  # type: ignore[arg-type]
        hand = canonical_hand()
        object.__setattr__(hand, "palm_scale", 0)
        with self.assertRaises(ValueError):
            extract_features(hand)
