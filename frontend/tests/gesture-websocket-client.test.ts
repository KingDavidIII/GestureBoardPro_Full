import { describe, expect, it, vi } from "vitest";

import {
  GestureWebSocketClient,
  GestureWebSocketClientError,
  GestureWebSocketClientErrorCode,
  WebSocketClientState,
  calculateReconnectDelay,
  createReconnectPolicy,
  type GestureWebSocketClientEvent,
  type ReconnectTimerApi,
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

class FakeReconnectTimers implements ReconnectTimerApi {
  readonly delays: number[] = [];
  readonly cleared: unknown[] = [];
  private readonly callbacks = new Map<number, () => void>();
  private nextHandle = 1;

  set(callback: () => void, delayMs: number): unknown {
    const handle = this.nextHandle++;
    this.delays.push(delayMs);
    this.callbacks.set(handle, callback);
    return handle;
  }

  clear(handle: unknown): void {
    this.cleared.push(handle);
    if (typeof handle === "number") this.callbacks.delete(handle);
  }

  runNext(): void {
    const first = this.callbacks.entries().next().value as
      | [number, () => void]
      | undefined;
    if (!first) throw new Error("No reconnect timer is pending.");
    this.callbacks.delete(first[0]);
    first[1]();
  }

  get pendingCount(): number {
    return this.callbacks.size;
  }
}

function resilientClient(
  overrides: {
    maximumAttempts?: number;
    enabled?: boolean;
    jitterRatio?: number;
    random?: () => number;
  } = {},
): {
  client: GestureWebSocketClient;
  sockets: FakeWebSocket[];
  timers: FakeReconnectTimers;
  events: GestureWebSocketClientEvent[];
} {
  const sockets: FakeWebSocket[] = [];
  const timers = new FakeReconnectTimers();
  const client = new GestureWebSocketClient("ws://board.test/ws/", {
    socketFactory: () => {
      const socket = new FakeWebSocket();
      sockets.push(socket);
      return socket;
    },
    reconnectTimers: timers,
    random: overrides.random ?? (() => 0.5),
    reconnectPolicy: {
      enabled: overrides.enabled ?? true,
      initialDelayMs: 100,
      multiplier: 2,
      maximumDelayMs: 250,
      maximumAttempts: overrides.maximumAttempts ?? 5,
      jitterRatio: overrides.jitterRatio ?? 0,
    },
  });
  const events: GestureWebSocketClientEvent[] = [];
  client.subscribe((event) => events.push(event));
  return { client, sockets, timers, events };
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
      client.destroy();
    } finally {
      consoleError.mockRestore();
    }
  });

  it("validates policy and calculates deterministic bounded jitter", () => {
    const policy = createReconnectPolicy({
      initialDelayMs: 100,
      multiplier: 2,
      maximumDelayMs: 250,
      jitterRatio: 0.2,
    });
    expect(calculateReconnectDelay(policy, 1, 0)).toBe(80);
    expect(calculateReconnectDelay(policy, 2, 0.5)).toBe(200);
    expect(calculateReconnectDelay(policy, 3, 1)).toBe(250);
    expect(() => createReconnectPolicy({ multiplier: 0.5 })).toThrow();
    expect(() => createReconnectPolicy({ maximumAttempts: -1 })).toThrow();
    expect(() => createReconnectPolicy({ jitterRatio: 2 })).toThrow();
  });

  it("schedules one initial reconnect after an unexpected close", async () => {
    const { client, sockets, timers, events } = resilientClient();
    const connecting = client.connect();
    sockets[0]?.open();
    await connecting;

    sockets[0]?.remoteClose(1000, "server restart");

    expect(timers.delays).toEqual([100]);
    expect(timers.pendingCount).toBe(1);
    expect(events).toContainEqual({
      type: "reconnect.scheduled",
      attempt: 1,
      delayMs: 100,
    });
    sockets[0]?.remoteClose(1006, "duplicate");
    expect(timers.pendingCount).toBe(1);
    client.destroy();
  });

  it("allows only one active connection attempt", async () => {
    const { client, sockets } = resilientClient();
    const first = client.connect();

    await expect(client.connect()).rejects.toMatchObject({
      code: GestureWebSocketClientErrorCode.INVALID_STATE,
    });
    expect(sockets).toHaveLength(1);

    sockets[0]?.open();
    await first;
    expect(client.getState()).toBe(WebSocketClientState.OPEN);
    client.destroy();
  });

  it("uses exponential delays capped at the configured maximum", async () => {
    const { client, sockets, timers } = resilientClient();
    const first = client.connect();
    sockets[0]?.open();
    await first;
    sockets[0]?.remoteClose();

    timers.runNext();
    sockets[1]?.error();
    timers.runNext();
    sockets[2]?.error();
    timers.runNext();
    sockets[3]?.error();

    expect(timers.delays).toEqual([100, 200, 250, 250]);
    expect(sockets).toHaveLength(4);
    client.destroy();
  });

  it("resets attempts after a successful reconnect", async () => {
    const { client, sockets, timers, events } = resilientClient();
    const first = client.connect();
    sockets[0]?.open();
    await first;
    sockets[0]?.remoteClose();
    timers.runNext();
    sockets[1]?.open();
    await Promise.resolve();

    expect(client.getReconnectAttempt()).toBe(0);
    expect(events).toContainEqual({ type: "reconnect.succeeded", attempt: 1 });
    sockets[1]?.remoteClose();
    expect(timers.delays.at(-1)).toBe(100);
    client.destroy();
  });

  it("manual disconnect never retries and cancels a pending retry", async () => {
    const { client, sockets, timers, events } = resilientClient();
    const first = client.connect();
    sockets[0]?.open();
    await first;
    client.disconnect();
    sockets[0]?.remoteClose();
    expect(timers.pendingCount).toBe(0);

    const second = client.connect();
    sockets[1]?.open();
    await second;
    sockets[1]?.remoteClose();
    expect(timers.pendingCount).toBe(1);
    client.disconnect();

    expect(timers.pendingCount).toBe(0);
    expect(events.some((event) => event.type === "reconnect.cancelled")).toBe(
      true,
    );
  });

  it("destroy cancels retries and a disabled policy never schedules", async () => {
    const active = resilientClient();
    const first = active.client.connect();
    active.sockets[0]?.open();
    await first;
    active.sockets[0]?.remoteClose();
    active.client.destroy();
    expect(active.timers.pendingCount).toBe(0);

    const disabled = resilientClient({ enabled: false });
    const second = disabled.client.connect();
    disabled.sockets[0]?.open();
    await second;
    disabled.sockets[0]?.remoteClose();
    expect(disabled.timers.pendingCount).toBe(0);
    expect(disabled.sockets).toHaveLength(1);
    disabled.client.destroy();
  });

  it("reports exhaustion after the maximum retry attempts", async () => {
    const { client, sockets, timers, events } = resilientClient({
      maximumAttempts: 2,
    });
    const first = client.connect();
    sockets[0]?.open();
    await first;
    sockets[0]?.remoteClose();
    timers.runNext();
    sockets[1]?.error();
    timers.runNext();
    sockets[2]?.error();

    expect(timers.pendingCount).toBe(0);
    expect(events).toContainEqual({ type: "reconnect.exhausted", attempts: 2 });
    expect(sockets).toHaveLength(3);
    client.destroy();
  });

  it("ignores every late event from a stale connection epoch", async () => {
    const { client, sockets, timers, events } = resilientClient();
    const first = client.connect();
    const oldSocket = sockets[0];
    oldSocket?.open();
    await first;
    oldSocket?.remoteClose();
    timers.runNext();
    const currentSocket = sockets[1];
    currentSocket?.open();
    await Promise.resolve();
    const eventCount = events.length;

    oldSocket?.open();
    oldSocket?.remoteClose();
    oldSocket?.message(
      '{"protocol_version":1,"type":"pong","request_id":"stale"}',
    );
    oldSocket?.message(createAnnotatedEnvelope(99));
    await Promise.resolve();
    await Promise.resolve();

    expect(events).toHaveLength(eventCount);
    expect(client.getLastMessage()?.type).not.toBe("pong");
    expect(client.getLatestAnnotatedFrame()).toBeNull();
    expect(client.getState()).toBe(WebSocketClientState.OPEN);
    client.destroy();
  });

  it("resets annotation epoch state and requires fresh opt-in after reconnect", async () => {
    const { client, sockets, timers } = resilientClient();
    const first = client.connect();
    sockets[0]?.open();
    await first;
    sockets[0]?.message(
      '{"protocol_version":1,"type":"annotated_frame.set.ack","enabled":true}',
    );
    sockets[0]?.message(createAnnotatedEnvelope(1));
    await Promise.resolve();
    expect(client.getAnnotatedFramesEnabled()).toBe(true);
    expect(client.getLatestAnnotatedFrame()).not.toBeNull();

    sockets[0]?.remoteClose();
    expect(client.getAnnotatedFramesEnabled()).toBe(false);
    expect(client.getLatestAnnotatedFrame()).toBeNull();
    timers.runNext();
    sockets[1]?.open();
    await Promise.resolve();

    expect(sockets[1]?.sent).toEqual([]);
    expect(client.getAnnotatedFramesEnabled()).toBe(false);
    client.destroy();
  });

  it("manual connect replaces a scheduled retry with one immediate socket", async () => {
    const { client, sockets, timers } = resilientClient();
    const first = client.connect();
    sockets[0]?.open();
    await first;
    sockets[0]?.remoteClose();

    const manual = client.connect();
    expect(timers.pendingCount).toBe(0);
    expect(sockets).toHaveLength(2);
    sockets[1]?.open();
    await manual;
    expect(client.getState()).toBe(WebSocketClientState.OPEN);
    client.destroy();
  });
});
