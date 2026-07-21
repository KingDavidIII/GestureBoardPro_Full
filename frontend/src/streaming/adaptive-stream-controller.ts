import type { SchedulerMetadata } from "../protocol";
import { releaseResourceOperations } from "../lifecycle/resource-cleanup";
import type {
  GestureWebSocketClientEvent,
  WebSocketClientState,
} from "../websocket";
import { FrameStreamState, type FrameStreamEvent } from "./stream-state";

export type AdaptiveMode = "adaptive" | "fixed";
export type AdaptiveDirection =
  | "increased"
  | "decreased"
  | "unchanged"
  | "reset";
export type AdaptiveReason =
  | "server_drop"
  | "queue_delay"
  | "pending_frame"
  | "processing_capacity"
  | "healthy_window"
  | "cooldown"
  | "insufficient_data"
  | "disabled"
  | "disconnected"
  | "manual_reset";

export interface AdaptiveStreamPolicy {
  readonly enabled?: boolean;
  readonly minimumFps?: number;
  readonly maximumFps: number;
  readonly decreaseFactor?: number;
  readonly increaseFps?: number;
  readonly healthySamplesBeforeIncrease?: number;
  readonly cooldownMs?: number;
  readonly queueDelayThresholdMs?: number;
  readonly utilisationThreshold?: number;
}

export interface AdaptiveDecision {
  readonly previousTargetFps: number;
  readonly targetFps: number;
  readonly direction: AdaptiveDirection;
  readonly reason: AdaptiveReason;
  readonly healthySamples: number;
  readonly overloadSamples: number;
  readonly estimatedCapacityFps: number | null;
  readonly adjustedAt: number | null;
}

export interface AdaptiveStreamState {
  readonly mode: AdaptiveMode;
  readonly healthySamples: number;
  readonly overloadSamples: number;
  readonly hasBaseline: boolean;
  readonly lastAdjustmentAt: number | null;
}

export interface AdaptiveStreamSnapshot {
  readonly mode: AdaptiveMode;
  readonly targetFps: number;
  readonly minimumFps: number;
  readonly maximumFps: number;
  readonly latestDecision: AdaptiveDecision | null;
  readonly healthySamples: number;
  readonly overloadSamples: number;
  readonly estimatedCapacityFps: number | null;
  readonly cooldownActive: boolean;
}

export type AdaptiveStreamListener = (snapshot: AdaptiveStreamSnapshot) => void;

export interface AdaptiveFrameStream {
  readonly targetFps: number;
  getState(): FrameStreamState;
  setTargetFps(targetFps: number): void;
  subscribe(listener: (event: FrameStreamEvent) => void): () => void;
}

export interface AdaptiveWebSocketSource {
  getState(): WebSocketClientState;
  subscribe(listener: (event: GestureWebSocketClientEvent) => void): () => void;
}

export class AdaptiveStreamController {
  readonly policy: Required<AdaptiveStreamPolicy>;
  private mode: AdaptiveMode;
  private previous: SchedulerMetadata | null = null;
  private healthy = 0;
  private overload = 0;
  private lastAdjustment: number | null = null;

  constructor(
    policy: AdaptiveStreamPolicy,
    private readonly now: () => number = () => performance.now(),
  ) {
    const normalized = {
      enabled: policy.enabled ?? true,
      minimumFps: policy.minimumFps ?? 5,
      maximumFps: policy.maximumFps,
      decreaseFactor: policy.decreaseFactor ?? 0.75,
      increaseFps: policy.increaseFps ?? 1,
      healthySamplesBeforeIncrease: policy.healthySamplesBeforeIncrease ?? 8,
      cooldownMs: policy.cooldownMs ?? 1500,
      queueDelayThresholdMs: policy.queueDelayThresholdMs ?? 80,
      utilisationThreshold: policy.utilisationThreshold ?? 0.9,
    };
    if (
      !Number.isFinite(normalized.minimumFps) ||
      !Number.isFinite(normalized.maximumFps) ||
      normalized.minimumFps <= 0 ||
      normalized.maximumFps < normalized.minimumFps ||
      !Number.isFinite(normalized.decreaseFactor) ||
      normalized.decreaseFactor <= 0 ||
      normalized.decreaseFactor >= 1 ||
      !Number.isFinite(normalized.increaseFps) ||
      normalized.increaseFps <= 0 ||
      !Number.isSafeInteger(normalized.healthySamplesBeforeIncrease) ||
      normalized.healthySamplesBeforeIncrease < 1 ||
      !Number.isFinite(normalized.cooldownMs) ||
      normalized.cooldownMs < 0 ||
      !Number.isFinite(normalized.queueDelayThresholdMs) ||
      normalized.queueDelayThresholdMs < 0 ||
      !Number.isFinite(normalized.utilisationThreshold) ||
      normalized.utilisationThreshold <= 0 ||
      normalized.utilisationThreshold > 1
    )
      throw new RangeError("Invalid adaptive stream policy.");
    this.policy = Object.freeze(normalized);
    this.mode = normalized.enabled ? "adaptive" : "fixed";
  }

  setMode(mode: AdaptiveMode): void {
    this.mode = mode;
    this.reset();
  }
  reset(): void {
    this.previous = null;
    this.healthy = 0;
    this.overload = 0;
    this.lastAdjustment = null;
  }
  getState(): AdaptiveStreamState {
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
  evaluate(targetFps: number, sample?: SchedulerMetadata): AdaptiveDecision {
    if (!Number.isFinite(targetFps) || targetFps <= 0)
      throw new RangeError("targetFps must be finite and positive.");
    const decision = (
      target: number,
      direction: AdaptiveDirection,
      reason: AdaptiveReason,
      capacity: number | null = null,
    ): AdaptiveDecision =>
      Object.freeze({
        previousTargetFps: targetFps,
        targetFps: target,
        direction,
        reason,
        healthySamples: this.healthy,
        overloadSamples: this.overload,
        estimatedCapacityFps: capacity,
        adjustedAt: this.lastAdjustment,
      });
    if (this.mode === "fixed")
      return decision(targetFps, "unchanged", "disabled");
    if (!sample) {
      this.healthy = 0;
      return decision(targetFps, "unchanged", "insufficient_data");
    }
    if (
      !this.previous ||
      sample.received_frames < this.previous.received_frames ||
      sample.dropped_frames < this.previous.dropped_frames
    ) {
      this.previous = sample;
      this.healthy = 0;
      return decision(targetFps, "reset", "insufficient_data");
    }
    const drops = sample.dropped_frames - this.previous.dropped_frames;
    const capacity =
      sample.processing_time_ms > 0 ? 1000 / sample.processing_time_ms : null;
    const reason =
      drops > 0
        ? "server_drop"
        : sample.pending_frames === 1
          ? "pending_frame"
          : sample.queue_delay_ms >= this.policy.queueDelayThresholdMs
            ? "queue_delay"
            : capacity !== null &&
                capacity * this.policy.utilisationThreshold < targetFps
              ? "processing_capacity"
              : null;
    this.previous = sample;
    if (reason) {
      this.overload++;
      this.healthy = 0;
      if (
        this.lastAdjustment !== null &&
        this.now() - this.lastAdjustment < this.policy.cooldownMs
      )
        return decision(targetFps, "unchanged", "cooldown", capacity);
      const next = Math.max(
        this.policy.minimumFps,
        Math.floor(targetFps * this.policy.decreaseFactor),
      );
      this.lastAdjustment = this.now();
      return decision(
        next,
        next === targetFps ? "unchanged" : "decreased",
        reason,
        capacity,
      );
    }
    this.overload = 0;
    this.healthy++;
    if (this.healthy < this.policy.healthySamplesBeforeIncrease)
      return decision(targetFps, "unchanged", "healthy_window", capacity);
    if (
      this.lastAdjustment !== null &&
      this.now() - this.lastAdjustment < this.policy.cooldownMs
    )
      return decision(targetFps, "unchanged", "cooldown", capacity);
    const next = Math.min(
      this.policy.maximumFps,
      targetFps + this.policy.increaseFps,
    );
    this.healthy = 0;
    this.lastAdjustment = this.now();
    return decision(
      next,
      next === targetFps ? "unchanged" : "increased",
      "healthy_window",
      capacity,
    );
  }
}

export class AdaptiveStreamCoordinator {
  private readonly listeners = new Set<AdaptiveStreamListener>();
  private readonly unsubscribeSocket: () => void;
  private readonly unsubscribeStream: () => void;
  private latestDecision: AdaptiveDecision | null = null;
  private destroyed = false;

  constructor(
    readonly controller: AdaptiveStreamController,
    private readonly stream: AdaptiveFrameStream,
    private readonly socket: AdaptiveWebSocketSource,
  ) {
    this.unsubscribeSocket = socket.subscribe((event) =>
      this.handleSocket(event),
    );
    this.unsubscribeStream = stream.subscribe((event) =>
      this.handleStream(event),
    );
  }

  getSnapshot(): AdaptiveStreamSnapshot {
    const state = this.controller.getState();
    return Object.freeze({
      mode: state.mode,
      targetFps: this.stream.targetFps,
      minimumFps: this.controller.policy.minimumFps,
      maximumFps: this.controller.policy.maximumFps,
      latestDecision: this.latestDecision,
      healthySamples: state.healthySamples,
      overloadSamples: state.overloadSamples,
      estimatedCapacityFps: this.latestDecision?.estimatedCapacityFps ?? null,
      cooldownActive: this.controller.isCooldownActive(),
    });
  }

  setMode(mode: AdaptiveMode): void {
    if (this.destroyed || this.controller.getState().mode === mode) return;
    this.controller.setMode(mode);
    this.latestDecision = null;
    this.publish();
  }

  reset(): void {
    if (this.destroyed) return;
    this.controller.reset();
    this.latestDecision = null;
    this.publish();
  }

  subscribe(listener: AdaptiveStreamListener): () => void {
    if (this.destroyed) return () => undefined;
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    releaseResourceOperations("AdaptiveStreamCoordinator", [
      [
        "controller.reset",
        () => {
          this.controller.reset();
          this.latestDecision = null;
          this.publish();
        },
      ],
      ["socket.unsubscribe", this.unsubscribeSocket],
      ["stream.unsubscribe", this.unsubscribeStream],
      ["listeners.clear", () => this.listeners.clear()],
    ]);
  }

  private handleSocket(event: GestureWebSocketClientEvent): void {
    if (this.destroyed) return;
    if (event.type === "state.changed" && event.state !== "OPEN") {
      this.reset();
      return;
    }
    if (event.type === "reconnect.started") {
      this.reset();
      return;
    }
    if (
      event.type !== "protocol.message" ||
      event.message.type !== "gesture.result" ||
      !event.message.scheduler ||
      this.socket.getState() !== "OPEN" ||
      this.stream.getState() !== FrameStreamState.STREAMING ||
      this.controller.getState().mode !== "adaptive"
    )
      return;
    const decision = this.controller.evaluate(
      this.stream.targetFps,
      event.message.scheduler,
    );
    this.latestDecision = decision;
    if (
      (decision.direction === "increased" ||
        decision.direction === "decreased") &&
      decision.targetFps !== this.stream.targetFps
    )
      this.stream.setTargetFps(decision.targetFps);
    this.publish();
  }

  private handleStream(event: FrameStreamEvent): void {
    if (this.destroyed) return;
    if (
      event.type === "state.changed" &&
      event.state !== FrameStreamState.STARTING &&
      event.state !== FrameStreamState.STREAMING
    )
      this.reset();
  }

  private publish(): void {
    const snapshot = this.getSnapshot();
    for (const listener of [...this.listeners]) listener(snapshot);
  }
}
