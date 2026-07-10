from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from gestureboard.services.landmark_normalizer import (
    LandmarkNormalizationError,
    LandmarkNormalizer,
)


def landmark(x: float, y: float, z: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


class LandmarkNormalizerTests(SimpleTestCase):
    def test_normalize_centres_wrist_and_scales_all_axes(self) -> None:
        landmarks = [landmark(1, 1), *[landmark(1, 1) for _ in range(8)]]
        landmarks.append(landmark(1, 3))
        landmarks.append(landmark(3, 1, 2))

        normalized = LandmarkNormalizer().normalize(landmarks)

        self.assertEqual(normalized[0].x, 0.0)
        self.assertEqual(normalized[0].y, 0.0)
        self.assertEqual(normalized[9].x, 0.0)
        self.assertEqual(normalized[9].y, 1.0)
        self.assertEqual(normalized[10].x, 1.0)
        self.assertEqual(normalized[10].z, 1.0)

    def test_normalize_is_invariant_to_translation_and_scale(self) -> None:
        base = [landmark(0, 0), *[landmark(0, 0) for _ in range(8)]]
        base.extend([landmark(0, 2), landmark(2, 0, 2)])
        transformed = [
            landmark(point.x * 4 + 10, point.y * 4 - 3, point.z * 4 + 7)
            for point in base
        ]

        normalizer = LandmarkNormalizer()
        self.assertEqual(normalizer.normalize(base), normalizer.normalize(transformed))

    def test_normalize_rejects_a_zero_scale_reference(self) -> None:
        landmarks = [landmark(0, 0) for _ in range(10)]

        with self.assertRaises(LandmarkNormalizationError):
            LandmarkNormalizer().normalize(landmarks)

    def test_normalize_requires_landmarks_through_the_scale_reference(self) -> None:
        with self.assertRaises(LandmarkNormalizationError):
            LandmarkNormalizer().normalize([landmark(0, 0)])
