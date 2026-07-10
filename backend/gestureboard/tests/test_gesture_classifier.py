"""Tests for deterministic normalized-landmark gesture classification."""

from dataclasses import FrozenInstanceError, replace
from math import nan

from django.test import SimpleTestCase

from gestureboard.services.gesture_classifier import (
    GestureClassifier,
    GestureClassifierConfig,
    GestureClassifierError,
    GestureLabel,
)
from gestureboard.services.landmark_normalizer import NormalizedLandmark


def synthetic_hand(
    *,
    extended: set[str] | None = None,
    pinch: bool = False,
) -> tuple[NormalizedLandmark, ...]:
    """Build normalized, camera-independent landmarks in MediaPipe order."""

    extended = extended or set()
    coordinates = [(0.0, 0.0, 0.0)] * 21
    fingers = {
        "thumb": ((-0.3, 0.2), (-0.5, 0.4), (-0.9, 0.4), (-1.3, 0.4)),
        "index": ((-0.4, 0.8), (-0.4, 1.1), (-0.4, 1.6), (-0.4, 2.1)),
        "middle": ((0.0, 1.0), (0.0, 1.3), (0.0, 1.8), (0.0, 2.3)),
        "ring": ((0.4, 0.9), (0.4, 1.2), (0.4, 1.7), (0.4, 2.2)),
        "little": ((0.7, 0.7), (0.7, 1.0), (0.7, 1.4), (0.7, 1.8)),
    }
    starts = {"thumb": 1, "index": 5, "middle": 9, "ring": 13, "little": 17}
    folded_tips = {
        "thumb": (-0.25, 0.2),
        "index": (-0.4, 0.75),
        "middle": (0.0, 0.8),
        "ring": (0.4, 0.75),
        "little": (0.7, 0.65),
    }
    for name, finger in fingers.items():
        start = starts[name]
        chosen = list(finger)
        if name not in extended:
            chosen[-1] = folded_tips[name]
        for offset, (x, y) in enumerate(chosen):
            coordinates[start + offset] = (x, y, 0.0)
    if pinch:
        coordinates[4] = coordinates[8]
    return tuple(NormalizedLandmark(*point) for point in coordinates)


class GestureClassifierTests(SimpleTestCase):
    def setUp(self) -> None:
        self.classifier = GestureClassifier()

    def assert_label(self, expected: GestureLabel, **hand_options: object) -> None:
        prediction = self.classifier.classify(synthetic_hand(**hand_options))
        self.assertEqual(prediction.label, expected)

    def test_open_palm(self) -> None:
        self.assert_label(
            GestureLabel.OPEN_PALM,
            extended={"thumb", "index", "middle", "ring", "little"},
        )

    def test_fist(self) -> None:
        self.assert_label(GestureLabel.FIST)

    def test_point(self) -> None:
        self.assert_label(GestureLabel.POINT, extended={"index"})

    def test_peace(self) -> None:
        self.assert_label(GestureLabel.PEACE, extended={"index", "middle"})

    def test_pinch(self) -> None:
        self.assert_label(GestureLabel.PINCH, pinch=True)

    def test_unknown_for_unrecognised_finger_combination(self) -> None:
        self.assert_label(GestureLabel.UNKNOWN, extended={"index", "ring"})

    def test_requires_exactly_twenty_one_landmarks(self) -> None:
        for landmarks in (
            synthetic_hand()[:-1],
            synthetic_hand() + synthetic_hand()[:1],
        ):
            with self.subTest(count=len(landmarks)):
                with self.assertRaises(GestureClassifierError):
                    self.classifier.classify(landmarks)

    def test_rejects_non_finite_coordinates(self) -> None:
        landmarks = list(synthetic_hand())
        landmarks[7] = replace(landmarks[7], z=nan)
        with self.assertRaisesRegex(GestureClassifierError, "finite"):
            self.classifier.classify(landmarks)

    def test_pinch_has_precedence_over_open_palm(self) -> None:
        landmarks = synthetic_hand(
            extended={"thumb", "index", "middle", "ring", "little"},
            pinch=True,
        )
        self.assertEqual(self.classifier.classify(landmarks).label, GestureLabel.PINCH)

    def test_peace_has_precedence_over_point(self) -> None:
        prediction = self.classifier.classify(
            synthetic_hand(extended={"index", "middle"})
        )
        self.assertEqual(prediction.label, GestureLabel.PEACE)

    def test_features_and_prediction_are_immutable(self) -> None:
        prediction = self.classifier.classify(synthetic_hand())
        with self.assertRaises(FrozenInstanceError):
            prediction.label = GestureLabel.UNKNOWN

    def test_feature_distances_are_three_dimensional(self) -> None:
        landmarks = list(synthetic_hand(extended={"index"}))
        landmarks[8] = replace(landmarks[8], z=1.0)
        features = self.classifier.extract_features(landmarks)
        self.assertGreater(features.index.tip_distance, 2.1)

    def test_configuration_rejects_invalid_values(self) -> None:
        invalid_options = (
            {"pinch_distance": 0.0},
            {"extended_alignment": 1.1},
            {"folded_alignment": 0.9, "extended_alignment": 0.8},
            {"folded_distance_ratio": 2.0, "extended_distance_ratio": 1.2},
            {"pinch_distance": nan},
        )
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaises(GestureClassifierError):
                    GestureClassifierConfig(**options)
