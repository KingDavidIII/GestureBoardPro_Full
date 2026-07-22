import { describe, expect, it, vi } from "vitest";

import {
  CameraState,
  type CameraEvent,
  type EncodedFrame,
  type FrameEncoder,
  type PreviewVideoElement,
} from "../src/camera";
import { DiagnosticDashboard } from "../src/dashboard/diagnostic-dashboard";
import { ResourceCleanupError } from "../src/lifecycle/resource-cleanup";
import {
  AdaptiveQualityController,
  AdaptiveQualityCoordinator,
  type AdaptiveQualitySnapshot,
  type QualityAdaptiveStream,
  type QualitySocketSource,
} from "../src/streaming/adaptive-quality-controller";
import {
  AdaptiveResolutionController,
  AdaptiveResolutionCoordinator,
  type ResolutionAdaptiveStream,
  type ResolutionSocketSource,
} from "../src/streaming/adaptive-resolution-controller";
import {
  AdaptiveStreamController,
  AdaptiveStreamCoordinator,
  type AdaptiveFrameStream,
  type AdaptiveStreamSnapshot,
  type AdaptiveWebSocketSource,
} from "../src/streaming/adaptive-stream-controller";
import { BandwidthEstimator } from "../src/streaming/bandwidth-estimator";
import {
  FrameStreamController,
  type FrameScheduler,
  type StreamCameraController,
  type StreamWebSocketClient,
} from "../src/streaming/frame-stream-controller";
import {
  FrameStreamState,
  type FrameStreamEvent,
  type FrameStreamMetrics,
} from "../src/streaming/stream-state";
import {
  GestureWebSocketClient,
  WebSocketClientState,
  type GestureWebSocketClientEvent,
} from "../src/websocket";
import { FakeWebSocket } from "./fake-websocket";

class SubscriptionSource<T> {
  listener: ((event: T) => void) | null = null;
  readonly unsubscribe = vi.fn();

  readonly subscribe = (listener: (event: T) => void): (() => void) => {
    this.listener = listener;
    return this.unsubscribe;
  };

  emit(event: T): void {
    this.listener?.(event);
  }
}

const metrics = (): FrameStreamMetrics => ({
  targetFps: 8,
  jpegQuality: 0.8,
  outputWidth: 640,
  outputHeight: 480,
  startedAt: null,
  stoppedAt: null,
  framesAttempted: 0,
  framesEncoded: 0,
  framesSent: 0,
  framesDroppedForBackpressure: 0,
  framesDroppedForTiming: 0,
  encodingFailures: 0,
  sendFailures: 0,
  lastFrameSize: null,
  lastFrameWidth: null,
  lastFrameHeight: null,
  effectiveFps: 0,
});

const captureCleanupError = (release: () => void): ResourceCleanupError => {
  try {
    release();
  } catch (error) {
    expect(error).toBeInstanceOf(ResourceCleanupError);
    return error as ResourceCleanupError;
  }

  throw new Error("Expected resource cleanup to fail.");
};

describe("resource lifecycle hardening", () => {
  it("destroys a frame stream once and continues after an unsubscribe fails", () => {
    const cameraEvents = new SubscriptionSource<CameraEvent>();
    const socketEvents = new SubscriptionSource<GestureWebSocketClientEvent>();
    const scheduler: FrameScheduler = {
      request: vi.fn(() => 1),
      cancel: vi.fn(),
    };
    const preview: PreviewVideoElement = {
      srcObject: null,
      videoWidth: 640,
      videoHeight: 480,
      readyState: 4,
      play: async () => undefined,
    };
    const camera: StreamCameraController = {
      getState: () => CameraState.READY,
      getPreview: () => preview,
      subscribe: cameraEvents.subscribe,
    };
    const client: StreamWebSocketClient = {
      getState: () => WebSocketClientState.OPEN,
      getBufferedAmount: () => 0,
      sendFrame: vi.fn(),
      subscribe: socketEvents.subscribe,
    };
    const frame: EncodedFrame = Object.freeze({
      blob: new Blob(["frame"], { type: "image/jpeg" }),
      width: 1,
      height: 1,
      size: 5,
      mimeType: "image/jpeg",
      capturedAt: 0,
    });
    const encoder: FrameEncoder = {
      jpegQuality: 0.8,
      setQuality: vi.fn(),
      encode: vi.fn().mockResolvedValue(frame),
    };
    const stream = new FrameStreamController(camera, encoder, client, {
      scheduler,
      now: () => 100,
    });
    const failure = new Error("camera unsubscribe failed");
    cameraEvents.unsubscribe.mockImplementationOnce(() => {
      throw failure;
    });
    stream.start();

    const error = captureCleanupError(() => stream.destroy());

    expect(error.failures).toEqual([
      { operation: "camera.unsubscribe", error: failure },
    ]);
    expect(scheduler.cancel).toHaveBeenCalledOnce();
    expect(cameraEvents.unsubscribe).toHaveBeenCalledOnce();
    expect(socketEvents.unsubscribe).toHaveBeenCalledOnce();
    expect(() => stream.destroy()).not.toThrow();
    expect(cameraEvents.unsubscribe).toHaveBeenCalledOnce();
    expect(socketEvents.unsubscribe).toHaveBeenCalledOnce();
    expect(() => stream.start()).toThrow(
      "Streaming controller has been destroyed.",
    );
  });

  const coordinatorCases: ReadonlyArray<{
    readonly name: string;
    readonly create: () => {
      readonly destroy: () => void;
      readonly reset: () => void;
      readonly subscribe: (listener: () => void) => () => void;
      readonly subscriberErrorHandler: ReturnType<typeof vi.fn>;
      readonly failReset: (error: Error) => void;
      readonly firstRelease: ReturnType<typeof vi.fn>;
      readonly secondRelease: ReturnType<typeof vi.fn>;
      readonly firstReleaseOperation: string;
    };
  }> = [
    {
      name: "adaptive stream coordinator",
      create: () => {
        const streamEvents = new SubscriptionSource<FrameStreamEvent>();
        const socketEvents =
          new SubscriptionSource<GestureWebSocketClientEvent>();
        const stream: AdaptiveFrameStream = {
          targetFps: 8,
          getState: () => FrameStreamState.STOPPED,
          setTargetFps: vi.fn(),
          subscribe: streamEvents.subscribe,
        };
        const socket: AdaptiveWebSocketSource = {
          getState: () => WebSocketClientState.CLOSED,
          subscribe: socketEvents.subscribe,
        };
        const controller = new AdaptiveStreamController({ maximumFps: 8 });
        const subscriberErrorHandler = vi.fn();
        const coordinator = new AdaptiveStreamCoordinator(
          controller,
          stream,
          socket,
          { subscriberErrorHandler },
        );
        return {
          destroy: () => coordinator.destroy(),
          reset: () => coordinator.reset(),
          subscribe: (listener) => coordinator.subscribe(listener),
          subscriberErrorHandler,
          failReset: (error) => {
            vi.spyOn(controller, "reset").mockImplementationOnce(() => {
              throw error;
            });
          },
          firstRelease: socketEvents.unsubscribe,
          secondRelease: streamEvents.unsubscribe,
          firstReleaseOperation: "socket.unsubscribe",
        };
      },
    },
    {
      name: "adaptive quality coordinator",
      create: () => {
        const streamEvents = new SubscriptionSource<FrameStreamEvent>();
        const socketEvents =
          new SubscriptionSource<GestureWebSocketClientEvent>();
        const stream: QualityAdaptiveStream = {
          jpegQuality: 0.8,
          getState: () => FrameStreamState.STOPPED,
          getMetrics: metrics,
          setJpegQuality: vi.fn(),
          subscribe: streamEvents.subscribe,
        };
        const socket: QualitySocketSource = {
          getState: () => WebSocketClientState.CLOSED,
          getBufferedAmount: () => 0,
          subscribe: socketEvents.subscribe,
        };
        const controller = new AdaptiveQualityController({
          initialQuality: 0.8,
        });
        const subscriberErrorHandler = vi.fn();
        const coordinator = new AdaptiveQualityCoordinator(
          controller,
          stream,
          socket,
          { subscriberErrorHandler },
        );
        return {
          destroy: () => coordinator.destroy(),
          reset: () => coordinator.reset(),
          subscribe: (listener) => coordinator.subscribe(listener),
          subscriberErrorHandler,
          failReset: (error) => {
            vi.spyOn(controller, "reset").mockImplementationOnce(() => {
              throw error;
            });
          },
          firstRelease: streamEvents.unsubscribe,
          secondRelease: socketEvents.unsubscribe,
          firstReleaseOperation: "stream.unsubscribe",
        };
      },
    },
    {
      name: "adaptive resolution coordinator",
      create: () => {
        const streamEvents = new SubscriptionSource<FrameStreamEvent>();
        const socketEvents =
          new SubscriptionSource<GestureWebSocketClientEvent>();
        const stream: ResolutionAdaptiveStream = {
          jpegQuality: 0.8,
          targetFps: 8,
          outputWidth: 640,
          outputHeight: 480,
          getState: () => FrameStreamState.STOPPED,
          getMetrics: metrics,
          setOutputResolution: vi.fn(),
          subscribe: streamEvents.subscribe,
        };
        const socket: ResolutionSocketSource = {
          getState: () => WebSocketClientState.CLOSED,
          getBufferedAmount: () => 0,
          subscribe: socketEvents.subscribe,
        };
        const controller = new AdaptiveResolutionController();
        const subscriberErrorHandler = vi.fn();
        const coordinator = new AdaptiveResolutionCoordinator(
          controller,
          new BandwidthEstimator(),
          stream,
          socket,
          0.45,
          () => 0,
          { subscriberErrorHandler },
        );
        return {
          destroy: () => coordinator.destroy(),
          reset: () => coordinator.reset(),
          subscribe: (listener) => coordinator.subscribe(listener),
          subscriberErrorHandler,
          failReset: (error) => {
            vi.spyOn(controller, "reset").mockImplementationOnce(() => {
              throw error;
            });
          },
          firstRelease: streamEvents.unsubscribe,
          secondRelease: socketEvents.unsubscribe,
          firstReleaseOperation: "stream.unsubscribe",
        };
      },
    },
  ];

  it.each(coordinatorCases)(
    "isolates subscriber failures for $name",
    ({ create }) => {
      const lifecycle = create();
      const firstFailure = new Error("first adaptive listener failed");
      const secondFailure = new Error("second adaptive listener failed");
      const healthyListener = vi.fn();
      const unsubscribeFirst = lifecycle.subscribe(() => {
        throw firstFailure;
      });
      const unsubscribeHealthy = lifecycle.subscribe(healthyListener);
      const unsubscribeSecond = lifecycle.subscribe(() => {
        throw secondFailure;
      });

      expect(() => lifecycle.reset()).not.toThrow();
      expect(healthyListener).toHaveBeenCalledOnce();
      expect(lifecycle.subscriberErrorHandler).toHaveBeenCalledTimes(2);
      expect(lifecycle.subscriberErrorHandler).toHaveBeenNthCalledWith(
        1,
        firstFailure,
      );
      expect(lifecycle.subscriberErrorHandler).toHaveBeenNthCalledWith(
        2,
        secondFailure,
      );

      unsubscribeFirst();
      unsubscribeHealthy();
      unsubscribeSecond();
      expect(() => lifecycle.destroy()).not.toThrow();
    },
  );

  it.each(coordinatorCases)(
    "finishes $name teardown after reset and unsubscribe failures",
    ({ create }) => {
      const lifecycle = create();
      const resetFailure = new Error("reset failed");
      const unsubscribeFailure = new Error("unsubscribe failed");
      lifecycle.failReset(resetFailure);
      lifecycle.firstRelease.mockImplementationOnce(() => {
        throw unsubscribeFailure;
      });

      const error = captureCleanupError(lifecycle.destroy);

      expect(error.failures).toEqual([
        { operation: "controller.reset", error: resetFailure },
        {
          operation: lifecycle.firstReleaseOperation,
          error: unsubscribeFailure,
        },
      ]);
      expect(lifecycle.firstRelease).toHaveBeenCalledOnce();
      expect(lifecycle.secondRelease).toHaveBeenCalledOnce();
      expect(() => lifecycle.destroy()).not.toThrow();
      expect(lifecycle.firstRelease).toHaveBeenCalledOnce();
      expect(lifecycle.secondRelease).toHaveBeenCalledOnce();
    },
  );

  it("finishes dashboard teardown after a subscription release fails", () => {
    const root = document.createElement("div");
    const client = new GestureWebSocketClient("ws://board.test/ws/", {
      socketFactory: () => new FakeWebSocket(),
    });
    const adaptiveUnsubscribe = vi.fn(() => {
      throw new Error("adaptive unsubscribe failed");
    });
    const qualityUnsubscribe = vi.fn();
    const adaptiveReset = vi.fn();
    const qualityReset = vi.fn();
    const adaptiveSnapshot: AdaptiveStreamSnapshot = Object.freeze({
      mode: "adaptive",
      targetFps: 8,
      minimumFps: 5,
      maximumFps: 8,
      latestDecision: null,
      healthySamples: 0,
      overloadSamples: 0,
      estimatedCapacityFps: null,
      cooldownActive: false,
    });
    const qualitySnapshot: AdaptiveQualitySnapshot = Object.freeze({
      mode: "adaptive",
      quality: 0.8,
      minimumQuality: 0.45,
      maximumQuality: 0.9,
      latestDecision: null,
      healthySamples: 0,
      overloadSamples: 0,
      latestPayloadBytes: 0,
      latestBufferedBytes: 0,
      cooldownActive: false,
    });
    const dashboard = new DiagnosticDashboard(root, client, {
      adaptive: {
        getSnapshot: () => adaptiveSnapshot,
        setMode: vi.fn(),
        reset: adaptiveReset,
        subscribe: () => adaptiveUnsubscribe,
      },
      adaptiveQuality: {
        getSnapshot: () => qualitySnapshot,
        setMode: vi.fn(),
        reset: qualityReset,
        subscribe: () => qualityUnsubscribe,
      },
    });

    const error = captureCleanupError(() => dashboard.destroy());

    expect(error.failures.map(({ operation }) => operation)).toEqual([
      "adaptive-stream.unsubscribe",
    ]);
    expect(qualityUnsubscribe).toHaveBeenCalledOnce();
    expect(adaptiveReset).toHaveBeenCalledOnce();
    expect(qualityReset).toHaveBeenCalledOnce();
    expect(root.childElementCount).toBe(0);
    expect(() => dashboard.destroy()).not.toThrow();
    client.destroy();
  });
});
