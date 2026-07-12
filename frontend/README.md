# GestureBoard Pro frontend

Sprint 2 Alpha 4 adds deterministic WebSocket recovery to camera streaming and
optional annotated JPEG feedback. Sprint 2 is not yet complete.

## Commands

Run these from this directory:

```powershell
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run format:check
npm.cmd run test:run
npm.cmd run build
```

Set `VITE_GESTUREBOARD_WS_URL` to override the default URL. Without it, the
client uses `ws://<current-host>/ws/` or `wss://<current-host>/ws/` for HTTPS.
See `.env.example`.

## Camera and streaming

Camera permission is requested only after the **Start Camera** control is used.
The camera uses ideal (not mandatory) width, height, frame-rate, and facing-mode
constraints with audio disabled. The local preview is muted, inline, and never
sent directly over the network.

`CanvasFrameEncoder` reuses a canvas to produce JPEG `Blob`s. The stream
controller sends one encoded frame at a time at its configured target FPS
(default 8), skips timing-bound frames, and skips before encoding when the
WebSocket buffered byte count exceeds its threshold. It owns neither the camera
nor the WebSocket: stopping streaming does not stop or disconnect either.

Camera access requires HTTPS outside localhost and a browser that supports
`navigator.mediaDevices.getUserMedia`. On page shutdown, the app stops the
stream scheduler, stops camera tracks, then disconnects the client. The current
backend processes binary frames sequentially; no annotated-frame return stream
is implemented in this Alpha.

## Server frame scheduling

Each WebSocket connection accepts one frame in flight plus exactly one pending
frame. A newer submission replaces the older pending frame, reducing latency
rather than maximizing processed-frame count. `received_frames` counts every
transport-accepted submission; `dropped_frames` counts pending frames replaced
by newer ones; `processed_frames` counts completed attempts; and
`processing_failures` counts failed attempts. `pending_frames` is always `0`
or `1`. `queue_delay_ms` is submission-to-processing-start delay and
`processing_time_ms` is bridge-processing duration.

CPU-bound bridge work is offloaded so text controls remain responsive. JSON and
optional GBF1 frames are emitted as one ordered response pair. Disconnect
clears pending work and suppresses late sends; thread work already running may
finish but its result is ignored. These server counters are connection-local
and distinct from browser-side drop metrics. There is no global admission
control, adaptive browser-FPS negotiation, automatic stream resumption, or
interruption of an in-flight frame.

## Public API

```ts
const client = new GestureWebSocketClient(url, options);
await client.connect();
client.sendPing("request-id");
client.resetRuntime("request-id");
client.sendFrame(new Uint8Array(frameBytes));
client.disconnect();
```

`subscribe()` receives typed connection-state, server-message, protocol-error,
socket-error, socket-closed, and reconnect lifecycle events. `sendFrame()` accepts `Blob`,
`ArrayBuffer`, or `Uint8Array`, and rejects empty or oversized payloads.

## Connection recovery

Unexpected remote closure or connection failure schedules automatic recovery.
The default policy starts at 500 ms, doubles each failure, caps at 8000 ms,
stops after 5 attempts, and applies up to 20% jitter. Policy values, timers, and
randomness are injectable so tests remain deterministic. Manual **Connect**
during a scheduled retry cancels the timer and connects immediately.

Manual disconnect and client destruction always cancel pending retries and
never reconnect. All socket callbacks carry a monotonically increasing
connection epoch; late open, close, message, and annotated-frame work from an
old socket cannot mutate a newer session. The implementation treats every
remote close code as unexpected unless the local client initiated disconnect.

Connection loss clears confirmed annotation state and pending annotated frames.
After recovery, a fresh `connection.ready` capability advertisement and a new
explicit user opt-in are required. Annotation is never re-enabled
automatically. Frame streaming stops as soon as the socket leaves `OPEN` and
does not resume after reconnect; the camera may remain ready, but the user must
start streaming again. Retry exhaustion is reported in the diagnostic
dashboard and requires manual connection.

```ts
const camera = new CameraController();
await camera.attachPreview(videoElement);
await camera.start();

const encoder = new CanvasFrameEncoder({ jpegQuality: 0.8, maximumWidth: 640 });
const stream = new FrameStreamController(camera, encoder, client, {
  targetFps: 8,
});
stream.start();
stream.stop();
camera.stop();
```

## Protocol

All JSON control and server messages use `protocol_version: 1`. Incoming data
is parsed and runtime-validated before it becomes a `ServerMessage`; malformed,
unsupported-version, and unsupported-type messages produce typed client errors.
Annotated feedback is opt-in and disabled by default. A server advertises
`annotated_frame.jpeg.v1`; clients then send `annotated_frame.set` and wait for
`annotated_frame.set.ack`. Each `gesture.result` JSON metadata message is sent
before its optional correlated GBF1 binary JPEG frame, using the same sequence.

GBF1 uses a 20-byte network-order header: `GBF1`, version, kind, two reserved
zero bytes, uint32 sequence, uint16 width, uint16 height, uint32 JPEG length,
then the JPEG payload. Images never enter JSON and base64 is never used. The UI
uses a JPEG Blob/object URL and revokes URLs on replacement, disable,
disconnect, and destruction. Annotation increases CPU and bandwidth usage; the
backend continues to process frames sequentially.

## Adaptive frame rate

Frame streaming defaults to **Adaptive** mode. Validated `gesture.result`
scheduler metadata feeds an additive-increase/multiplicative-decrease controller
only while the WebSocket is open and streaming is active. **Fixed** mode keeps
the current target FPS and disables automatic changes without stopping the
stream. Switching back to Adaptive starts a fresh sampling epoch and never
starts the camera, stream, or connection automatically.

The default policy uses a minimum of 5 FPS and the configured initial stream
rate as its maximum. It decreases by a factor of 0.75 on overload, increases by
1 FPS after 8 healthy samples, and enforces a 1500 ms cooldown. Overload means a
positive delta in the cumulative server dropped-frame counter, pending depth of
1, queue delay of at least 80 ms, or estimated processing capacity below the
0.9 utilisation threshold. A zero processing duration does not produce an
infinite estimate.

Adaptive history resets when streaming stops, the socket leaves `OPEN`,
reconnection begins, Adaptive mode is disabled, counters regress, or the
application is destroyed. Reconnection never resumes streaming; the user must
restart it explicitly, and fresh scheduler samples establish a new baseline.

The dashboard keeps browser capture/send metrics, server scheduler metrics, and
adaptive-controller decisions distinct.

### Adaptive JPEG quality

JPEG quality has its own **Adaptive** and **Fixed** mode, independent of the
server-pressure FPS controller. The default transport policy starts from the
configured encoder quality, stays between 0.45 and 0.90, decreases by 0.10,
and restores 0.05 after 10 consecutive healthy samples. Adjustments have a
2000 ms cooldown.

Overload priority is send-failure delta, browser backpressure-drop delta,
WebSocket buffered bytes at 262144, then encoded payload size at 131072 bytes.
Drop and failure metrics are cumulative, so only positive deltas represent new
pressure; counter regression starts a fresh epoch. Encoding failures interrupt
the healthy window without being treated as server pressure.

Quality history resets on socket loss, reconnect start, stream stop, mode
change, counter regression, and application destruction. The last applied
quality is preserved across a manual stream restart, while a fresh sample is
required to establish the new baseline. Reconnection never resumes streaming.
The dashboard renders browser transport, server scheduler, adaptive FPS, and
adaptive JPEG-quality metrics separately. Alpha 7 changes only JPEG quality and
FPS: it does not negotiate codecs or estimate available bandwidth.

## Testing

Vitest runs in JSDOM. Camera, media stream, canvas, scheduler, clock, encoder,
and WebSocket tests inject fakes; no test starts a server, requests a real
camera, renders a native canvas, waits on wall-clock time, or accesses a network.
