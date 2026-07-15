"""Adapt an already-selected recognition hand into a camera-space target."""

from __future__ import annotations

from math import isfinite

from gestureboard.recognition.observations import HandObservation, HandSelection

from .models import CursorTarget

INDEX_FINGERTIP_LANDMARK_INDEX = 8


def cursor_target_from_selected_hand(
    selected: HandSelection | HandObservation | None,
    *,
    timestamp_ms: int,
) -> CursorTarget | None:
    """Extract landmark 8 without selecting, retaining, or invoking MediaPipe.

    Finite camera coordinates are clamped to the camera frame before the Alpha
    1 target model validates them. Invalid or absent selected-hand input maps to
    ``None`` so the caller can request a tracking reset without leaking data.
    """

    hand = selected.primary_hand if isinstance(selected, HandSelection) else selected
    if not isinstance(hand, HandObservation):
        return None
    try:
        point = hand.landmarks[INDEX_FINGERTIP_LANDMARK_INDEX]
        x, y = point.x, point.y
    except (AttributeError, IndexError, TypeError):
        return None
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
        or not isfinite(x)
        or not isfinite(y)
    ):
        return None
    try:
        return CursorTarget(
            min(1.0, max(0.0, float(x))),
            min(1.0, max(0.0, float(y))),
            timestamp_ms,
            hand.source_index,
        )
    except ValueError:
        return None
