from __future__ import annotations

import math
from unittest import TestCase

from gestureboard.recognition import (
    GestureClassifierPolicy,
    GestureId,
    HandFeatures,
    classify,
)
from gestureboard.recognition.classifier import classify_with_diagnostics


def point_features(
    *, index_margin: float = 0.151, little_margin: float = 0.0
) -> HandFeatures:
    return HandFeatures(
        False,
        True,
        False,
        False,
        False,
        2,
        1,
        1,
        4,
        index_tip_to_mcp_distance=1 + index_margin,
        index_pip_to_mcp_distance=1,
        little_tip_to_mcp_distance=1 + little_margin,
        little_pip_to_mcp_distance=1,
    )


class RecognitionClassifierTests(TestCase):
    def test_canonical_rules_and_precedence(self) -> None:
        pinch = HandFeatures(True, True, False, False, False, 0.1, 2, 1, 3)
        point = point_features()
        fist = HandFeatures(False, False, False, False, False, 2, 0, 0, 5)
        palm = HandFeatures(True, True, True, True, True, 2, 5, 4, 0)
        self.assertEqual(classify(pinch).gesture_id, GestureId.PINCH)
        self.assertEqual(classify(point).gesture_id, GestureId.POINT)
        self.assertEqual(classify(fist).gesture_id, GestureId.CLOSED_FIST)
        self.assertEqual(classify(palm).gesture_id, GestureId.OPEN_PALM)
        self.assertIsNone(classify(None))

    def test_policy_validation_and_unknown_fallback(self) -> None:
        with self.assertRaises(ValueError):
            GestureClassifierPolicy(pinch_distance_threshold=0)
        with self.assertRaises(ValueError):
            GestureClassifierPolicy(minimum_classification_confidence=2)
        unknown = HandFeatures(False, True, True, False, False, 2, 2, 2, 3)
        candidate = classify(unknown)
        self.assertEqual(candidate.gesture_id, GestureId.UNKNOWN)
        self.assertTrue(0 <= candidate.confidence <= 1)

    def test_candidate_diagnostics_threshold_and_precedence_are_deterministic(
        self,
    ) -> None:
        overlapping = HandFeatures(True, True, False, False, False, 0.1, 2, 1, 3)
        candidate = classify(overlapping)
        self.assertEqual(candidate.gesture_id, GestureId.PINCH)
        self.assertTrue(candidate.threshold_satisfied)
        self.assertEqual(candidate.reason, "thumb_index_distance")
        self.assertIn("extended_finger_count", candidate.diagnostics)
        with self.assertRaises(TypeError):
            candidate.diagnostics["new"] = 1  # type: ignore[index]
        strict = GestureClassifierPolicy(minimum_classification_confidence=0.95)
        self.assertEqual(classify(overlapping, strict).gesture_id, GestureId.UNKNOWN)

    def test_policy_rejects_non_finite_boolean_and_invalid_count_values(self) -> None:
        invalid = (
            {"minimum_usable_hand_confidence": math.nan},
            {"minimum_classification_confidence": math.inf},
            {"pinch_distance_threshold": -1},
            {"pinch_distance_threshold": math.nan},
            {"fist_maximum_extended_fingers": True},
            {"open_palm_minimum_extended_non_thumb_fingers": 5},
            {"point_index_min_extension_margin": True},
            {"point_index_min_extension_margin": math.nan},
            {"point_folded_finger_max_extension_margin": True},
            {"point_folded_finger_max_extension_margin": math.inf},
        )
        for values in invalid:
            with self.assertRaises(ValueError):
                GestureClassifierPolicy(**values)

    def test_fallback_diagnostics_are_scalar_finite_and_coordinate_free(self) -> None:
        candidate, diagnostics = classify_with_diagnostics(
            point_features(), frame_sequence=7, handedness="right"
        )

        self.assertEqual(candidate.gesture_id, GestureId.POINT)
        self.assertIsNotNone(diagnostics)
        values = diagnostics.as_log_fields()
        self.assertIn("extended_finger_count", values)
        self.assertTrue(values["point_predicate"])
        self.assertFalse(values["pinch_predicate"])
        self.assertEqual(values["final_candidate"], "point")
        self.assertTrue(all(not key.endswith(("_x", "_y", "_z")) for key in values))
        self.assertNotIn("landmark", repr(values).lower())
        self.assertTrue(
            all(
                not isinstance(value, float) or math.isfinite(value)
                for value in values.values()
            )
        )

    def test_fallback_diagnostics_record_pinch_precedence_over_point(self) -> None:
        overlapping = HandFeatures(
            True,
            True,
            False,
            False,
            False,
            0.1,
            2,
            1,
            3,
            index_tip_to_mcp_distance=1.151,
            index_pip_to_mcp_distance=1,
        )
        candidate, diagnostics = classify_with_diagnostics(
            overlapping, frame_sequence=1
        )

        self.assertEqual(candidate.gesture_id, GestureId.PINCH)
        self.assertTrue(diagnostics.values["pinch_predicate"])
        self.assertTrue(diagnostics.values["point_predicate"])
        self.assertEqual(diagnostics.values["final_candidate"], "pinch")

    def test_point_margin_boundaries_are_deliberate_and_do_not_change_other_rules(
        self,
    ) -> None:
        self.assertNotEqual(
            classify(point_features(index_margin=0.149)).gesture_id, GestureId.POINT
        )
        self.assertEqual(
            classify(point_features(index_margin=0.151)).gesture_id, GestureId.POINT
        )
        self.assertEqual(
            classify(point_features(little_margin=0.002)).gesture_id, GestureId.POINT
        )
        self.assertEqual(
            classify(point_features(little_margin=0.029)).gesture_id, GestureId.POINT
        )
        self.assertEqual(
            classify(point_features(little_margin=0.03)).gesture_id, GestureId.POINT
        )
        self.assertNotEqual(
            classify(point_features(little_margin=0.031)).gesture_id, GestureId.POINT
        )
        fist = HandFeatures(False, False, False, False, False, 2, 0, 0, 5)
        palm = HandFeatures(True, True, True, True, True, 2, 5, 4, 0)
        self.assertNotEqual(classify(fist).gesture_id, GestureId.POINT)
        self.assertEqual(classify(palm).gesture_id, GestureId.OPEN_PALM)
