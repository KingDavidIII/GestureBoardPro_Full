import { expect, it, vi } from "vitest";

import {
  CameraController,
  type EncodedFrame,
  type PreviewVideoElement,
} from "../src/camera";
import { FrameStreamController, type FrameScheduler } from "../src/streaming";
import { GestureWebSocketClient } from "../src/websocket";
import { FakeWebSocket } from "./fake-websocket";

class Scheduler implements FrameScheduler {
  callback: FrameRequestCallback | null = null;
  request(callback: FrameRequestCallback): number {
    this.callback = callback;
    return 1;
  }
  cancel(): void {
    this.callback = null;
  }
  run(timestamp: number): void {
    const callback = this.callback;
    this.callback = null;
    callback?.(timestamp);
  }
}

it("streams an explicitly started fake camera once and releases it on shutdown", async () => {
  const stop = vi.fn();
  const track = {
    readyState: "live",
    stop,
    getSettings: () => ({ width: 640, height: 480 }),
  } as unknown as MediaStreamTrack;
  const mediaStream = {
    getVideoTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
  const camera = new CameraController({
    mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(mediaStream) },
  });
  const preview: PreviewVideoElement = {
    srcObject: null,
    videoWidth: 640,
    videoHeight: 480,
    readyState: 4,
    play: async () => undefined,
  };
  await camera.attachPreview(preview);
  await camera.start();
  const socket = new FakeWebSocket();
  const client = new GestureWebSocketClient("ws://board.test/ws/", {
    socketFactory: () => socket,
  });
  const connection = client.connect();
  socket.open();
  await connection;
  const encoded: EncodedFrame = Object.freeze({
    blob: new Blob(["jpeg"], { type: "image/jpeg" }),
    width: 640,
    height: 480,
    size: 4,
    mimeType: "image/jpeg",
    capturedAt: 0,
  });
  const scheduler = new Scheduler();
  const stream = new FrameStreamController(
    camera,
    { encode: vi.fn().mockResolvedValue(encoded) },
    client,
    { scheduler, now: () => 100 },
  );
  stream.start();
  scheduler.run(100);
  await Promise.resolve();
  await Promise.resolve();
  expect(socket.sent).toHaveLength(1);
  stream.stop();
  scheduler.run(200);
  expect(socket.sent).toHaveLength(1);
  camera.stop();
  expect(stop).toHaveBeenCalledOnce();
});
