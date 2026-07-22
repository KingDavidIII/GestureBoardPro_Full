# GestureBoard Pro

GestureBoard Pro is a Python/Django gesture-recognition backend that turns
encoded hand-image frames into deterministic, explicitly configured keyboard
actions. Sprint 1 provides the complete synchronous backend pipeline and a
versioned WebSocket interface with a browser diagnostic frontend.

The classifier is rule-based, not machine-learning based. MediaPipe supplies
hand landmarks; project-owned rules classify normalized landmark geometry.

## Requirements and setup

- Python 3.11
- A platform supported by MediaPipe, OpenCV, and pynput

From the repository root:

```text
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
copy .env.example .env
```

On POSIX shells, activate with `source .venv/bin/activate` and copy the
environment file with `cp .env.example .env`. Replace `DJANGO_SECRET_KEY` with
a private value before production use. The environment controls
`DJANGO_ENV`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, and
`DJANGO_ALLOWED_HOSTS`. Production refuses the development fallback secret.

## Validation and startup

Run Django commands from `backend/`:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test gestureboard.tests -v 2
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

Repository-wide quality checks run from the repository root:

```text
python -m pip check
python -m compileall -q backend
ruff check .
ruff format --check .
python -m pre_commit run --all-files
```

## WebSocket protocol

Connect to `ws://127.0.0.1:8000/ws/`. Protocol version 1 accepts encoded JPEG
or PNG images as binary WebSocket messages. It returns `gesture.result` JSON
messages and optional correlated annotated GBF1 JPEG frames; landmarks are not
transported.

Text control messages are JSON:

```json
{"protocol_version":1,"type":"ping","request_id":"client-1"}
```

```json
{"protocol_version":1,"type":"runtime.reset","request_id":"client-1"}
```

Responses include `connection.ready`, `pong`, `runtime.reset.ack`,
`gesture.result`, and typed `error` envelopes. Each connection owns an isolated
runtime and temporal state.

### Recognition metadata

`connection.ready` advertises `gesture.recognition.v1`. A `gesture.result` may
include a nullable `recognition` object (schema version 1). Recognition is
deterministic, rule-based metadata for `open_palm`, `closed_fist`, `point`,
`pinch`, or `unknown`; it is not sign-language recognition and does not produce
keyboard or mouse input. It uses normalized geometry and one deterministic
primary hand (detection confidence, handedness confidence, palm area, then
source index). Rule precedence is pinch, point, fist, then open palm.

The object contains only scalar hand, candidate, stable, and transition data;
raw landmarks are never transmitted. Activation/change require confirmation,
release requires the no-hand release window, and stream reset, disconnect,
sequence regression, or a long gap clears its per-connection state. Recognition
uses the same single MediaPipe result as annotation. A recognition-only failure
returns `"recognition": null` while preserving the frame result, scheduler
metadata, and optional annotated GBF1 feedback.

## Architecture and safety

```text
Client binary frame
→ Channels consumer
→ WebSocketRuntimeBridge
→ GestureRuntime
→ GesturePipeline
→ landmark detection and normalization
→ deterministic GestureClassifier
→ temporal GestureEngine
→ ActionDispatcher
→ KeyboardController
```

The default gesture-to-action mapping is intentionally empty. Therefore the
default server does not generate keyboard actions. Real keyboard input requires
deliberate application configuration that maps selected gesture labels to
validated keyboard actions.

See [Sprint 1 architecture](docs/sprint1-architecture.md) and
[Sprint 1 acceptance](docs/sprint1-acceptance.md) for detailed contracts.

## Known limitations

- Hand identity uses hand index plus normalized handedness metadata. It is not
  persistent spatial tracking and may change after occlusion or reindexing.
- Frames are processed sequentially per connection without frame dropping or a
  backpressure queue.
- Run the deterministic loopback acceptance path with
  `node scripts/run-alpha7-integrated-runtime-acceptance.mjs`.
- Camera capture and the incomplete frontend remain outside the WebSocket
  consumer.
# Alpha 9.1 gesture recognition

GestureBoard Pro uses the packaged MediaPipe Gesture Recognizer Task model for
primary static-gesture recognition. The model is neither trained nor downloaded
at runtime. `GESTURE_RECOGNIZER_MODEL_PATH` can supply an optional local model
path; when Task initialisation fails, the deterministic rule classifier is used
once as the startup fallback. Custom local gesture-model training and capture
are not part of the supported Alpha 9 architecture.
