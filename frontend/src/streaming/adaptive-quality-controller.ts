import type {
  GestureWebSocketClientEvent,
  WebSocketClientState,
} from "../websocket";
import { releaseResourceOperations } from "../lifecycle/resource-cleanup";
import {
  FrameStreamState,
  type FrameStreamEvent,
  type FrameStreamMetrics,
} from "./stream-state";

export type AdaptiveQualityMode = "adaptive" | "fixed";
export type AdaptiveQualityDirection =
  | "increased"
  | "decreased"
  | "unchanged"
  | "reset";
export type AdaptiveQualityReason =
  | "buffered_amount"
  | "payload_size"
  | "backpressure_drop"
  | "send_failure"
  | "healthy_window"
  | "cooldown"
  | "insufficient_data"
  | "disabled"
  | "disconnected"
  | "stream_stopped"
  | "manual_reset";

export interface AdaptiveQualityPolicy {
  readonly enabled?: boolean;
  readonly minimumQuality?: number;
  readonly maximumQuality?: number;
  readonly initialQuality: number;
  readonly decreaseStep?: number;
  readonly increaseStep?: number;
  readonly healthySamplesBeforeIncrease?: number;
  readonly overloadSamplesBeforeDecrease?: number;
  readonly cooldownMs?: number;
  readonly bufferedBytesThreshold?: number;
  readonly payloadBytesThreshold?: number;
}

export interface TransportQualitySample {
  readonly sentFrames: number;
  readonly backpressureDrops: number;
  readonly sendFailures: number;
  readonly encodingFailures: number;
  readonly latestEncodedBytes: number;
  readonly bufferedAmountBytes: number;
}

export interface AdaptiveQualityDecision {
  readonly previousQuality: number;
  readonly quality: number;
  readonly direction: AdaptiveQualityDirection;
  readonly reason: AdaptiveQualityReason;
  readonly healthySamples: number;
  readonly overloadSamples: number;
  readonly latestPayloadBytes: number;
  readonly latestBufferedBytes: number;
  readonly cooldownActive: boolean;
  readonly adjustedAt: number | null;
}

export interface AdaptiveQualityState {
  readonly mode: AdaptiveQualityMode;
  readonly healthySamples: number;
  readonly overloadSamples: number;
  readonly hasBaseline: boolean;
  readonly lastAdjustmentAt: number | null;
}

const DEFAULTS = Object.freeze({
  enabled: true,
  minimumQuality: 0.45,
  maximumQuality: 0.9,
  decreaseStep: 0.1,
  increaseStep: 0.05,
  healthySamplesBeforeIncrease: 10,
  overloadSamplesBeforeDecrease: 1,
  cooldownMs: 2000,
  bufferedBytesThreshold: 262144,
  payloadBytesThreshold: 131072,
});

export class AdaptiveQualityController {
  readonly policy: Required<AdaptiveQualityPolicy>;
  private mode: AdaptiveQualityMode;
  private previous: TransportQualitySample | null = null;
  private healthy = 0;
  private overload = 0;
  private lastAdjustment: number | null = null;

  constructor(
    policy: AdaptiveQualityPolicy,
    private readonly now: () => number = () => performance.now(),
  ) {
    const normalized = Object.freeze({ ...DEFAULTS, ...policy });
    const quality = (value: number) =>
      Number.isFinite(value) && value > 0 && value <= 1;
    if (
      !quality(normalized.minimumQuality) ||
      !quality(normalized.maximumQuality) ||
      normalized.minimumQuality > normalized.maximumQuality ||
      !quality(normalized.initialQuality) ||
      normalized.initialQuality < normalized.minimumQuality ||
      normalized.initialQuality > normalized.maximumQuality ||
      !quality(normalized.decreaseStep) ||
      !quality(normalized.increaseStep) ||
      !Number.isSafeInteger(normalized.healthySamplesBeforeIncrease) ||
      normalized.healthySamplesBeforeIncrease < 1 ||
      !Number.isSafeInteger(normalized.overloadSamplesBeforeDecrease) ||
      normalized.overloadSamplesBeforeDecrease < 1 ||
      !Number.isFinite(normalized.cooldownMs) ||
      normalized.cooldownMs < 0 ||
      !Number.isSafeInteger(normalized.bufferedBytesThreshold) ||
      normalized.bufferedBytesThreshold < 0 ||
      !Number.isSafeInteger(normalized.payloadBytesThreshold) ||
      normalized.payloadBytesThreshold < 0
    )
      throw new RangeError("Invalid adaptive quality policy.");
    this.policy = normalized;
    this.mode = normalized.enabled ? "adaptive" : "fixed";
  }

  setMode(mode: AdaptiveQualityMode): void {
    if (mode !== "adaptive" && mode !== "fixed")
      throw new RangeError("Invalid quality mode.");
    if (this.mode === mode) return;
    this.mode = mode;
    this.reset();
  }
  reset(): void {
    this.previous = null;
    this.healthy = 0;
    this.overload = 0;
    this.lastAdjustment = null;
  }
  getState(): AdaptiveQualityState {
    return Object.freeze({
      mode: this.mode,
      healthySamples: this.healthy,
      overloadSamples: this.overload,
      hasBaseline: this.previous !== null,
      lastAdjustmentAt: this.lastAdjustment,
    });
  }
  isCooldownActive(): boolean {
    return (
      this.lastAdjustment !== null &&
      this.now() - this.lastAdjustment < this.policy.cooldownMs
    );
  }

  evaluate(
    currentQuality: number,
    sample?: TransportQualitySample,
  ): AdaptiveQualityDecision {
    if (
      !Number.isFinite(currentQuality) ||
      currentQuality <= 0 ||
      currentQuality > 1
    )
      throw new RangeError("Current quality must be finite and in (0, 1].");
    const make = (
      quality: number,
      direction: AdaptiveQualityDirection,
      reason: AdaptiveQualityReason,
      source?: TransportQualitySample,
    ): AdaptiveQualityDecision =>
      Object.freeze({
        previousQuality: currentQuality,
        quality,
        direction,
        reason,
        healthySamples: this.healthy,
        overloadSamples: this.overload,
        latestPayloadBytes: source?.latestEncodedBytes ?? 0,
        latestBufferedBytes: source?.bufferedAmountBytes ?? 0,
        cooldownActive: this.isCooldownActive(),
        adjustedAt: this.lastAdjustment,
      });
    if (this.mode === "fixed")
      return make(currentQuality, "unchanged", "disabled", sample);
    if (!sample) {
      this.healthy = 0;
      return make(currentQuality, "unchanged", "insufficient_data");
    }
    this.validateSample(sample);
    if (!this.previous || this.regressed(sample)) {
      this.previous = sample;
      this.healthy = 0;
      this.overload = 0;
      return make(currentQuality, "reset", "insufficient_data", sample);
    }
    const sendFailure = sample.sendFailures > this.previous.sendFailures;
    const backpressure =
      sample.backpressureDrops > this.previous.backpressureDrops;
    const encodingFailure =
      sample.encodingFailures > this.previous.encodingFailures;
    const reason: AdaptiveQualityReason | null = sendFailure
      ? "send_failure"
      : backpressure
        ? "backpressure_drop"
        : sample.bufferedAmountBytes >= this.policy.bufferedBytesThreshold
          ? "buffered_amount"
          : sample.latestEncodedBytes >= this.policy.payloadBytesThreshold
            ? "payload_size"
            : null;
    this.previous = sample;
    if (reason) {
      this.overload += 1;
      this.healthy = 0;
      if (this.overload < this.policy.overloadSamplesBeforeDecrease)
        return make(currentQuality, "unchanged", reason, sample);
      if (this.isCooldownActive())
        return make(currentQuality, "unchanged", "cooldown", sample);
      const next = Math.max(
        this.policy.minimumQuality,
        currentQuality - this.policy.decreaseStep,
      );
      this.lastAdjustment = this.now();
      this.overload = 0;
      return make(
        next,
        next === currentQuality ? "unchanged" : "decreased",
        reason,
        sample,
      );
    }
    this.overload = 0;
    if (encodingFailure) {
      this.healthy = 0;
      return make(currentQuality, "unchanged", "insufficient_data", sample);
    }
    this.healthy += 1;
    if (this.healthy < this.policy.healthySamplesBeforeIncrease)
      return make(currentQuality, "unchanged", "healthy_window", sample);
    if (this.isCooldownActive())
      return make(currentQuality, "unchanged", "cooldown", sample);
    const next = Math.min(
      this.policy.maximumQuality,
      currentQuality + this.policy.increaseStep,
    );
    this.healthy = 0;
    this.lastAdjustment = this.now();
    return make(
      next,
      next === currentQuality ? "unchanged" : "increased",
      "healthy_window",
      sample,
    );
  }

  private validateSample(sample: TransportQualitySample): void {
    for (const value of Object.values(sample))
      if (!Number.isSafeInteger(value) || value < 0)
        throw new RangeError(
          "Transport sample values must be non-negative safe integers.",
        );
  }
  private regressed(sample: TransportQualitySample): boolean {
    return (
      this.previous !== null &&
      (sample.sentFrames < this.previous.sentFrames ||
        sample.backpressureDrops < this.previous.backpressureDrops ||
        sample.sendFailures < this.previous.sendFailures ||
        sample.encodingFailures < this.previous.encodingFailures)
    );
  }
}

export interface AdaptiveQualitySnapshot {
  readonly mode: AdaptiveQualityMode;
  readonly quality: number;
  readonly minimumQuality: number;
  readonly maximumQuality: number;
  readonly latestDecision: AdaptiveQualityDecision | null;
  readonly healthySamples: number;
  readonly overloadSamples: number;
  readonly latestPayloadBytes: number;
  readonly latestBufferedBytes: number;
  readonly cooldownActive: boolean;
}
export interface QualityAdaptiveStream {
  readonly jpegQuality: number;
  getState(): FrameStreamState;
  getMetrics(): FrameStreamMetrics;
  setJpegQuality(quality: number): void;
  subscribe(listener: (event: FrameStreamEvent) => void): () => void;
}
export interface QualitySocketSource {
  getState(): WebSocketClientState;
  getBufferedAmount(): number;
  subscribe(listener: (event: GestureWebSocketClientEvent) => void): () => void;
}
export type AdaptiveQualityListener = (
  snapshot: AdaptiveQualitySnapshot,
) => void;

export interface AdaptiveQualityCoordinatorOptions {
  readonly subscriberErrorHandler?: (error: unknown) => void;
}

export class AdaptiveQualityCoordinator {
  private readonly listeners = new Set<AdaptiveQualityListener>();
  private readonly subscriberErrorHandler: (error: unknown) => void;
  private latest: AdaptiveQualityDecision | null = null;
  private destroyed = false;
  private applying = false;
  private readonly unsubscribeStream: () => void;
  private readonly unsubscribeSocket: () => void;
  constructor(
    readonly controller: AdaptiveQualityController,
    private readonly stream: QualityAdaptiveStream,
    private readonly socket: QualitySocketSource,
    options: AdaptiveQualityCoordinatorOptions = {},
  ) {
    this.subscriberErrorHandler =
      options.subscriberErrorHandler ??
      ((error) => console.error("Adaptive quality listener failed", error));
    this.unsubscribeStream = stream.subscribe((event) =>
      this.handleStream(event),
    );
    this.unsubscribeSocket = socket.subscribe((event) =>
      this.handleSocket(event),
    );
  }
  getSnapshot(): AdaptiveQualitySnapshot {
    const state = this.controller.getState();
    return Object.freeze({
      mode: state.mode,
      quality: this.stream.jpegQuality,
      minimumQuality: this.controller.policy.minimumQuality,
      maximumQuality: this.controller.policy.maximumQuality,
      latestDecision: this.latest,
      healthySamples: state.healthySamples,
      overloadSamples: state.overloadSamples,
      latestPayloadBytes: this.latest?.latestPayloadBytes ?? 0,
      latestBufferedBytes: this.latest?.latestBufferedBytes ?? 0,
      cooldownActive: this.controller.isCooldownActive(),
    });
  }
  setMode(mode: AdaptiveQualityMode): void {
    if (this.destroyed || mode === this.controller.getState().mode) return;
    this.controller.setMode(mode);
    this.latest = null;
    this.publish();
  }
  reset(): void {
    if (this.destroyed) return;
    this.controller.reset();
    this.latest = null;
    this.publish();
  }
  subscribe(listener: AdaptiveQualityListener): () => void {
    if (this.destroyed) return () => undefined;
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    releaseResourceOperations("AdaptiveQualityCoordinator", [
      [
        "controller.reset",
        () => {
          this.controller.reset();
          this.latest = null;
          this.publish();
        },
      ],
      ["stream.unsubscribe", this.unsubscribeStream],
      ["socket.unsubscribe", this.unsubscribeSocket],
      ["listeners.clear", () => this.listeners.clear()],
    ]);
  }
  private handleStream(event: FrameStreamEvent): void {
    if (this.destroyed) return;
    if (
      event.type === "state.changed" &&
      event.state !== FrameStreamState.STARTING &&
      event.state !== FrameStreamState.STREAMING
    ) {
      this.reset();
      return;
    }
    if (
      event.type !== "metrics.changed" ||
      this.applying ||
      this.socket.getState() !== "OPEN" ||
      this.stream.getState() !== FrameStreamState.STREAMING ||
      this.controller.getState().mode !== "adaptive"
    )
      return;
    const metrics = event.metrics;
    const decision = this.controller.evaluate(this.stream.jpegQuality, {
      sentFrames: metrics.framesSent,
      backpressureDrops: metrics.framesDroppedForBackpressure,
      sendFailures: metrics.sendFailures,
      encodingFailures: metrics.encodingFailures,
      latestEncodedBytes: metrics.lastFrameSize ?? 0,
      bufferedAmountBytes: this.socket.getBufferedAmount(),
    });
    this.latest = decision;
    if (
      (decision.direction === "increased" ||
        decision.direction === "decreased") &&
      decision.quality !== this.stream.jpegQuality
    )
      try {
        this.applying = true;
        this.stream.setJpegQuality(decision.quality);
      } finally {
        this.applying = false;
      }
    this.publish();
  }
  private handleSocket(event: GestureWebSocketClientEvent): void {
    if (this.destroyed) return;
    if (
      (event.type === "state.changed" && event.state !== "OPEN") ||
      event.type === "reconnect.started"
    )
      this.reset();
  }
  private publish(): void {
    const snapshot = this.getSnapshot();
    for (const listener of [...this.listeners]) {
      try {
        listener(snapshot);
      } catch (error) {
        this.subscriberErrorHandler(error);
      }
    }
  }
}
