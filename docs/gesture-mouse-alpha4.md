# Gesture Mouse Alpha 4 unit layer

Alpha 4 defines a default-disabled button domain only. Button and drag settings
are parsed with the mouse configuration and integrated into the coordinator
through an injected button-output port. Coordinator button integration and
stable-`GestureId` forwarding from the WebSocket bridge exist: the coordinator
reuses the selected hand and recognition result already produced for the frame,
without a second decode, recognition, adaptation, selection, or stability pass.
This forwarding is not WebSocket button-action transport: button decisions and
actions are deliberately not serialized in protocol version 1 responses.

The detector accepts an already-selected `HandObservation`; the controller
accepts `MouseButtonIntent` decisions. Primary contact must first remain valid
for activation dwell, then a confirmed normal release emits a primary click.
Secondary click is activation-triggered and its confirmed release only rearms.
Contact acceptance requires `d_candidate <= threshold` and
`d_candidate <= d_competing * contact_isolation_ratio`; the active contact
alone may use release hysteresis. A drag emits `PRIMARY_DOWN` after its hold
duration and `PRIMARY_UP` on release or any safety interruption.

A fresh contact during cooldown is `SUPPRESSED`. A suppressed held contact does
not activate merely because the cooldown expires: a genuine, dwell-confirmed
release and a fresh contact epoch are required. Ambiguous, malformed, reset,
source-change, emergency-stop, and shutdown paths are safety interruptions and
never fabricate a click. Unsafe interruptions preserve the release-required
latch; repeated reset cannot bypass it. Secondary remains latched until a
confirmed `NONE` release. Invalid intent values are rejected before state
mutation. Direct safety-operation timestamps must be non-negative integer and
monotonic relative to accepted controller time.
Missing or malformed hands are `AMBIGUOUS`, never normal `NONE` release.
Actionable source indices are non-negative integers; `None`, booleans, and
negative values are rejected. Every drag-ending `PRIMARY_UP` begins cooldown.

`NullMouseButtonOutput` remains the default and has no OS effect. `WindowsMouseButtonOutput` is guarded
by an injected SendInput boundary for deterministic tests; no real native call
is made by the unit suite. Failed native cleanup remains retryable and a failed
close does not discard logical held state. Buttons and drag are disabled by
default. There is no button-action WebSocket transport and no live Windows
click/drag test; frontend and protocol version 1 remain unchanged.
Native-boundary exceptions are normalised to `MouseOutputError`.
Scrolling, keyboard input, double-clicking, and global hotkeys are not
implemented.

`POINT` permits cursor movement and button geometry, and cursor movement
continues during a primary drag. Missing, malformed, invalid-source, and
non-`POINT` inputs fail closed. Button-action failures preserve the original
action error even if cleanup also fails; cursor-output failures likewise
preserve their original error.

Coordinator `tracking_lost()` and `emergency_stop()` independently attempt
button interruption, button-output cleanup, service reset, mapper reset, and
ownership release. Coordinator `close()` independently attempts button
shutdown and output cleanup, service shutdown, cursor-output close, and
ownership release. Button output is released and closed even when buttons are
disabled. Bridge close likewise attempts every owned dependency and wraps the
first failure cause.

Equal timestamps are accepted; stale timestamps are rejected. Real Windows
click/drag behaviour remains untested, and Alpha 4 is not complete.
