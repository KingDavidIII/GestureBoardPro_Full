"""Deterministic, transport-neutral gesture-recognition primitives."""

from .classifier import GestureClassifierPolicy, classify
from .geometry import HandFeatures, extract_features
from .models import GestureCandidate, GestureId, GestureTransition, TransitionKind
from .observations import (
    Handedness,
    HandObservation,
    HandSelection,
    Landmark3D,
    adapt_hands,
    select_primary,
)
from .service import RecognitionFrameResult, RecognitionService, serialize_recognition
from .stabilizer import GestureStabilizer, GestureStabilizerPolicy

__all__ = [
    "GestureCandidate",
    "GestureId",
    "GestureStabilizer",
    "GestureStabilizerPolicy",
    "GestureTransition",
    "TransitionKind",
    "GestureClassifierPolicy",
    "HandFeatures",
    "HandObservation",
    "HandSelection",
    "Handedness",
    "Landmark3D",
    "RecognitionFrameResult",
    "RecognitionService",
    "adapt_hands",
    "classify",
    "extract_features",
    "select_primary",
    "serialize_recognition",
]
