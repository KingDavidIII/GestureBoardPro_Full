# Gesture Mouse Alpha 3 unit layer

Gesture mouse control is disabled by default. `GestureMouseRuntimeConfig` accepts
`GESTURE_MOUSE_ENABLED`, `GESTURE_MOUSE_OUTPUT_MODE`,
`GESTURE_MOUSE_VIRTUAL_WIDTH_PX`, `GESTURE_MOUSE_VIRTUAL_HEIGHT_PX`,
`GESTURE_MOUSE_MIRROR_X`, `GESTURE_MOUSE_MIRROR_Y`,
`GESTURE_MOUSE_SMOOTHING_ALPHA`, `GESTURE_MOUSE_DEAD_ZONE_RADIUS`,
`GESTURE_MOUSE_ACTIVE_LEFT`, `GESTURE_MOUSE_ACTIVE_TOP`,
`GESTURE_MOUSE_ACTIVE_RIGHT`, `GESTURE_MOUSE_ACTIVE_BOTTOM`, and
`GESTURE_MOUSE_MAX_OUTPUT_HZ`. Defaults are virtual mode, 1920×1080, full
camera region, no mirroring, exact mapping, no dead zone, and 60 Hz.

Virtual mode uses a null output port. Windows mode has a small injected
GetSystemMetrics/SetCursorPos boundary; it preserves the virtual desktop origin,
including negative monitor coordinates. Output is process-locally leased to one
owner, is rate limited from supplied timestamps, and resets on tracking loss or
source change. Emergency stop and output failure reset mapping/timing and release
the lease; shutdown also closes the output.

The per-connection `WebSocketRuntimeBridge` is now the composition boundary. It
loads configuration once, creates at most one coordinator, and forwards the
cached `RecognitionService` primary hand to it after normal recognition. The
same immutable selected-hand object therefore drives recognition, task-path
annotation, and cursor mapping; no second MediaPipe call, decode, adaptation,
or selection is performed. Mouse data is not added to protocol-v1 messages.

Movement additionally requires the existing stabilized recognition result to be
the canonical `point` gesture. Hand presence, raw candidates, pending
stabilisation, open palm, closed fist, pinch, unknown, unsupported categories,
and release all clear mouse tracking, smoothing, retained targets, and rate
history without disabling the configured coordinator. A later stable point
starts a fresh mapping sequence.

Default startup remains disabled and does not construct a Windows API, read
desktop metrics, acquire ownership, or move the cursor. Virtual mode maps the
cached hand with a null output. Explicitly enabled Windows mode uses the
virtual-desktop bounds and the process-local lease, so another Windows-mode
connection cannot take ownership. No-hand frames, stream/runtime resets, and
processing failures clear tracking, mapping, and rate-limit history. Output
failures emergency-stop only the mouse layer and leave the computed gesture
result deliverable. Disconnect closes the coordinator/output once and releases
ownership. Emergency stop remains callable internally; there is no global
hotkey, clicks, dragging, right-click, scrolling, or keyboard input.

For a controlled manual check, first run virtual-only mode:

```powershell
$env:GESTURE_MOUSE_ENABLED = "true"
$env:GESTURE_MOUSE_OUTPUT_MODE = "virtual"
$env:GESTURE_MOUSE_MIRROR_X = "true"
$env:GESTURE_MOUSE_MIRROR_Y = "false"
```

Then use the normal development backend command and confirm recognition and
annotation continue while the physical cursor remains still. Only after that,
with one browser tab/connection, no risky unrelated applications, the physical
mouse within reach, and the backend terminal ready for Ctrl+C, explicitly try
Windows mode with slow movement:

```powershell
$env:GESTURE_MOUSE_ENABLED = "true"
$env:GESTURE_MOUSE_OUTPUT_MODE = "windows"
$env:GESTURE_MOUSE_MAX_OUTPUT_HZ = "30"
$env:GESTURE_MOUSE_SMOOTHING_ALPHA = "0.5"
$env:GESTURE_MOUSE_MIRROR_X = "true"
$env:GESTURE_MOUSE_MIRROR_Y = "false"
```

Stop immediately if tracking-loss reset or ownership protection behaves
unexpectedly. Mirrored browser previews normally require `MIRROR_X=true`; keep
vertical mirroring false unless the camera orientation genuinely requires it.
Alpha 4 may add gesture-button recognition and safe button release only after
this Alpha 3 cursor test has been validated.
