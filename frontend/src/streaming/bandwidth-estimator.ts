export type BandwidthConfidence = "unavailable" | "low" | "medium" | "high";
export type BandwidthPressure =
  | "unknown"
  | "healthy"
  | "constrained"
  | "overloaded";

export interface BandwidthEstimatorPolicy {
  readonly ewmaAlpha?: number;
  readonly minimumWindowMs?: number;
  readonly mediumConfidenceSamples?: number;
  readonly highConfidenceSamples?: number;
  readonly constrainedBufferedBytes?: number;
  readonly overloadedBufferedBytes?: number;
  readonly payloadUtilisationHeadroom?: number;
}
export interface BandwidthSample {
  readonly timestamp: number;
  readonly encodedPayloadBytes: number;
  readonly successfullySentFrames: number;
  readonly sendFailures: number;
  readonly backpressureDrops: number;
  readonly bufferedBytes: number;
  readonly targetFps: number;
  readonly jpegQuality: number;
  readonly width: number;
  readonly height: number;
}
export interface BandwidthEstimate {
  readonly instantaneousBitrateBps: number | null;
  readonly smoothedBitrateBps: number | null;
  readonly estimatedBytesPerSecond: number | null;
  readonly averageFrameBytes: number | null;
  readonly sampleCount: number;
  readonly elapsedWindowMs: number;
  readonly confidence: BandwidthConfidence;
  readonly pressure: BandwidthPressure;
  readonly latestBufferedBytes: number;
  readonly latestPayloadBytes: number;
  readonly sendFailureDelta: number;
  readonly backpressureDropDelta: number;
}

const DEFAULTS = Object.freeze({
  ewmaAlpha: 0.25,
  minimumWindowMs: 250,
  mediumConfidenceSamples: 5,
  highConfidenceSamples: 12,
  constrainedBufferedBytes: 131072,
  overloadedBufferedBytes: 262144,
  payloadUtilisationHeadroom: 0.8,
});

export class BandwidthEstimator {
  readonly policy: Required<BandwidthEstimatorPolicy>;
  private previous: BandwidthSample | null = null;
  private smoothed: number | null = null;
  private samples = 0;
  constructor(policy: BandwidthEstimatorPolicy = {}) {
    const normalized = Object.freeze({ ...DEFAULTS, ...policy });
    if (
      !Number.isFinite(normalized.ewmaAlpha) ||
      normalized.ewmaAlpha <= 0 ||
      normalized.ewmaAlpha > 1 ||
      !Number.isFinite(normalized.minimumWindowMs) ||
      normalized.minimumWindowMs <= 0 ||
      !Number.isSafeInteger(normalized.mediumConfidenceSamples) ||
      normalized.mediumConfidenceSamples < 1 ||
      !Number.isSafeInteger(normalized.highConfidenceSamples) ||
      normalized.highConfidenceSamples < normalized.mediumConfidenceSamples ||
      !Number.isSafeInteger(normalized.constrainedBufferedBytes) ||
      normalized.constrainedBufferedBytes < 0 ||
      !Number.isSafeInteger(normalized.overloadedBufferedBytes) ||
      normalized.overloadedBufferedBytes <
        normalized.constrainedBufferedBytes ||
      !Number.isFinite(normalized.payloadUtilisationHeadroom) ||
      normalized.payloadUtilisationHeadroom <= 0 ||
      normalized.payloadUtilisationHeadroom > 1
    )
      throw new RangeError("Invalid bandwidth estimator policy.");
    this.policy = normalized;
  }
  reset(): void {
    this.previous = null;
    this.smoothed = null;
    this.samples = 0;
  }
  getState(): BandwidthEstimate {
    return this.empty();
  }
  evaluate(sample?: BandwidthSample): BandwidthEstimate {
    if (!sample) {
      this.reset();
      return this.empty();
    }
    this.validate(sample);
    if (
      !this.previous ||
      sample.timestamp <= this.previous.timestamp ||
      this.regressed(sample)
    ) {
      this.reset();
      this.previous = sample;
      return this.make(null, 0, 0, sample, "unknown");
    }
    const elapsed = sample.timestamp - this.previous.timestamp;
    const bytes =
      Math.max(
        0,
        sample.successfullySentFrames - this.previous.successfullySentFrames,
      ) * sample.encodedPayloadBytes;
    const failures = sample.sendFailures - this.previous.sendFailures;
    const drops = sample.backpressureDrops - this.previous.backpressureDrops;
    this.previous = sample;
    if (elapsed < this.policy.minimumWindowMs)
      return this.make(null, failures, drops, sample, "unknown", elapsed);
    const instantaneous = (bytes * 8 * 1000) / elapsed;
    this.smoothed =
      this.smoothed === null
        ? instantaneous
        : this.policy.ewmaAlpha * instantaneous +
          (1 - this.policy.ewmaAlpha) * this.smoothed;
    this.samples += 1;
    const pressure: BandwidthPressure =
      failures > 0 ||
      drops > 0 ||
      sample.bufferedBytes >= this.policy.overloadedBufferedBytes
        ? "overloaded"
        : sample.bufferedBytes >= this.policy.constrainedBufferedBytes
          ? "constrained"
          : "healthy";
    return this.make(instantaneous, failures, drops, sample, pressure, elapsed);
  }
  private make(
    instantaneous: number | null = null,
    failures = 0,
    drops = 0,
    sample?: BandwidthSample,
    pressure: BandwidthPressure = "unknown",
    elapsed = 0,
  ): BandwidthEstimate {
    const confidence: BandwidthConfidence =
      this.samples === 0
        ? "unavailable"
        : this.samples >= this.policy.highConfidenceSamples
          ? "high"
          : this.samples >= this.policy.mediumConfidenceSamples
            ? "medium"
            : "low";
    return Object.freeze({
      instantaneousBitrateBps: instantaneous,
      smoothedBitrateBps: this.smoothed,
      estimatedBytesPerSecond:
        this.smoothed === null ? null : this.smoothed / 8,
      averageFrameBytes: sample?.encodedPayloadBytes ?? null,
      sampleCount: this.samples,
      elapsedWindowMs: elapsed,
      confidence,
      pressure,
      latestBufferedBytes: sample?.bufferedBytes ?? 0,
      latestPayloadBytes: sample?.encodedPayloadBytes ?? 0,
      sendFailureDelta: Math.max(0, failures),
      backpressureDropDelta: Math.max(0, drops),
    });
  }
  private empty(): BandwidthEstimate {
    return this.make();
  }
  private regressed(sample: BandwidthSample): boolean {
    const prior = this.previous;
    return (
      prior !== null &&
      (sample.successfullySentFrames < prior.successfullySentFrames ||
        sample.sendFailures < prior.sendFailures ||
        sample.backpressureDrops < prior.backpressureDrops)
    );
  }
  private validate(sample: BandwidthSample): void {
    if (
      !Number.isFinite(sample.timestamp) ||
      !Number.isSafeInteger(sample.encodedPayloadBytes) ||
      sample.encodedPayloadBytes < 0 ||
      !Number.isSafeInteger(sample.successfullySentFrames) ||
      sample.successfullySentFrames < 0 ||
      !Number.isSafeInteger(sample.sendFailures) ||
      sample.sendFailures < 0 ||
      !Number.isSafeInteger(sample.backpressureDrops) ||
      sample.backpressureDrops < 0 ||
      !Number.isSafeInteger(sample.bufferedBytes) ||
      sample.bufferedBytes < 0 ||
      !Number.isFinite(sample.targetFps) ||
      sample.targetFps <= 0 ||
      !Number.isFinite(sample.jpegQuality) ||
      sample.jpegQuality <= 0 ||
      sample.jpegQuality > 1 ||
      !Number.isSafeInteger(sample.width) ||
      sample.width < 1 ||
      !Number.isSafeInteger(sample.height) ||
      sample.height < 1
    )
      throw new RangeError("Invalid bandwidth sample.");
  }
}
