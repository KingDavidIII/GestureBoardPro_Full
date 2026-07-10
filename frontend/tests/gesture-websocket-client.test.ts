import { describe, expect, it } from "vitest";

import {
  GestureWebSocketClient,
  GestureWebSocketClientError,
  GestureWebSocketClientErrorCode,
  WebSocketClientState,
} from "../src/websocket";
import { FakeWebSocket } from "./fake-websocket";

function clientWithFakeSocket(): [GestureWebSocketClient, FakeWebSocket] {
  const socket = new FakeWebSocket();
  const client = new GestureWebSocketClient("ws://board.test/ws/", {
    socketFactory: () => socket,
  });
  return [client, socket];
}

describe("GestureWebSocketClient", () => {
  it("manages connection lifecycle and control messages without a real socket", async () => {
    const [client, socket] = clientWithFakeSocket();
    const states: string[] = [];
    client.subscribe((event) => {
      if (event.type === "state.changed") states.push(event.state);
    });

    const connection = client.connect();
    expect(client.getState()).toBe(WebSocketClientState.CONNECTING);
    socket.open();
    await expect(connection).resolves.toBeUndefined();
    client.sendPing("ping-1");
    client.resetRuntime("reset-1");
    socket.message(
      '{"protocol_version":1,"type":"pong","request_id":"ping-1"}',
    );
    client.disconnect();

    expect(socket.sent).toEqual([
      '{"protocol_version":1,"type":"ping","request_id":"ping-1"}',
      '{"protocol_version":1,"type":"runtime.reset","request_id":"reset-1"}',
    ]);
    expect(client.getLastMessage()?.type).toBe("pong");
    expect(states).toContain(WebSocketClientState.OPEN);
    expect(client.getState()).toBe(WebSocketClientState.CLOSED);
  });

  it("sends binary frames and enforces the outbound frame limit", async () => {
    const [client, socket] = clientWithFakeSocket();
    const connection = client.connect();
    socket.open();
    await connection;

    client.sendFrame(new Uint8Array([1, 2, 3]));
    expect(socket.sent[0]).toEqual(new Uint8Array([1, 2, 3]));
    expect(() => client.sendFrame(new Uint8Array())).toThrow(
      GestureWebSocketClientError,
    );

    const limitedSocket = new FakeWebSocket();
    const limited = new GestureWebSocketClient("ws://board.test/ws/", {
      maximumFrameSize: 2,
      socketFactory: () => limitedSocket,
    });
    const limitedConnection = limited.connect();
    limitedSocket.open();
    await limitedConnection;
    expect(() => limited.sendFrame(new Uint8Array([1, 2, 3]))).toThrow(
      expect.objectContaining({
        code: GestureWebSocketClientErrorCode.FRAME_TOO_LARGE,
      }),
    );
  });

  it("emits typed protocol errors for malformed server messages", async () => {
    const [client, socket] = clientWithFakeSocket();
    const errors: GestureWebSocketClientError[] = [];
    client.subscribe((event) => {
      if (event.type === "protocol.error") errors.push(event.error);
    });
    const connection = client.connect();
    socket.open();
    await connection;
    socket.message("not json");

    expect(errors[0]?.code).toBe(GestureWebSocketClientErrorCode.INVALID_JSON);
  });
});
