# Sprint 1 acceptance

## Automated validation

From `backend/`:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test gestureboard.tests -v 2
```

From the repository root:

```text
python -m pip check
python -m compileall -q backend
ruff check .
ruff format --check .
git diff --check
python -m pre_commit run --all-files
```

The Sprint 1 acceptance test uses real normalization, classification, pipeline,
temporal engine, dispatcher, keyboard controller, runtime, and bridge
serialization. Only detection output, encoded-frame decoding, and OS keyboard
execution are faked. It verifies activation, held suppression, neutral release,
re-arming, unmapped safety, transport safety, and lifecycle ownership.

## Live WebSocket smoke test

Start Daphne and connect a WebSocket client to `/ws/`. A successful connection
first receives:

```json
{"protocol_version":1,"type":"connection.ready"}
```

A valid binary JPEG/PNG frame receives one metadata response shaped like:

```json
{
  "protocol_version": 1,
  "type": "gesture.result",
  "sequence": 0,
  "timestamp": 123.456,
  "detected_hand_count": 1,
  "selection": {
    "decision": "FIRST_DETECTED",
    "identity": {"hand_index": 0, "handedness": "right"}
  },
  "hand": {
    "index": 0,
    "handedness": "Right",
    "detection_confidence": 0.98
  },
  "gesture": {"label": "POINT", "engine_decision": "ACCUMULATING"},
  "action_executed": false,
  "dispatch": null
}
```

Ping and reset controls produce:

```json
{"protocol_version":1,"type":"pong","request_id":"smoke-1"}
```

```json
{"protocol_version":1,"type":"runtime.reset.ack","request_id":"smoke-1"}
```

Invalid input should return a typed `error` without disconnecting the client.
Binary frames must never cause keyboard activity with the default empty action
mapping.

## Release acceptance criteria

- Django system and migration checks pass.
- The complete gestureboard test suite passes without webcam, real keyboard,
  database writes, external network services, or sleeps.
- Dependency, compilation, lint, format, diff, and pre-commit checks pass.
- Protocol output is JSON serializable and excludes frames, landmarks,
  exceptions, and backend objects.
- Per-connection runtime isolation, reset, error, and cleanup contracts pass.
- Production requires an explicit secret key.

## Known limitations

- Metadata identity is not persistent spatial hand tracking.
- Per-connection processing is sequential and does not drop frames.
- Annotated images are not returned through protocol version 1.
- The default action mapping is empty; keyboard behavior requires deliberate
  configuration.
- The frontend and live camera UX are not Sprint 1 acceptance deliverables.
