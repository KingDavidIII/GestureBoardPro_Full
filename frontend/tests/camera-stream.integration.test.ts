import { expect, it, vi } from "vitest";

import {
  CameraController,
  type EncodedFrame,
  type PreviewVideoElement,
} from "../src/camera";
import {
  AdaptiveQualityController,
  AdaptiveQualityCoordinator,
  AdaptiveResolutionController,
  AdaptiveResolutionCoordinator,
  AdaptiveStreamController,
  AdaptiveStreamCoordinator,
  BandwidthEstimator,
  FrameStreamController,
  FrameStreamState,
  type ResolutionAdaptiveStream,
  type ResolutionSocketSource,
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
    {
      jpegQuality: 0.8,
      setQuality: vi.fn(),
      encode: vi.fn().mockResolvedValue(encoded),
    },
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
  let jpegQuality = 0.8;
  const encoder = {
    get jpegQuality() {
      return jpegQuality;
    },
    setQuality: vi.fn((quality: number) => {
      jpegQuality = quality;
    }),
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
  const qualityController = new AdaptiveQualityController(
    {
      initialQuality: 0.8,
      minimumQuality: 0.45,
      maximumQuality: 0.9,
      healthySamplesBeforeIncrease: 2,
      cooldownMs: 0,
    },
    () => now,
  );
  const adaptiveQuality = new AdaptiveQualityCoordinator(
    qualityController,
    stream,
    client,
  );
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

  scheduler.run(100);
  await Promise.resolve();
  await Promise.resolve();
  socket.bufferedAmount = 300000;
  scheduler.run(300);
  expect(stream.jpegQuality).toBeCloseTo(0.7);
  expect(stream.getState()).toBe(FrameStreamState.STREAMING);
  socket.bufferedAmount = 0;
  scheduler.run(500);
  await Promise.resolve();
  await Promise.resolve();
  scheduler.run(700);
  await Promise.resolve();
  await Promise.resolve();
  expect(stream.jpegQuality).toBeCloseTo(0.75);
  adaptiveQuality.setMode("fixed");
  socket.bufferedAmount = 300000;
  scheduler.run(900);
  expect(stream.jpegQuality).toBeCloseTo(0.75);

  now = 2000;
  for (let received = 3; received <= 10; received += 1) sendSample(received, 1);
  expect(stream.targetFps).toBe(7);

  adaptive.setMode("fixed");
  sendSample(11, 2);
  expect(stream.targetFps).toBe(7);
  adaptive.setMode("adaptive");
  expect(controller.getState().hasBaseline).toBe(false);
  expect(qualityController.getState().hasBaseline).toBe(false);
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
  adaptiveQuality.destroy();
});

it("feeds deterministic transport metrics through bandwidth estimation into one-step resolution control", () => {
  let now = 0;
  let width = 640;
  let height = 480;
  const listeners: Array<(event: never) => void> = [];
  const stream = {
    jpegQuality: 0.45,
    targetFps: 8,
    get outputWidth() {
      return width;
    },
    get outputHeight() {
      return height;
    },
    getState: () => FrameStreamState.STREAMING,
    getMetrics: () => ({
      targetFps: 8,
      jpegQuality: 0.45,
      outputWidth: width,
      outputHeight: height,
      startedAt: 0,
      stoppedAt: null,
      framesAttempted: 0,
      framesEncoded: 0,
      framesSent: 0,
      framesDroppedForBackpressure: 0,
      framesDroppedForTiming: 0,
      encodingFailures: 0,
      sendFailures: 0,
      lastFrameSize: 100,
      lastFrameWidth: width,
      lastFrameHeight: height,
      effectiveFps: 0,
    }),
    setOutputResolution: vi.fn((nextWidth: number, nextHeight: number) => {
      width = nextWidth;
      height = nextHeight;
    }),
    subscribe: (listener: (event: never) => void) => {
      listeners.push(listener);
      return () => undefined;
    },
  } as unknown as ResolutionAdaptiveStream;
  const socket = {
    getState: () => "OPEN",
    getBufferedAmount: () => 0,
    subscribe: () => () => undefined,
  } as unknown as ResolutionSocketSource;
  const coordinator = new AdaptiveResolutionCoordinator(
    new AdaptiveResolutionController(undefined, {
      overloadSamplesBeforeDecrease: 1,
      cooldownMs: 0,
    }),
    new BandwidthEstimator({ minimumWindowMs: 1 }),
    stream,
    socket,
    0.45,
    () => now,
  );
  const emit = (framesSent: number, drops: number) =>
    listeners.forEach((listener) =>
      listener({
        type: "metrics.changed",
        metrics: {
          ...stream.getMetrics(),
          framesSent,
          framesDroppedForBackpressure: drops,
        },
      } as never),
    );
  emit(0, 0);
  now = 1000;
  emit(1, 1);
  expect(stream.setOutputResolution).toHaveBeenCalledWith(480, 360);
  expect(coordinator.getSnapshot()).toMatchObject({
    currentProfile: { id: "medium" },
    estimate: { pressure: "overloaded" },
  });
  coordinator.setMode("fixed");
  now = 2000;
  emit(2, 2);
  expect(stream.setOutputResolution).toHaveBeenCalledTimes(1);
  coordinator.destroy();
});

it("moves high to low and restores low to high one profile at a time across fresh epochs", () => {
  let now = 0;
  const controller = new AdaptiveResolutionController(
    undefined,
    {
      overloadSamplesBeforeDecrease: 2,
      healthySamplesBeforeIncrease: 2,
      cooldownMs: 10,
    },
    () => now,
  );
  const overloaded = (profile: string) => ({
    currentProfile: profile,
    jpegQuality: 0.45,
    minimumJpegQuality: 0.45,
    targetFps: 8,
    streaming: true,
    socketOpen: true,
    estimate: {
      instantaneousBitrateBps: 1000000,
      smoothedBitrateBps: 1000000,
      estimatedBytesPerSecond: 125000,
      averageFrameBytes: 100,
      sampleCount: 12,
      elapsedWindowMs: 1000,
      confidence: "high" as const,
      pressure: "overloaded" as const,
      latestBufferedBytes: 0,
      latestPayloadBytes: 100,
      sendFailureDelta: 0,
      backpressureDropDelta: 0,
    },
  });
  const healthy = (profile: string) => ({
    ...overloaded(profile),
    estimate: { ...overloaded(profile).estimate, pressure: "healthy" as const },
  });
  expect(controller.evaluate(overloaded("high")).profile).toBe("high");
  expect(controller.evaluate(overloaded("high"))).toMatchObject({
    profile: "medium",
    direction: "decreased",
  });
  now = 11;
  controller.evaluate(overloaded("medium"));
  expect(controller.evaluate(overloaded("medium"))).toMatchObject({
    profile: "low",
    direction: "decreased",
  });
  now = 22;
  controller.evaluate(overloaded("low"));
  expect(controller.evaluate(overloaded("low"))).toMatchObject({
    profile: "low",
    direction: "unchanged",
  });
  now = 33;
  expect(controller.evaluate(healthy("low")).direction).toBe("unchanged");
  expect(controller.evaluate(healthy("low"))).toMatchObject({
    profile: "medium",
    direction: "increased",
  });
  now = 44;
  controller.evaluate(healthy("medium"));
  expect(controller.evaluate(healthy("medium"))).toMatchObject({
    profile: "high",
    direction: "increased",
  });
  now = 55;
  controller.evaluate(healthy("high"));
  expect(controller.evaluate(healthy("high"))).toMatchObject({
    profile: "high",
    direction: "unchanged",
  });
  expect(controller.getState().mode).toBe("adaptive");
});

it("resets adaptation on reconnect and requires a new baseline after manual stream restart", () => {
  const controller = new AdaptiveResolutionController();
  const estimate = {
    instantaneousBitrateBps: null,
    smoothedBitrateBps: null,
    estimatedBytesPerSecond: null,
    averageFrameBytes: null,
    sampleCount: 0,
    elapsedWindowMs: 0,
    confidence: "unavailable" as const,
    pressure: "unknown" as const,
    latestBufferedBytes: 0,
    latestPayloadBytes: 0,
    sendFailureDelta: 0,
    backpressureDropDelta: 0,
  };
  const sample = {
    currentProfile: "medium",
    jpegQuality: 0.45,
    minimumJpegQuality: 0.45,
    targetFps: 8,
    streaming: true,
    socketOpen: true,
    estimate,
  };
  controller.evaluate(sample);
  controller.reset();
  expect(controller.getState()).toMatchObject({
    hasBaseline: false,
    healthySamples: 0,
    overloadSamples: 0,
  });
  expect(controller.evaluate(sample)).toMatchObject({
    profile: "medium",
    direction: "reset",
    reason: "insufficient_data",
  });
});

it("drives capture-loop transport drops through quality then resolution composition", async () => {
  const camera = {
    getState: () => "READY",
    getPreview: () => ({ videoWidth: 640, videoHeight: 480, readyState: 4 }),
    subscribe: () => () => undefined,
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
  let quality = 0.45;
  let width = 640;
  let height = 480;
  let now = 0;
  const encoder = {
    get jpegQuality() {
      return quality;
    },
    get outputWidth() {
      return width;
    },
    get outputHeight() {
      return height;
    },
    setQuality: vi.fn((value: number) => {
      quality = value;
    }),
    setOutputDimensions: vi.fn((nextWidth: number, nextHeight: number) => {
      width = nextWidth;
      height = nextHeight;
    }),
    encode: vi.fn().mockImplementation(async () => ({
      blob: new Blob([new Uint8Array(1000000)]),
      width: 640,
      height: 480,
      size: 1000000,
      mimeType: "image/jpeg",
      capturedAt: 0,
    })),
  };
  const stream = new FrameStreamController(camera, encoder, client, {
    scheduler,
    now: () => now,
  });
  const qualityCoordinator = new AdaptiveQualityCoordinator(
    new AdaptiveQualityController({
      initialQuality: 0.45,
      minimumQuality: 0.45,
      overloadSamplesBeforeDecrease: 1,
      healthySamplesBeforeIncrease: 2,
      cooldownMs: 0,
    }),
    stream,
    client,
  );
  const resolution = new AdaptiveResolutionCoordinator(
    new AdaptiveResolutionController(undefined, {
      overloadSamplesBeforeDecrease: 2,
      healthySamplesBeforeIncrease: 2,
      requiredBandwidthHeadroom: 1.01,
      cooldownMs: 0,
    }),
    new BandwidthEstimator({ minimumWindowMs: 1 }),
    stream,
    client,
    0.45,
    () => now,
  );
  const tick = async (timestamp: number, bufferedAmount: number) => {
    now = timestamp;
    socket.bufferedAmount = bufferedAmount;
    scheduler.run(timestamp);
    await Promise.resolve();
    await Promise.resolve();
  };
  stream.start();
  await tick(0, 0);
  await tick(1000, 300000);
  expect(stream.outputWidth).toBe(640);
  await tick(2000, 300000);
  expect(stream.outputWidth).toBe(480);
  await tick(3000, 300000);
  await tick(4000, 300000);
  expect(stream.outputWidth).toBe(320);
  await tick(5000, 300000);
  await tick(6000, 300000);
  expect(stream.outputWidth).toBe(320);
  await tick(7000, 0);
  expect(stream.outputWidth).toBe(320);
  await tick(8000, 0);
  expect(stream.outputWidth).toBe(480);
  await tick(9000, 0);
  await tick(10000, 0);
  expect(stream.outputWidth).toBe(640);
  expect(stream.getState()).toBe(FrameStreamState.STREAMING);
  expect(quality).toBe(0.45);
  expect(encoder.setOutputDimensions.mock.calls).toEqual([
    [480, 360],
    [320, 240],
    [480, 360],
    [640, 480],
  ]);
  expect(stream.outputWidth).toBe(640);
  expect(scheduler.requests).toBeGreaterThan(1);
  expect(encoder.encode).toHaveBeenCalledTimes(5);
  qualityCoordinator.destroy();
  resolution.destroy();
  stream.stop();
  client.destroy();
});
