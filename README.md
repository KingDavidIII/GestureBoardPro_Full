# GestureBoard Pro

GestureBoard Pro is a Python/Django gesture-recognition backend that turns
encoded hand-image frames into deterministic, explicitly configured keyboard
actions. Sprint 1 provides the complete synchronous backend pipeline and a
versioned WebSocket interface. The frontend is not yet complete.

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
or PNG images as binary WebSocket messages. It returns metadata-only
`gesture.result` messages; annotated frames and landmarks are not transported.

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
- Annotated frames are retained internally but not streamed to WebSocket
  clients.
- Camera capture and the incomplete frontend remain outside the WebSocket
  consumer.
