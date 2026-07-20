# Guarded Windows mouse validation

This manual harness closes the remaining Alpha 4 evidence gap for actual
Windows cursor, primary-click, drag, interruption-release, and cleanup output.
It uses the production `WindowsCursorOutput`, `WindowsMouseButtonOutput`, and
shared ownership lease and process-wide named Windows mutex. The same mutex is
enabled by production native-output composition, so the harness and server
fail closed instead of controlling the cursor concurrently. It does not
duplicate `SendInput`.

The mutex boundary uses explicit pointer-sized Win32 `HANDLE` signatures and
last-error handling. Repeated production dependency construction configures the
same lease idempotently; coordinators may coexist, but only the current owner
can emit native output.

Live validation is intentionally not part of automated test execution. A human
must run it from an interactive Windows desktop and review the resulting report
before Alpha 4 live Windows validation can be declared complete.

## Inspection and live commands

Dry-run is the default and never constructs native output:

```powershell
./.venv/Scripts/python.exe backend/manage.py validate_windows_mouse
```

The guarded live command is:

```powershell
./.venv/Scripts/python.exe backend/manage.py validate_windows_mouse `
  --live `
  --acknowledge "I-UNDERSTAND-THIS-CONTROLS-MY-MOUSE" `
  --scenario all `
  --countdown 5 `
  --json-report .local/windows-mouse-validation.json
```

Live mode requires Windows, an interactive terminal, an interactive desktop,
the exact acknowledgement, and a countdown from 3 through 30 seconds. It opens
its own topmost Tkinter window containing a yellow click target and a bounded
blue drag lane. Do not move focus away from that window during a scenario.

Press **Escape** or close the window for an emergency stop. Focus loss, timeout,
closure, cancellation, and operational failure all trigger independent cleanup:
`release_all`, cursor restoration, owned output closure, ownership release, and
window closure. Cleanup continues after an individual cleanup error, while the
original operational error remains in the report.

## Scenarios

- `cursor` moves between two visibly separated safe points, verifies each
  native `GetCursorPos` observation within tolerance, and holds the final point
  briefly before cleanup restoration. Tk motion is supporting evidence only.
- `click` requires exactly one primary press followed by one release.
- `drag` requires down, held movement through the bounded lane, displacement,
  and one final release.
- `interruption` starts a primary hold and proves `release_all` emits release.
- `all` runs every scenario in that order.

Generated coordinates must remain inside both the current virtual desktop and
the owned client area; they are rejected rather than silently clamped. Desktop
bounds are checked again immediately before native actions so monitor-layout
changes fail closed, even if the validation window would still fit. Click and
drag do not press until the native cursor-position boundary verifies their
target. Drag then waits for the real Tk down edge, verifies each native
waypoint while the button is commanded down, and waits for the real Tk up edge
at the final native position. Tk held-motion is supporting evidence only;
absent Tk motion cannot invalidate a drag whose native waypoints, bounded
button edges, safety bounds, and displacement all verify. Paths over the
conservative distance limit are rejected. A successful `SetCursorPos` whose
native position never reaches the target fails clearly rather than being
accepted as a silent no-op.
The harness attempts to restore the original cursor after every run.

## Report and troubleshooting

The optional JSON report is written atomically. It contains schema version, UTC
time, platform and Python versions, selected scenario, live/dry mode, countdown,
scenario observations, expected/actual coordinates and tolerance, cancellation
or operational error, cleanup errors, final release/restoration status, and the
overall result. Any scenario, cancellation, operational, release, restoration,
ownership, output-close, presentation, or window-close failure makes the
overall result fail and live mode exits non-zero after writing the requested
report. Dry-run reports never claim a pass. Reports contain no environment dump
or secrets.

If the command refuses live mode, confirm it is running in a normal interactive
Windows desktop terminal, use the exact acknowledgement, and keep the Tk window
focused. If a scenario fails, stop and inspect the report; do not repeatedly run
live output without understanding the failure.

Automated tests inject the window, clock, Escape checker, output acquisition,
cursor-position boundary, restoration boundary, and report writer. They never
call real `SetCursorPos` or `SendInput`, install hooks, or require a desktop.
