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
  request(callback: FrameRequestCallback): number {
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
