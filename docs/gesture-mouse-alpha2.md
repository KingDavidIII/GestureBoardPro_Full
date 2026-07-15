# Sprint 3 Alpha 2: virtual cursor mapping

Alpha 2 consumes only the existing selected `HandObservation` or
`HandSelection`; it never selects hands, runs MediaPipe, or reads a camera.
Landmark 8 (the index fingertip) is converted to a normalised `CursorTarget`.
Finite camera coordinates slightly outside the frame are clamped to `[0, 1]`;
NaN, infinity, booleans, malformed input, and no selected hand produce no
target.

The virtual mapper normalises against a configurable active camera region,
clamps outside-region samples to virtual edges, then applies optional X/Y
mirroring. It exponentially smooths the resulting virtual coordinates with
`alpha * current + (1 - alpha) * previous`, then applies a normalised Euclidean
dead zone before deterministically rounding to a virtual pixel surface. Equal
timestamps are accepted; lower timestamps for the same source hand are rejected.

Tracking loss calls `reset()`, which clears smoothing, retained output,
timestamp history, and source identity. A changed `source_index` also clears all
history before its first sample, preventing interpolation between hands.

This remains virtual only: no browser/protocol changes and no real cursor,
click, scroll, keyboard, monitor, or operating-system input. Alpha 3 may add
reviewed runtime integration and Windows output only after a separate safety
gate.
