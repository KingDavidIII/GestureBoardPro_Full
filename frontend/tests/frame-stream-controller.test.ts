import { describe, expect, it, vi } from "vitest";

import {
  CameraState,
  type CameraEvent,
  type EncodedFrame,
  type PreviewVideoElement,
} from "../src/camera";
import {
  FrameStreamController,
  FrameStreamState,
  type FrameScheduler,
  type StreamCameraController,
  type StreamWebSocketClient,
} from "../src/streaming";
import {
  WebSocketClientState,
  type GestureWebSocketClientEvent,
} from "../src/websocket";

class Scheduler implements FrameScheduler {
  callback: FrameRequestCallback | null = null;
  cancelled = false;
  requests = 0;
  request(callback: FrameRequestCallback): number {
    this.requests += 1;
    this.callback = callback;
    return 1;
  }
  cancel(): void {
    this.cancelled = true;
    this.callback = null;
  }
  run(timestamp: number): void {
    const callback = this.callback;
    this.callback = null;
    callback?.(timestamp);
  }
}

class Camera implements StreamCameraController {
  state = CameraState.READY;
  preview: PreviewVideoElement = {
    srcObject: null,
    videoWidth: 640,
    videoHeight: 480,
    readyState: 4,
    play: async () => undefined,
  };
  listeners: Array<(event: CameraEvent) => void> = [];
  getState(): CameraState {
    return this.state;
  }
  getPreview(): PreviewVideoElement {
    return this.preview;
  }
  subscribe(listener: (event: CameraEvent) => void): () => void {
    this.listeners.push(listener);
    return () => undefined;
  }
}
class Client implements StreamWebSocketClient {
  state = WebSocketClientState.OPEN;
  buffered = 0;
  sent = vi.fn();
  listeners: Array<(event: GestureWebSocketClientEvent) => void> = [];
  getState(): WebSocketClientState {
    return this.state;
  }
  getBufferedAmount(): number {
    return this.buffered;
  }
  sendFrame(payload: Blob): void {
    this.sent(payload);
  }
  subscribe(
    listener: (event: GestureWebSocketClientEvent) => void,
  ): () => void {
    this.listeners.push(listener);
    return () => undefined;
  }
}
const frame: EncodedFrame = Object.freeze({
  blob: new Blob(["a"], { type: "image/jpeg" }),
  width: 10,
  height: 10,
  size: 1,
  mimeType: "image/jpeg",
  capturedAt: 0,
});

describe("FrameStreamController", () => {
  const createStream = (targetFps = 8) => {
    const camera = new Camera();
    const client = new Client();
    const scheduler = new Scheduler();
    const encoder = { encode: vi.fn().mockResolvedValue(frame) };
    const stream = new FrameStreamController(camera, encoder, client, {
      scheduler,
      now: () => 100,
      targetFps,
    });
    return { camera, client, scheduler, encoder, stream };
  };

  it("changes target FPS before streaming and exposes it in metrics", () => {
    const { stream } = createStream();
    stream.setTargetFps(12);
    expect(stream.targetFps).toBe(12);
    expect(stream.getMetrics().targetFps).toBe(12);
  });

  it("uses an updated FPS for future scheduling in the same loop", async () => {
    const { stream, scheduler, encoder } = createStream(10);
    stream.start();
    scheduler.run(100);
    await Promise.resolve();
    await Promise.resolve();
    stream.setTargetFps(20);
    const requestsBefore = scheduler.requests;
    scheduler.run(140);
    expect(encoder.encode).toHaveBeenCalledOnce();
    scheduler.run(150);
    await Promise.resolve();
    expect(encoder.encode).toHaveBeenCalledTimes(2);
    expect(scheduler.requests).toBeGreaterThanOrEqual(requestsBefore);
    stream.stop();
  });

  it("does not create a second loop when FPS changes repeatedly", () => {
    const { stream, scheduler } = createStream();
    stream.start();
    expect(scheduler.requests).toBe(1);
    stream.setTargetFps(9);
    stream.setTargetFps(10);
    expect(scheduler.requests).toBe(1);
    stream.stop();
  });

  it("preserves sent and drop counters across target changes", async () => {
    const { stream, scheduler, client } = createStream();
    stream.start();
    scheduler.run(100);
    await Promise.resolve();
    await Promise.resolve();
    client.buffered = Number.MAX_SAFE_INTEGER;
    scheduler.run(300);
    const before = stream.getMetrics();
    stream.setTargetFps(4);
    expect(stream.getMetrics()).toMatchObject({
      framesSent: before.framesSent,
      framesDroppedForBackpressure: before.framesDroppedForBackpressure,
    });
    stream.stop();
  });

  it("setting the same FPS is idempotent", () => {
    const { stream } = createStream(8);
    const listener = vi.fn();
    stream.subscribe(listener);
    stream.setTargetFps(8);
    expect(listener).not.toHaveBeenCalled();
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY, 0, -1])(
    "rejects invalid target FPS %s",
    (targetFps) => {
      const { stream } = createStream();
      expect(() => stream.setTargetFps(targetFps)).toThrow();
      expect(stream.targetFps).toBe(8);
    },
  );

  it("does not duplicate an encode already in flight", async () => {
    let resolve!: (value: EncodedFrame) => void;
    const pending = new Promise<EncodedFrame>((done) => {
      resolve = done;
    });
    const { stream, scheduler, encoder } = createStream();
    encoder.encode.mockReturnValue(pending);
    stream.start();
    scheduler.run(100);
    stream.setTargetFps(20);
    scheduler.run(150);
    expect(encoder.encode).toHaveBeenCalledOnce();
    resolve(frame);
    await Promise.resolve();
    await Promise.resolve();
    stream.stop();
  });

  it("stops deterministically after multiple FPS changes", () => {
    const { stream, scheduler } = createStream();
    stream.start();
    stream.setTargetFps(4);
    stream.setTargetFps(16);
    stream.stop();
    expect(scheduler.cancelled).toBe(true);
    scheduler.run(1000);
    expect(stream.getState()).toBe(FrameStreamState.STOPPED);
  });

  it("keeps the configured target while preserving dependency ownership", () => {
    const { stream, camera, client } = createStream();
    stream.setTargetFps(24);
    stream.start();
    stream.stop();
    expect(stream.targetFps).toBe(24);
    expect(camera.state).toBe(CameraState.READY);
    expect(client.state).toBe(WebSocketClientState.OPEN);
  });

  it("encodes and sends one scheduled frame without owning dependencies", async () => {
    const camera = new Camera();
    const client = new Client();
    const scheduler = new Scheduler();
    const encoder = { encode: vi.fn().mockResolvedValue(frame) };
    const stream = new FrameStreamController(camera, encoder, client, {
      scheduler,
      now: () => 100,
      targetFps: 8,
    });
    stream.start();
    scheduler.run(100);
    await Promise.resolve();
    await Promise.resolve();
    expect(encoder.encode).toHaveBeenCalledOnce();
    expect(client.sent).toHaveBeenCalledWith(frame.blob);
    expect(stream.getMetrics().framesSent).toBe(1);
    stream.stop();
    expect(stream.getState()).toBe(FrameStreamState.STOPPED);
    expect(camera.state).toBe(CameraState.READY);
    expect(client.state).toBe(WebSocketClientState.OPEN);
  });

  it("skips backpressured frames before encoding and rejects unavailable dependencies", () => {
    const camera = new Camera();
    const client = new Client();
    const scheduler = new Scheduler();
    const encoder = { encode: vi.fn().mockResolvedValue(frame) };
    client.buffered = 10;
    const stream = new FrameStreamController(camera, encoder, client, {
      scheduler,
      now: () => 100,
      bufferedAmountThreshold: 1,
    });
    stream.start();
    scheduler.run(100);
    expect(encoder.encode).not.toHaveBeenCalled();
    expect(stream.getMetrics().framesDroppedForBackpressure).toBe(1);
    camera.state = CameraState.STOPPED;
    expect(() =>
      new FrameStreamController(camera, encoder, client).start(),
    ).toThrow();
  });

  it("stops after an encoding failure without sending a queued frame", async () => {
    const scheduler = new Scheduler();
    const client = new Client();
    const stream = new FrameStreamController(
      new Camera(),
      { encode: vi.fn().mockRejectedValue(new Error("bad frame")) },
      client,
      { scheduler, now: () => 100 },
    );
    stream.start();
    scheduler.run(100);
    await Promise.resolve();
    await Promise.resolve();
    expect(stream.getMetrics().encodingFailures).toBe(1);
    expect(client.sent).not.toHaveBeenCalled();
    expect(stream.getState()).toBe(FrameStreamState.STOPPED);
  });
});
