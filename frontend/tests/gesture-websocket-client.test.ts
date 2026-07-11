import { describe, expect, it, vi } from "vitest";

import {
  GestureWebSocketClient,
  GestureWebSocketClientError,
  GestureWebSocketClientErrorCode,
  WebSocketClientState,
} from "../src/websocket";
import { FakeWebSocket } from "./fake-websocket";

function createClient(): [GestureWebSocketClient, FakeWebSocket] {
  const socket = new FakeWebSocket();

  const client = new GestureWebSocketClient("ws://board.test/ws/", {
    socketFactory: () => socket,
  });

  return [client, socket];
}

async function connectClient(
  client: GestureWebSocketClient,
  socket: FakeWebSocket,
): Promise<void> {
  const connection = client.connect();

  socket.open();
  await connection;
}

function createAnnotatedEnvelope(sequence: number): ArrayBuffer {
  const payload = new Uint8Array([1, 2, 3]);
  const buffer = new ArrayBuffer(20 + payload.byteLength);
  const view = new DataView(buffer);

  for (const [index, value] of [..."GBF1"].entries()) {
    view.setUint8(index, value.charCodeAt(0));
  }

  view.setUint8(4, 1);
  view.setUint8(5, 1);
  view.setUint16(6, 0, false);
  view.setUint32(8, sequence, false);
  view.setUint16(12, 2, false);
  view.setUint16(14, 1, false);
  view.setUint32(16, payload.byteLength, false);

  new Uint8Array(buffer, 20).set(payload);

  return buffer;
}

describe("GestureWebSocketClient", () => {
  it("sends annotation controls only while open and confirms state on acknowledgement", async () => {
    const [client, socket] = createClient();

    expect(() => client.setAnnotatedFramesEnabled(true)).toThrow(
      GestureWebSocketClientError,
    );

    await connectClient(client, socket);

    client.setAnnotatedFramesEnabled(true, "annotation-1");

    expect(socket.sent).toContain(
      '{"protocol_version":1,"type":"annotated_frame.set","request_id":"annotation-1","enabled":true}',
    );
    expect(client.getAnnotatedFramesEnabled()).toBe(false);

    socket.message(
      '{"protocol_version":1,"type":"annotated_frame.set.ack","enabled":true,"request_id":"annotation-1"}',
    );

    expect(client.getAnnotatedFramesEnabled()).toBe(true);

    client.disconnect();

    expect(client.getAnnotatedFramesEnabled()).toBe(false);
    expect(client.getLatestAnnotatedFrame()).toBeNull();
  });

  it("manages connection lifecycle and control messages without a real socket", async () => {
    const [client, socket] = createClient();
    const states: WebSocketClientState[] = [];

    client.subscribe((event) => {
      if (event.type === "state.changed") {
        states.push(event.state);
      }
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
    const [client, socket] = createClient();

    await connectClient(client, socket);

    const frame = new Uint8Array([1, 2, 3]);

    client.sendFrame(frame);

    expect(socket.sent[0]).toEqual(frame);
    expect(() => client.sendFrame(new Uint8Array())).toThrow(
      GestureWebSocketClientError,
    );

    const limitedSocket = new FakeWebSocket();
    const limitedClient = new GestureWebSocketClient("ws://board.test/ws/", {
      maximumFrameSize: 2,
      socketFactory: () => limitedSocket,
    });

    await connectClient(limitedClient, limitedSocket);

    expect(() => limitedClient.sendFrame(new Uint8Array([1, 2, 3]))).toThrow(
      expect.objectContaining({
        code: GestureWebSocketClientErrorCode.FRAME_TOO_LARGE,
      }),
    );
  });

  it("emits typed protocol errors for malformed server messages", async () => {
    const [client, socket] = createClient();
    const errors: GestureWebSocketClientError[] = [];

    client.subscribe((event) => {
      if (event.type === "protocol.error") {
        errors.push(event.error);
      }
    });

    await connectClient(client, socket);

    socket.message("not json");

    expect(errors).toHaveLength(1);
    expect(errors[0]?.code).toBe(GestureWebSocketClientErrorCode.INVALID_JSON);
  });

  it("emits and retains valid annotated frames with monotonic sequence handling", async () => {
    const [client, socket] = createClient();
    const sequences: number[] = [];

    client.subscribe((event) => {
      if (event.type === "annotated-frame") {
        sequences.push(event.frame.sequence);
      }
    });

    await connectClient(client, socket);

    socket.message(createAnnotatedEnvelope(2));
    await Promise.resolve();

    socket.message(createAnnotatedEnvelope(2));
    socket.message(createAnnotatedEnvelope(1));
    socket.message(createAnnotatedEnvelope(3));
    await Promise.resolve();

    expect(sequences).toEqual([2, 3]);
    expect(client.getLatestAnnotatedFrame()).toMatchObject({
      sequence: 3,
      width: 2,
      height: 1,
      size: 3,
    });
  });

  it("isolates failing annotation subscribers and clears state on remote close", async () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    try {
      const [client, socket] = createClient();
      const receivedEventTypes: string[] = [];

      client.subscribe(() => {
        throw new Error("subscriber");
      });

      client.subscribe((event) => {
        receivedEventTypes.push(event.type);
      });

      await connectClient(client, socket);

      socket.message(createAnnotatedEnvelope(1));
      await Promise.resolve();

      expect(receivedEventTypes).toContain("annotated-frame");
      expect(consoleError).toHaveBeenCalled();

      socket.message(
        '{"protocol_version":1,"type":"annotated_frame.set.ack","enabled":true}',
      );

      expect(client.getAnnotatedFramesEnabled()).toBe(true);

      socket.remoteClose();

      expect(client.getAnnotatedFramesEnabled()).toBe(false);
      expect(client.getLatestAnnotatedFrame()).toBeNull();
    } finally {
      consoleError.mockRestore();
    }
  });
});
