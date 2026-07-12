import {
  CameraState,
  type CameraEvent,
  type FrameEncoder,
  type PreviewVideoElement,
} from "../camera";
import {
  WebSocketClientState,
  type GestureWebSocketClientEvent,
} from "../websocket";
import {
  FrameStreamDecision,
  FrameStreamError,
  FrameStreamErrorCode,
  FrameStreamState,
  type FrameStreamEvent,
  type FrameStreamListener,
  type FrameStreamMetrics,
} from "./stream-state";

export interface FrameScheduler {
  request(callback: FrameRequestCallback): number;
  cancel(identifier: number): void;
}

export interface StreamCameraController {
  getState(): CameraState;
  getPreview(): PreviewVideoElement | null;
  subscribe(listener: (event: CameraEvent) => void): () => void;
}

export interface StreamWebSocketClient {
  getState(): WebSocketClientState;
  getBufferedAmount(): number;
  sendFrame(payload: Blob | ArrayBuffer | Uint8Array): void;
  subscribe(listener: (event: GestureWebSocketClientEvent) => void): () => void;
}

export interface FrameStreamConfig {
  readonly targetFps?: number;
  readonly bufferedAmountThreshold?: number;
  readonly scheduler?: FrameScheduler;
  readonly now?: () => number;
  readonly subscriberErrorHandler?: (error: unknown) => void;
}

const DEFAULT_TARGET_FPS = 8;
const DEFAULT_BUFFERED_AMOUNT_THRESHOLD = 256 * 1024;

const browserScheduler: FrameScheduler = {
  request: (callback) => requestAnimationFrame(callback),
  cancel: (identifier) => cancelAnimationFrame(identifier),
};

export class FrameStreamController {
  readonly bufferedAmountThreshold: number;
  private currentTargetFps: number;
  private readonly scheduler: FrameScheduler;
  private readonly now: () => number;
  private readonly subscriberErrorHandler: (error: unknown) => void;
  private readonly listeners = new Set<FrameStreamListener>();
  private readonly cameraUnsubscribe: () => void;
  private readonly socketUnsubscribe: () => void;
  private state = FrameStreamState.IDLE;
  private scheduledFrame: number | null = null;
  private processing = false;
  private lastAttemptAt: number | null = null;
  private metrics = this.emptyMetrics();

  constructor(
    readonly camera: StreamCameraController,
    readonly encoder: FrameEncoder,
    readonly client: StreamWebSocketClient,
    config: FrameStreamConfig = {},
  ) {
    this.currentTargetFps = this.positiveNumber(
      config.targetFps ?? DEFAULT_TARGET_FPS,
      "targetFps",
    );
    this.bufferedAmountThreshold = this.nonNegativeNumber(
      config.bufferedAmountThreshold ?? DEFAULT_BUFFERED_AMOUNT_THRESHOLD,
      "bufferedAmountThreshold",
    );
    this.scheduler = config.scheduler ?? browserScheduler;
    this.now = config.now ?? (() => performance.now());
    this.subscriberErrorHandler =
      config.subscriberErrorHandler ??
      ((error) => console.error("Stream listener failed", error));
    this.cameraUnsubscribe = this.camera.subscribe((event) => {
      if (
        event.type === "state.changed" &&
        event.state !== CameraState.READY &&
        this.isActive()
      )
        this.stopWith(
          FrameStreamDecision.CAMERA_NOT_READY,
          FrameStreamErrorCode.CAMERA_NOT_READY,
        );
    });
    this.socketUnsubscribe = this.client.subscribe((event) => {
      if (
        event.type === "state.changed" &&
        event.state !== WebSocketClientState.OPEN &&
        this.isActive()
      )
        this.stopWith(
          FrameStreamDecision.SOCKET_NOT_OPEN,
          FrameStreamErrorCode.SOCKET_NOT_OPEN,
        );
    });
  }

  start(): void {
    if (this.isActive())
      throw this.error(
        FrameStreamErrorCode.INVALID_STATE,
        "Streaming is already active.",
      );
    if (
      this.camera.getState() !== CameraState.READY ||
      !this.camera.getPreview()
    )
      throw this.error(
        FrameStreamErrorCode.CAMERA_NOT_READY,
        "Camera preview is not ready.",
      );
    if (this.client.getState() !== WebSocketClientState.OPEN)
      throw this.error(
        FrameStreamErrorCode.SOCKET_NOT_OPEN,
        "WebSocket is not open.",
      );
    this.metrics = { ...this.emptyMetrics(), startedAt: this.now() };
    this.lastAttemptAt = null;
    this.setState(FrameStreamState.STARTING);
    this.setState(FrameStreamState.STREAMING);
    this.schedule();
  }

  stop(): void {
    if (!this.isActive() && this.state !== FrameStreamState.ERROR) return;
    this.setState(FrameStreamState.STOPPING);
    if (this.scheduledFrame !== null)
      this.scheduler.cancel(this.scheduledFrame);
    this.scheduledFrame = null;
    this.setState(FrameStreamState.STOPPED);
    this.metrics = {
      ...this.metrics,
      stoppedAt: this.now(),
      effectiveFps: this.effectiveFps(),
    };
    this.emitMetrics();
  }

  destroy(): void {
    this.stop();
    this.cameraUnsubscribe();
    this.socketUnsubscribe();
  }
  subscribe(listener: FrameStreamListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  getState(): FrameStreamState {
    return this.state;
  }
  get targetFps(): number {
    return this.currentTargetFps;
  }
  setTargetFps(targetFps: number): void {
    const validated = this.positiveNumber(targetFps, "targetFps");
    if (validated === this.currentTargetFps) return;
    this.currentTargetFps = validated;
    this.metrics = { ...this.metrics, targetFps: validated };
    this.emitMetrics();
  }
  getMetrics(): FrameStreamMetrics {
    return Object.freeze({
      ...this.metrics,
      effectiveFps: this.effectiveFps(),
    });
  }
  resetMetrics(): void {
    if (this.isActive())
      throw this.error(
        FrameStreamErrorCode.INVALID_STATE,
        "Stop streaming before resetting metrics.",
      );
    this.metrics = this.emptyMetrics();
    this.emitMetrics();
  }

  private schedule(): void {
    if (
      this.state !== FrameStreamState.STREAMING ||
      this.scheduledFrame !== null
    )
      return;
    this.scheduledFrame = this.scheduler.request((timestamp) => {
      this.scheduledFrame = null;
      void this.cycle(timestamp);
    });
  }

  private async cycle(timestamp: number): Promise<void> {
    if (this.state !== FrameStreamState.STREAMING || this.processing) return;
    const interval = 1000 / this.targetFps;
    if (
      this.lastAttemptAt !== null &&
      timestamp - this.lastAttemptAt < interval
    ) {
      this.metrics = {
        ...this.metrics,
        framesDroppedForTiming: this.metrics.framesDroppedForTiming + 1,
      };
      this.emitDecision(FrameStreamDecision.TIMING_SKIPPED);
      this.schedule();
      return;
    }
    if (
      this.camera.getState() !== CameraState.READY ||
      !this.camera.getPreview()
    )
      return this.stopWith(
        FrameStreamDecision.CAMERA_NOT_READY,
        FrameStreamErrorCode.CAMERA_NOT_READY,
      );
    if (this.client.getState() !== WebSocketClientState.OPEN)
      return this.stopWith(
        FrameStreamDecision.SOCKET_NOT_OPEN,
        FrameStreamErrorCode.SOCKET_NOT_OPEN,
      );
    if (this.client.getBufferedAmount() > this.bufferedAmountThreshold) {
      this.metrics = {
        ...this.metrics,
        framesDroppedForBackpressure:
          this.metrics.framesDroppedForBackpressure + 1,
      };
      this.emitDecision(FrameStreamDecision.BACKPRESSURE_SKIPPED);
      this.schedule();
      return;
    }
    this.lastAttemptAt = timestamp;
    this.processing = true;
    this.metrics = {
      ...this.metrics,
      framesAttempted: this.metrics.framesAttempted + 1,
    };
    try {
      const frame = await this.encoder.encode(
        this.camera.getPreview() as PreviewVideoElement,
      );
      if (this.state !== FrameStreamState.STREAMING) return;
      this.metrics = {
        ...this.metrics,
        framesEncoded: this.metrics.framesEncoded + 1,
        lastFrameSize: frame.size,
        lastFrameWidth: frame.width,
        lastFrameHeight: frame.height,
      };
      try {
        this.client.sendFrame(frame.blob);
      } catch (cause) {
        this.metrics = {
          ...this.metrics,
          sendFailures: this.metrics.sendFailures + 1,
        };
        return this.stopWith(
          FrameStreamDecision.SOCKET_NOT_OPEN,
          FrameStreamErrorCode.SEND_FAILED,
          cause,
        );
      }
      this.metrics = {
        ...this.metrics,
        framesSent: this.metrics.framesSent + 1,
      };
      this.emitDecision(FrameStreamDecision.SENT);
    } catch (cause) {
      this.metrics = {
        ...this.metrics,
        encodingFailures: this.metrics.encodingFailures + 1,
      };
      this.stopWith(
        FrameStreamDecision.CAMERA_NOT_READY,
        FrameStreamErrorCode.ENCODING_FAILED,
        cause,
      );
    } finally {
      this.processing = false;
      this.emitMetrics();
      this.schedule();
    }
  }

  private stopWith(
    decision: FrameStreamDecision,
    code: FrameStreamErrorCode,
    cause?: unknown,
  ): void {
    this.emitDecision(decision);
    const error = this.error(
      code,
      code === FrameStreamErrorCode.ENCODING_FAILED
        ? "Frame encoding failed."
        : "Streaming dependency is unavailable.",
      cause,
    );
    this.setState(FrameStreamState.ERROR);
    this.emit(Object.freeze({ type: "error", error }));
    this.stop();
  }
  private emptyMetrics(): FrameStreamMetrics {
    return {
      targetFps: this.currentTargetFps,
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
    };
  }
  private effectiveFps(): number {
    if (!this.metrics.startedAt) return 0;
    const end = this.metrics.stoppedAt ?? this.now();
    return end > this.metrics.startedAt
      ? this.metrics.framesSent / ((end - this.metrics.startedAt) / 1000)
      : 0;
  }
  private isActive(): boolean {
    return (
      this.state === FrameStreamState.STARTING ||
      this.state === FrameStreamState.STREAMING ||
      this.state === FrameStreamState.STOPPING
    );
  }
  private positiveNumber(value: number, name: string): number {
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0)
      throw this.error(
        FrameStreamErrorCode.INVALID_CONFIGURATION,
        `${name} must be positive.`,
      );
    return value;
  }
  private nonNegativeNumber(value: number, name: string): number {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0)
      throw this.error(
        FrameStreamErrorCode.INVALID_CONFIGURATION,
        `${name} must be non-negative.`,
      );
    return value;
  }
  private error(
    code: FrameStreamErrorCode,
    message: string,
    cause?: unknown,
  ): FrameStreamError {
    return new FrameStreamError(
      code,
      message,
      cause === undefined ? undefined : { cause },
    );
  }
  private setState(state: FrameStreamState): void {
    this.state = state;
    this.emit(Object.freeze({ type: "state.changed", state }));
  }
  private emitDecision(decision: FrameStreamDecision): void {
    this.emit(Object.freeze({ type: "decision", decision }));
  }
  private emitMetrics(): void {
    this.emit(
      Object.freeze({ type: "metrics.changed", metrics: this.getMetrics() }),
    );
  }
  private emit(event: FrameStreamEvent): void {
    for (const listener of [...this.listeners]) {
      try {
        listener(event);
      } catch (error) {
        this.subscriberErrorHandler(error);
      }
    }
  }
}
