import type {
  BandwidthEstimator,
  BandwidthEstimate,
  BandwidthSample,
} from "./bandwidth-estimator";
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

export interface ResolutionProfile {
  readonly id: string;
  readonly width: number;
  readonly height: number;
}
export type AdaptiveResolutionMode = "adaptive" | "fixed";
export type AdaptiveResolutionDirection =
  | "increased"
  | "decreased"
  | "unchanged"
  | "reset";
export type AdaptiveResolutionReason =
  | "sustained_overload"
  | "insufficient_bandwidth"
  | "transport_failure"
  | "quality_floor"
  | "healthy_headroom"
  | "cooldown"
  | "insufficient_data"
  | "fixed_mode"
  | "disconnected"
  | "stream_stopped"
  | "counter_reset"
  | "manual_reset";
export interface AdaptiveResolutionPolicy {
  readonly enabled?: boolean;
  readonly overloadSamplesBeforeDecrease?: number;
  readonly healthySamplesBeforeIncrease?: number;
  readonly cooldownMs?: number;
  readonly minimumProfile?: string;
  readonly maximumProfile?: string;
  readonly qualityFloorTolerance?: number;
  readonly requiredBandwidthHeadroom?: number;
}
export interface ResolutionSample {
  readonly estimate: BandwidthEstimate;
  readonly currentProfile: string;
  readonly jpegQuality: number;
  readonly minimumJpegQuality: number;
  readonly targetFps: number;
  readonly streaming: boolean;
  readonly socketOpen: boolean;
}
export interface AdaptiveResolutionDecision {
  readonly previousProfile: string;
  readonly profile: string;
  readonly previousWidth: number;
  readonly previousHeight: number;
  readonly width: number;
  readonly height: number;
  readonly direction: AdaptiveResolutionDirection;
  readonly reason: AdaptiveResolutionReason;
  readonly healthySamples: number;
  readonly overloadSamples: number;
  readonly estimatedBandwidth: number | null;
  readonly estimatedRequiredBandwidth: number | null;
  readonly headroomRatio: number | null;
  readonly cooldownActive: boolean;
  readonly adjustedAt: number | null;
}
export interface AdaptiveResolutionState {
  readonly mode: AdaptiveResolutionMode;
  readonly healthySamples: number;
  readonly overloadSamples: number;
  readonly hasBaseline: boolean;
  readonly lastAdjustmentAt: number | null;
}
export const DEFAULT_RESOLUTION_PROFILES: readonly ResolutionProfile[] =
  Object.freeze([
    Object.freeze({ id: "low", width: 320, height: 240 }),
    Object.freeze({ id: "medium", width: 480, height: 360 }),
    Object.freeze({ id: "high", width: 640, height: 480 }),
  ]);

export class AdaptiveResolutionController {
  readonly profiles: readonly ResolutionProfile[];
  readonly policy: Required<AdaptiveResolutionPolicy>;
  private mode: AdaptiveResolutionMode;
  private baseline = false;
  private healthy = 0;
  private overload = 0;
  private lastAdjustment: number | null = null;
  constructor(
    profiles: readonly ResolutionProfile[] = DEFAULT_RESOLUTION_PROFILES,
    policy: AdaptiveResolutionPolicy = {},
    private readonly now: () => number = () => performance.now(),
  ) {
    if (profiles.length < 1)
      throw new RangeError("Resolution profiles are required.");
    const cloned = profiles.map((profile) => Object.freeze({ ...profile }));
    if (
      new Set(cloned.map((p) => p.id)).size !== cloned.length ||
      cloned.some(
        (p) =>
          !p.id ||
          !Number.isSafeInteger(p.width) ||
          p.width < 1 ||
          !Number.isSafeInteger(p.height) ||
          p.height < 1,
      ) ||
      cloned.some((p, i) => {
        const previous = cloned[i - 1];
        return (
          previous !== undefined &&
          p.width * p.height <= previous.width * previous.height
        );
      })
    )
      throw new RangeError("Invalid resolution profiles.");
    this.profiles = Object.freeze(cloned);
    const normalized = Object.freeze({
      enabled: policy.enabled ?? true,
      overloadSamplesBeforeDecrease: policy.overloadSamplesBeforeDecrease ?? 3,
      healthySamplesBeforeIncrease: policy.healthySamplesBeforeIncrease ?? 20,
      cooldownMs: policy.cooldownMs ?? 4000,
      minimumProfile: policy.minimumProfile ?? this.required(cloned[0]).id,
      maximumProfile: policy.maximumProfile ?? this.required(cloned.at(-1)).id,
      qualityFloorTolerance: policy.qualityFloorTolerance ?? 0.02,
      requiredBandwidthHeadroom: policy.requiredBandwidthHeadroom ?? 1.3,
    });
    if (
      !Number.isSafeInteger(normalized.overloadSamplesBeforeDecrease) ||
      normalized.overloadSamplesBeforeDecrease < 1 ||
      !Number.isSafeInteger(normalized.healthySamplesBeforeIncrease) ||
      normalized.healthySamplesBeforeIncrease < 1 ||
      !Number.isFinite(normalized.cooldownMs) ||
      normalized.cooldownMs < 0 ||
      !Number.isFinite(normalized.qualityFloorTolerance) ||
      normalized.qualityFloorTolerance < 0 ||
      !Number.isFinite(normalized.requiredBandwidthHeadroom) ||
      normalized.requiredBandwidthHeadroom <= 1 ||
      !this.find(normalized.minimumProfile) ||
      !this.find(normalized.maximumProfile) ||
      this.index(normalized.minimumProfile) >
        this.index(normalized.maximumProfile)
    )
      throw new RangeError("Invalid adaptive resolution policy.");
    this.policy = normalized;
    this.mode = normalized.enabled ? "adaptive" : "fixed";
  }
  setMode(mode: AdaptiveResolutionMode): void {
    if (mode !== "adaptive" && mode !== "fixed")
      throw new RangeError("Invalid resolution mode.");
    if (this.mode !== mode) {
      this.mode = mode;
      this.reset();
    }
  }
  reset(): void {
    this.baseline = false;
    this.healthy = 0;
    this.overload = 0;
    this.lastAdjustment = null;
  }
  getState(): AdaptiveResolutionState {
    return Object.freeze({
      mode: this.mode,
      healthySamples: this.healthy,
      overloadSamples: this.overload,
      hasBaseline: this.baseline,
      lastAdjustmentAt: this.lastAdjustment,
    });
  }
  isCooldownActive(): boolean {
    return (
      this.lastAdjustment !== null &&
      this.now() - this.lastAdjustment < this.policy.cooldownMs
    );
  }
  evaluate(sample?: ResolutionSample): AdaptiveResolutionDecision {
    const current = this.required(
      this.find(sample?.currentProfile ?? this.policy.minimumProfile),
    );
    const make = (
      profile: ResolutionProfile,
      direction: AdaptiveResolutionDirection,
      reason: AdaptiveResolutionReason,
    ): AdaptiveResolutionDecision => {
      const estimated = sample?.estimate.smoothedBitrateBps ?? null;
      const required = sample
        ? current.width * current.height * sample.targetFps * 0.12
        : null;
      return Object.freeze({
        previousProfile: current.id,
        profile: profile.id,
        previousWidth: current.width,
        previousHeight: current.height,
        width: profile.width,
        height: profile.height,
        direction,
        reason,
        healthySamples: this.healthy,
        overloadSamples: this.overload,
        estimatedBandwidth: estimated,
        estimatedRequiredBandwidth: required,
        headroomRatio:
          estimated !== null && required && required > 0
            ? estimated / required
            : null,
        cooldownActive: this.isCooldownActive(),
        adjustedAt: this.lastAdjustment,
      });
    };
    if (!sample) return make(current, "unchanged", "insufficient_data");
    if (!this.find(sample.currentProfile))
      throw new RangeError("Current profile is not configured.");
    if (this.mode === "fixed") return make(current, "unchanged", "fixed_mode");
    if (!sample.socketOpen) {
      this.reset();
      return make(current, "reset", "disconnected");
    }
    if (!sample.streaming) {
      this.reset();
      return make(current, "reset", "stream_stopped");
    }
    if (sample.estimate.confidence === "unavailable") {
      this.baseline = true;
      this.healthy = 0;
      this.overload = 0;
      return make(current, "reset", "insufficient_data");
    }
    this.baseline = true;
    const overloaded =
      sample.estimate.pressure === "overloaded" ||
      sample.estimate.sendFailureDelta > 0 ||
      sample.estimate.backpressureDropDelta > 0;
    const qualityFloor =
      sample.jpegQuality <=
      sample.minimumJpegQuality + this.policy.qualityFloorTolerance;
    if (overloaded) {
      this.overload += 1;
      this.healthy = 0;
      if (this.overload < this.policy.overloadSamplesBeforeDecrease)
        return make(current, "unchanged", "sustained_overload");
      if (this.isCooldownActive())
        return make(current, "unchanged", "cooldown");
      if (!qualityFloor && sample.estimate.sendFailureDelta === 0)
        return make(current, "unchanged", "quality_floor");
      const next = this.profileAt(this.index(current.id) - 1);
      this.lastAdjustment = this.now();
      this.overload = 0;
      return make(
        next,
        next.id === current.id ? "unchanged" : "decreased",
        sample.estimate.sendFailureDelta > 0
          ? "transport_failure"
          : "sustained_overload",
      );
    }
    this.overload = 0;
    const next = this.profileAt(this.index(current.id) + 1);
    const required = next.width * next.height * sample.targetFps * 0.12;
    const headroom =
      sample.estimate.smoothedBitrateBps === null
        ? 0
        : sample.estimate.smoothedBitrateBps / required;
    if (
      sample.estimate.pressure !== "healthy" ||
      headroom < this.policy.requiredBandwidthHeadroom
    ) {
      this.healthy = 0;
      return make(current, "unchanged", "insufficient_bandwidth");
    }
    this.healthy += 1;
    if (this.healthy < this.policy.healthySamplesBeforeIncrease)
      return make(current, "unchanged", "healthy_headroom");
    if (this.isCooldownActive()) return make(current, "unchanged", "cooldown");
    this.healthy = 0;
    this.lastAdjustment = this.now();
    return make(
      next,
      next.id === current.id ? "unchanged" : "increased",
      "healthy_headroom",
    );
  }
  private find(id: string): ResolutionProfile | undefined {
    return this.profiles.find((profile) => profile.id === id);
  }
  private index(id: string): number {
    return this.profiles.findIndex((profile) => profile.id === id);
  }
  private profileAt(index: number): ResolutionProfile {
    const low = this.index(this.policy.minimumProfile);
    const high = this.index(this.policy.maximumProfile);
    return this.required(this.profiles[Math.max(low, Math.min(high, index))]);
  }
  private required<T>(value: T | undefined): T {
    if (value === undefined)
      throw new RangeError("Configured resolution profile is unavailable.");
    return value;
  }
}

export interface AdaptiveResolutionSnapshot {
  readonly mode: AdaptiveResolutionMode;
  readonly currentProfile: ResolutionProfile;
  readonly minimumProfile: ResolutionProfile;
  readonly maximumProfile: ResolutionProfile;
  readonly latestDecision: AdaptiveResolutionDecision | null;
  readonly estimate: BandwidthEstimate;
  readonly healthySamples: number;
  readonly overloadSamples: number;
  readonly cooldownActive: boolean;
}
export interface ResolutionAdaptiveStream {
  readonly jpegQuality: number;
  readonly targetFps: number;
  readonly outputWidth: number;
  readonly outputHeight: number;
  getState(): FrameStreamState;
  getMetrics(): FrameStreamMetrics;
  setOutputResolution(width: number, height: number): void;
  subscribe(listener: (event: FrameStreamEvent) => void): () => void;
}
export interface ResolutionSocketSource {
  getState(): WebSocketClientState;
  getBufferedAmount(): number;
  subscribe(listener: (event: GestureWebSocketClientEvent) => void): () => void;
}
export type AdaptiveResolutionListener = (
  snapshot: AdaptiveResolutionSnapshot,
) => void;
export class AdaptiveResolutionCoordinator {
  private readonly listeners = new Set<AdaptiveResolutionListener>();
  private readonly unsubscribeStream: () => void;
  private readonly unsubscribeSocket: () => void;
  private latest: AdaptiveResolutionDecision | null = null;
  private estimate: BandwidthEstimate;
  private destroyed = false;
  private applying = false;
  constructor(
    readonly controller: AdaptiveResolutionController,
    readonly estimator: BandwidthEstimator,
    private readonly stream: ResolutionAdaptiveStream,
    private readonly socket: ResolutionSocketSource,
    private readonly minimumQuality: number,
    private readonly now: () => number = () => performance.now(),
  ) {
    this.estimate = estimator.getState();
    this.unsubscribeStream = stream.subscribe((event) =>
      this.handleStream(event),
    );
    this.unsubscribeSocket = socket.subscribe((event) =>
      this.handleSocket(event),
    );
  }
  getSnapshot(): AdaptiveResolutionSnapshot {
    const current = this.profileFor(
      this.stream.outputWidth,
      this.stream.outputHeight,
    );
    const state = this.controller.getState();
    return Object.freeze({
      mode: state.mode,
      currentProfile: current,
      minimumProfile: this.requiredProfile(
        this.controller.policy.minimumProfile,
      ),
      maximumProfile: this.requiredProfile(
        this.controller.policy.maximumProfile,
      ),
      latestDecision: this.latest,
      estimate: this.estimate,
      healthySamples: state.healthySamples,
      overloadSamples: state.overloadSamples,
      cooldownActive: this.controller.isCooldownActive(),
    });
  }
  setMode(mode: AdaptiveResolutionMode): void {
    if (this.destroyed || mode === this.controller.getState().mode) return;
    this.controller.setMode(mode);
    this.estimator.reset();
    this.estimate = this.estimator.getState();
    this.latest = null;
    this.publish();
  }
  reset(): void {
    if (this.destroyed) return;
    this.controller.reset();
    this.estimator.reset();
    this.estimate = this.estimator.getState();
    this.latest = null;
    this.publish();
  }
  subscribe(listener: AdaptiveResolutionListener): () => void {
    if (this.destroyed) return () => undefined;
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    releaseResourceOperations("AdaptiveResolutionCoordinator", [
      [
        "controller.reset",
        () => {
          this.controller.reset();
          this.estimator.reset();
          this.estimate = this.estimator.getState();
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
    if (event.type !== "metrics.changed" || this.applying || this.destroyed)
      return;
    const metrics = event.metrics;
    const sample: BandwidthSample = {
      timestamp: this.now(),
      encodedPayloadBytes: metrics.lastFrameSize ?? 0,
      successfullySentFrames: metrics.framesSent,
      sendFailures: metrics.sendFailures,
      backpressureDrops: metrics.framesDroppedForBackpressure,
      bufferedBytes: this.socket.getBufferedAmount(),
      targetFps: this.stream.targetFps,
      jpegQuality: this.stream.jpegQuality,
      width: this.stream.outputWidth || metrics.lastFrameWidth || 1,
      height: this.stream.outputHeight || metrics.lastFrameHeight || 1,
    };
    this.estimate = this.estimator.evaluate(sample);
    if (
      this.socket.getState() !== "OPEN" ||
      this.stream.getState() !== FrameStreamState.STREAMING ||
      this.controller.getState().mode !== "adaptive"
    ) {
      this.publish();
      return;
    }
    const current = this.profileFor(
      this.stream.outputWidth,
      this.stream.outputHeight,
    );
    this.latest = this.controller.evaluate({
      estimate: this.estimate,
      currentProfile: current.id,
      jpegQuality: this.stream.jpegQuality,
      minimumJpegQuality: this.minimumQuality,
      targetFps: this.stream.targetFps,
      streaming: true,
      socketOpen: true,
    });
    if (
      (this.latest.direction === "increased" ||
        this.latest.direction === "decreased") &&
      (this.latest.width !== this.stream.outputWidth ||
        this.latest.height !== this.stream.outputHeight)
    ) {
      try {
        this.applying = true;
        this.stream.setOutputResolution(this.latest.width, this.latest.height);
      } finally {
        this.applying = false;
      }
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
  private profileFor(width: number, height: number): ResolutionProfile {
    return (
      this.controller.profiles.find(
        (profile) => profile.width === width && profile.height === height,
      ) ?? this.requiredProfile(this.controller.policy.maximumProfile)
    );
  }
  private requiredProfile(id: string): ResolutionProfile {
    const profile = this.controller.profiles.find(
      (candidate) => candidate.id === id,
    );
    if (!profile)
      throw new RangeError("Configured resolution profile is unavailable.");
    return profile;
  }
  private publish(): void {
    const snapshot = this.getSnapshot();
    for (const listener of [...this.listeners]) listener(snapshot);
  }
}
