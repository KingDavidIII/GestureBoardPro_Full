# Sprint 3 Alpha 1: Gesture Mouse foundation

Alpha 1 is an internal, transport-neutral state machine for safe gesture-mouse
control. It has no browser controls, WebSocket protocol changes, camera input,
MediaPipe dependency, screen-coordinate mapping, smoothing, or operating-system
input. In particular, it cannot move the Windows cursor, click, scroll, or send
keyboard input.

Its modes are `disabled`, `ready`, `active`, `paused`, and `closed`. Targets are
accepted only while `active`; pausing, tracking loss, disabling, emergency stop,
and shutdown clear retained targets and request a safety reset. Emergency stop
always returns the service to `disabled` and needs a deliberate later enable.
Shutdown is permanent and closes the event output once.

The output port receives immutable internal events only. The production default
is a null port with no external side effects. If an output fails, target delivery
is not reported as successful, the failed target is not retained, and emergency
stop or shutdown remain available.

Alpha 2 will adapt the already selected recognition hand's index-fingertip into
a normalised `CursorTarget`, then add virtual cursor mapping and smoothing. A
later stage may add separately reviewed Windows output and mouse buttons.
