# GestureBoard Pro frontend

Sprint 2 Alpha 1 supplies a typed WebSocket protocol client and a browser-based
diagnostic dashboard. It does not access the camera: frame bytes are accepted
only through the public client API.

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

## Protocol

All JSON control and server messages use `protocol_version: 1`. Incoming data
is parsed and runtime-validated before it becomes a `ServerMessage`; malformed,
unsupported-version, and unsupported-type messages produce typed client errors.
Binary inbound messages are intentionally unsupported in Alpha 1.

## Testing

Vitest runs in JSDOM. The client and dashboard tests inject `FakeWebSocket`; no
test starts a WebSocket server or accesses a camera.
