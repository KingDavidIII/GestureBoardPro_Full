# GestureBoard Pro frontend

Sprint 2 Alpha 2 adds user-initiated browser camera capture and JPEG frame
streaming on top of the typed WebSocket protocol client. Sprint 2 is not yet
complete: annotated-frame streaming and automatic reconnection are not present.

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
socket-error, and socket-closed events. `sendFrame()` accepts `Blob`,
`ArrayBuffer`, or `Uint8Array`, and rejects empty or oversized payloads.

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
Binary inbound messages are intentionally unsupported in Alpha 1.

## Testing

Vitest runs in JSDOM. Camera, media stream, canvas, scheduler, clock, encoder,
and WebSocket tests inject fakes; no test starts a server, requests a real
camera, renders a native canvas, waits on wall-clock time, or accesses a network.
