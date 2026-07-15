from __future__ import annotations

import importlib
import sys
from unittest import TestCase


class RecognitionExportTests(TestCase):
    def test_public_surface_is_deliberate_and_transport_neutral(self) -> None:
        for name in tuple(sys.modules):
            if name.startswith(("django", "channels", "cv2", "mediapipe")):
                sys.modules.pop(name, None)
        recognition = importlib.import_module("gestureboard.recognition")
        required = {
            "Landmark3D",
            "HandObservation",
            "HandSelection",
            "Handedness",
            "adapt_hands",
            "select_primary",
            "HandFeatures",
            "extract_features",
            "GestureClassifierPolicy",
            "classify",
            "GestureCandidate",
            "GestureId",
            "GestureStabilizer",
            "GestureStabilizerPolicy",
            "GestureTransition",
            "TransitionKind",
        }
        self.assertTrue(required.issubset(recognition.__all__))
        self.assertEqual(len(recognition.__all__), len(set(recognition.__all__)))
        self.assertFalse(any(name.startswith("_") for name in recognition.__all__))
        self.assertFalse(
            any(
                name.startswith(("django", "channels", "cv2", "mediapipe"))
                for name in sys.modules
            )
        )
        self.assertFalse(hasattr(recognition, "_distance"))
