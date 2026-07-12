import { expect, it, vi } from "vitest";

import {
  CameraController,
  type EncodedFrame,
  type PreviewVideoElement,
} from "../src/camera";
import {
  AdaptiveStreamController,
  AdaptiveStreamCoordinator,
  FrameStreamController,
  FrameStreamState,
  type FrameScheduler,
} from "../src/streaming";
import { GestureWebSocketClient } from "../src/websocket";
import { FakeWebSocket } from "./fake-websocket";

class Scheduler implements FrameScheduler {
  callback: FrameRequestCallback | null = null;
  requests = 0;
  request(callback: FrameRequestCallback): number {
    this.requests += 1;
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

it("coordinates validated scheduler feedback without restarting stream dependencies", async () => {
  const cameraStart = vi.fn();
  const camera = {
    getState: () => "READY",
    getPreview: () => ({ videoWidth: 640, videoHeight: 480 }),
    subscribe: () => () => undefined,
    start: cameraStart,
  } as never;
  const socket = new FakeWebSocket();
  const client = new GestureWebSocketClient("ws://board.test/ws/", {
    socketFactory: () => socket,
    reconnectPolicy: { enabled: false },
  });
  const connection = client.connect();
  socket.open();
  await connection;
  const scheduler = new Scheduler();
  const encoder = {
    encode: vi.fn().mockResolvedValue({
      blob: new Blob(["jpeg"]),
      width: 640,
      height: 480,
      size: 4,
      mimeType: "image/jpeg",
      capturedAt: 0,
    }),
  };
  const stream = new FrameStreamController(camera, encoder, client, {
    scheduler,
    targetFps: 8,
    now: () => 100,
  });
  let now = 0;
  const controller = new AdaptiveStreamController({ maximumFps: 8 }, () => now);
  const adaptive = new AdaptiveStreamCoordinator(controller, stream, client);
  stream.start();
  const loopsBeforeFeedback = scheduler.requests;
  const sendSample = (received: number, dropped: number, processingTime = 20) =>
    socket.message(
      JSON.stringify({
        protocol_version: 1,
        type: "gesture.result",
        sequence: received,
        timestamp: received,
        detected_hand_count: 0,
        selection: { decision: "NO_HANDS", identity: null },
        hand: null,
        gesture: { label: null, engine_decision: "NO_HAND" },
        action_executed: false,
        dispatch: null,
        scheduler: {
          received_frames: received,
          processed_frames: received - dropped,
          dropped_frames: dropped,
          processing_failures: 0,
          pending_frames: 0,
          queue_delay_ms: 0,
          processing_time_ms: processingTime,
        },
      }),
    );

  sendSample(1, 0);
  sendSample(2, 1);
  expect(stream.targetFps).toBe(6);
  expect(stream.getState()).toBe(FrameStreamState.STREAMING);
  expect(cameraStart).not.toHaveBeenCalled();
  expect(scheduler.requests).toBe(loopsBeforeFeedback);

  now = 2000;
  for (let received = 3; received <= 10; received += 1) sendSample(received, 1);
  expect(stream.targetFps).toBe(7);

  adaptive.setMode("fixed");
  sendSample(11, 2);
  expect(stream.targetFps).toBe(7);
  adaptive.setMode("adaptive");
  expect(controller.getState().hasBaseline).toBe(false);
  sendSample(12, 2);
  expect(stream.targetFps).toBe(7);

  stream.stop();
  sendSample(13, 3);
  expect(stream.targetFps).toBe(7);
  expect(controller.getState().hasBaseline).toBe(false);
  client.disconnect();
  expect(stream.getState()).toBe(FrameStreamState.STOPPED);
  expect(controller.getState().hasBaseline).toBe(false);
  adaptive.destroy();
});
