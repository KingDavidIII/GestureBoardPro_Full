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

Runtime/bridge construction now composes the validated cursor and button
outputs in one dependency-injectable factory. Buttons remain disabled by
default, and enabled buttons use the null output unless Windows output is
explicitly configured. Explicit Windows selection fails during construction on
unsupported platforms; the native cursor and SendInput boundaries are injected
in unit tests, so those tests perform no real native calls. The coordinator
owns only outputs it constructs through this composition path; supplied
coordinators remain externally owned.

Any explicit Windows cursor or button mode is validated during composition,
including while that feature is disabled; disabled features retain null runtime
outputs and do not construct native APIs. Active native output requires one
caller-supplied application-scoped ownership lease shared by every bridge.
Ownership is acquired before actionable cursor or button processing, so a
denied coordinator emits neither cursor movement nor button actions. Ownership
transfers only after lifecycle cleanup releases it. Construction failures clean
up already owned dependencies best-effort while preserving the original error.

Button-only native composition receives that exact same shared lease. Denied
frames do not advance actionable button state. A button-output failure performs
fail-closed cleanup, releases ownership, and leaves another coordinator free to
acquire it. Complete bridge construction is transactional: rollback preserves
the original construction error and never treats injected dependencies as owned,
including falsy injected objects. Protocol version 1 remains unchanged and no
WebSocket button-action transport exists; real Windows end-to-end behaviour is
still unverified and Alpha 4 remains incomplete.

Falsy injected native boundaries are retained through explicit `None` checks;
they are never replaced by duplicate production boundaries. Global mouse
enablement gates native button construction: disabled mouse configurations use
the null button output and construct no native API, although explicit Windows
modes are still validated. Composed click and drag paths, lease denial and
transfer, and failure cleanup are exercised with fake native boundaries only.
Factory and bridge rollback, falsy injected dependencies, and recursive
protocol isolation are also covered without real Windows input. Protocol
version 1 remains unchanged.

Runtime composition also preserves falsy injected mapper, cursor-output,
button-policy, and button-output dependencies through explicit `None` checks.
The fake-boundary suite covers composed clicks, movement during active drags,
owner denial and transfer, failure cleanup, rollback paths, falsy dependencies,
and recursive protocol isolation. No live Windows validation has been run.
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
