import { describe, expect, it } from "vitest";
import { BandwidthEstimator, type BandwidthSample } from "../src/streaming";

const sample = (values: Partial<BandwidthSample> = {}): BandwidthSample => ({
  timestamp: 0,
  encodedPayloadBytes: 100,
  successfullySentFrames: 0,
  sendFailures: 0,
  backpressureDrops: 0,
  bufferedBytes: 0,
  targetFps: 8,
  jpegQuality: 0.8,
  width: 640,
  height: 480,
  ...values,
});
describe("BandwidthEstimator", () => {
  it("provides an immutable valid default policy", () => {
    const estimator = new BandwidthEstimator();
    expect(estimator.policy.ewmaAlpha).toBe(0.25);
    expect(Object.isFrozen(estimator.policy)).toBe(true);
  });
  it.each([
    { ewmaAlpha: 0 },
    { minimumWindowMs: 0 },
    { mediumConfidenceSamples: 0 },
    { highConfidenceSamples: 1 },
    { constrainedBufferedBytes: -1 },
  ])("rejects invalid policy %#", (policy) =>
    expect(() => new BandwidthEstimator(policy)).toThrow(RangeError),
  );
  it("uses the first sample as a zero-bitrate baseline", () => {
    const state = new BandwidthEstimator().evaluate(sample());
    expect(state.confidence).toBe("unavailable");
    expect(state.smoothedBitrateBps).toBeNull();
  });
  it("calculates bits per second from sent-frame and timestamp deltas", () => {
    const estimator = new BandwidthEstimator({ minimumWindowMs: 1 });
    estimator.evaluate(sample());
    const state = estimator.evaluate(
      sample({ timestamp: 1000, successfullySentFrames: 2 }),
    );
    expect(state.instantaneousBitrateBps).toBe(1600);
    expect(state.estimatedBytesPerSecond).toBe(200);
  });
  it("smooths bitrate with EWMA", () => {
    const estimator = new BandwidthEstimator({
      minimumWindowMs: 1,
      ewmaAlpha: 0.25,
    });
    estimator.evaluate(sample());
    estimator.evaluate(sample({ timestamp: 1000, successfullySentFrames: 2 }));
    const state = estimator.evaluate(
      sample({ timestamp: 2000, successfullySentFrames: 6 }),
    );
    expect(state.smoothedBitrateBps).toBe(2000);
  });
  it("does not create Infinity for zero elapsed time", () => {
    const estimator = new BandwidthEstimator();
    estimator.evaluate(sample());
    expect(
      estimator.evaluate(sample({ successfullySentFrames: 1 }))
        .smoothedBitrateBps,
    ).toBeNull();
  });
  it("resets safely when counters regress", () => {
    const estimator = new BandwidthEstimator({ minimumWindowMs: 1 });
    estimator.evaluate(sample({ successfullySentFrames: 5 }));
    expect(estimator.evaluate(sample({ timestamp: 1000 })).confidence).toBe(
      "unavailable",
    );
  });
  it("classifies failure and drop deltas as overload", () => {
    const estimator = new BandwidthEstimator({ minimumWindowMs: 1 });
    estimator.evaluate(sample());
    expect(
      estimator.evaluate(sample({ timestamp: 1000, sendFailures: 1 })).pressure,
    ).toBe("overloaded");
    estimator.reset();
    estimator.evaluate(sample());
    expect(
      estimator.evaluate(sample({ timestamp: 1000, backpressureDrops: 1 }))
        .pressure,
    ).toBe("overloaded");
  });
  it("classifies buffer thresholds and healthy samples", () => {
    const estimator = new BandwidthEstimator({ minimumWindowMs: 1 });
    estimator.evaluate(sample());
    expect(
      estimator.evaluate(sample({ timestamp: 1000, bufferedBytes: 131072 }))
        .pressure,
    ).toBe("constrained");
    expect(
      estimator.evaluate(sample({ timestamp: 2000, bufferedBytes: 262144 }))
        .pressure,
    ).toBe("overloaded");
    expect(estimator.evaluate(sample({ timestamp: 3000 })).pressure).toBe(
      "healthy",
    );
  });
  it("progresses confidence and returns immutable state", () => {
    const estimator = new BandwidthEstimator({
      minimumWindowMs: 1,
      mediumConfidenceSamples: 2,
      highConfidenceSamples: 3,
    });
    estimator.evaluate(sample());
    expect(
      estimator.evaluate(sample({ timestamp: 1, successfullySentFrames: 1 }))
        .confidence,
    ).toBe("low");
    expect(
      estimator.evaluate(sample({ timestamp: 2, successfullySentFrames: 2 }))
        .confidence,
    ).toBe("medium");
    const state = estimator.evaluate(
      sample({ timestamp: 3, successfullySentFrames: 3 }),
    );
    expect(state.confidence).toBe("high");
    expect(Object.isFrozen(state)).toBe(true);
  });
  it("returns unavailable state when a sample is missing", () => {
    expect(new BandwidthEstimator().evaluate()).toMatchObject({
      confidence: "unavailable",
      pressure: "unknown",
      smoothedBitrateBps: null,
    });
  });
  it("handles a negative timestamp delta as a fresh baseline", () => {
    const estimator = new BandwidthEstimator({ minimumWindowMs: 1 });
    estimator.evaluate(sample({ timestamp: 10, successfullySentFrames: 2 }));
    expect(
      estimator.evaluate(sample({ timestamp: 9, successfullySentFrames: 3 })),
    ).toMatchObject({
      confidence: "unavailable",
      instantaneousBitrateBps: null,
    });
  });
  it("calculates average encoded frame size safely", () => {
    const estimator = new BandwidthEstimator({ minimumWindowMs: 1 });
    estimator.evaluate(sample());
    expect(
      estimator.evaluate(
        sample({
          timestamp: 1000,
          encodedPayloadBytes: 321,
          successfullySentFrames: 1,
        }),
      ).averageFrameBytes,
    ).toBe(321);
  });
  it("interrupts confidence growth for a missing sample", () => {
    const estimator = new BandwidthEstimator({ minimumWindowMs: 1 });
    estimator.evaluate(sample());
    estimator.evaluate(sample({ timestamp: 1, successfullySentFrames: 1 }));
    estimator.evaluate();
    expect(estimator.getState().confidence).toBe("unavailable");
  });
  it("manual reset clears bitrate and confidence", () => {
    const estimator = new BandwidthEstimator({ minimumWindowMs: 1 });
    estimator.evaluate(sample());
    estimator.evaluate(sample({ timestamp: 1, successfullySentFrames: 1 }));
    estimator.reset();
    expect(estimator.getState()).toMatchObject({
      smoothedBitrateBps: null,
      confidence: "unavailable",
      sampleCount: 0,
    });
  });
  it("uses the same reset contract for stream stop and disconnect", () => {
    const estimator = new BandwidthEstimator({ minimumWindowMs: 1 });
    estimator.evaluate(sample());
    estimator.evaluate(sample({ timestamp: 1, successfullySentFrames: 1 }));
    estimator.reset();
    expect(
      estimator.evaluate(sample({ timestamp: 2, successfullySentFrames: 2 }))
        .confidence,
    ).toBe("unavailable");
    estimator.reset();
    expect(estimator.getState().sampleCount).toBe(0);
  });
  it("keeps policy and returned state independent from caller mutation", () => {
    const input = sample();
    const estimator = new BandwidthEstimator();
    const state = estimator.evaluate(input);
    (input as { encodedPayloadBytes: number }).encodedPayloadBytes = 999;
    expect(state.latestPayloadBytes).toBe(100);
    expect(Object.isFrozen(state)).toBe(true);
  });
});
