# Gesture Mouse Alpha 4 unit layer

Alpha 4 defines a default-disabled button domain only. Button and drag settings
are parsed with the mouse configuration, but are not connected to the runtime
or WebSocket bridge yet. No application startup path can send native button
input.

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

`NullMouseButtonOutput` has no OS effect. `WindowsMouseButtonOutput` is guarded
by an injected SendInput boundary for deterministic tests; no real native call
is made by the unit suite. Failed native cleanup remains retryable and a failed
close does not discard logical held state. Buttons and drag are disabled by
default. There is no coordinator or bridge integration, no live Windows
click/drag test, and frontend and protocol version 1 remain unchanged.
Native-boundary exceptions are normalised to `MouseOutputError`.
Scrolling, keyboard input, double-clicking, and global hotkeys are not
implemented.
