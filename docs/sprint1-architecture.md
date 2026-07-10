# Sprint 1 architecture

## Processing flow

```text
Client binary frame
→ Channels GestureConsumer
→ WebSocketRuntimeBridge
→ GestureRuntime
→ GesturePipeline
→ LandmarkProcessor
→ LandmarkNormalizer
→ GestureClassifier
→ GestureEngine
→ ActionDispatcher
→ KeyboardController
```

The consumer receives binary JPEG/PNG payloads at `/ws/`. It owns one bridge
per connection and runs synchronous bridge operations through Channels'
thread-sensitive `sync_to_async` adapter. Frames and control messages remain
ordered within a connection.

`WebSocketRuntimeBridge` validates payloads, decodes BGR frames, invokes the
runtime once, and serializes protocol-v1 metadata. It does not transport the
annotated image, landmarks, NumPy arrays, exceptions, or keyboard objects.

`GestureRuntime` invokes the pipeline, selects at most one hand, creates a
gesture or no-hand observation, and advances the temporal engine. Selection is
deterministic and sticky by default. Identity is the available `(hand_index,
normalized_handedness)` metadata, not a persistent spatial tracking ID.

`GesturePipeline` owns frame-to-prediction orchestration. `LandmarkProcessor`
performs MediaPipe inference, `LandmarkNormalizer` produces wrist-relative and
scale-normalized 3D points while preserving rotation, and `GestureClassifier`
applies deterministic geometric rules.

`GestureEngine` filters low-detection-confidence and unstable observations,
requires configured activation/release frames, suppresses held gestures, and
applies cooldown and opt-in repeat policies. No-hand frames are explicit neutral
observations and contribute to release.

`ActionDispatcher` maps labels to immutable keyboard actions. Its default map
is empty, preventing unexpected input. `KeyboardController` alone understands
keyboard execution and lazily creates the pynput backend.

## Ownership and lifecycle

- The consumer owns and closes its per-connection bridge.
- A bridge owns its default runtime and decoder.
- A runtime owns its default pipeline and engine.
- A pipeline owns only dependencies it creates.
- An engine owns its default dispatcher; a dispatcher owns its default keyboard
  controller; a controller owns its default backend.
- Injected dependencies always remain caller-owned.
- Close operations are idempotent and outer services reject work after closure.

Camera capture is not part of the WebSocket consumer. There are no background
frame queues, detached tasks, database persistence, or global gesture-runtime
singletons. The global WebSocket manager tracks connections only; temporal and
keyboard state remain connection-local.

## Protocol and safety boundaries

Protocol version 1 supports binary JPEG/PNG frames plus `ping` and
`runtime.reset` JSON controls. Results contain selection, hand, gesture, engine,
dispatch, sequence, and timestamp metadata. Error envelopes use stable codes
and omit tracebacks and internal object representations.

Automated tests replace external boundaries: webcam/MediaPipe detection,
encoded-frame decoding where appropriate, monotonic time, and operating-system
keyboard execution. Internal orchestration and deterministic gesture behavior
are exercised with real implementations.
