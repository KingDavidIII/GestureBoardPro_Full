import { describe, expect, it } from "vitest";
import {
  AdaptiveResolutionController,
  DEFAULT_RESOLUTION_PROFILES,
  type ResolutionSample,
} from "../src/streaming";

const sample = (values: Partial<ResolutionSample> = {}): ResolutionSample => ({
  currentProfile: "high",
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
    confidence: "high",
    pressure: "healthy",
    latestBufferedBytes: 0,
    latestPayloadBytes: 100,
    sendFailureDelta: 0,
    backpressureDropDelta: 0,
  },
  ...values,
});
describe("AdaptiveResolutionController", () => {
  it("uses immutable default profiles and policy", () => {
    const controller = new AdaptiveResolutionController();
    expect(controller.profiles).toEqual(DEFAULT_RESOLUTION_PROFILES);
    expect(Object.isFrozen(controller.profiles)).toBe(true);
    expect(Object.isFrozen(controller.policy)).toBe(true);
  });
  it.each([
    [[{ id: "bad", width: 0, height: 1 }]],
    [
      [
        { id: "same", width: 1, height: 1 },
        { id: "same", width: 2, height: 2 },
      ],
    ],
    [
      [
        { id: "high", width: 640, height: 480 },
        { id: "low", width: 320, height: 240 },
      ],
    ],
  ])("rejects invalid profiles %#", (profiles) =>
    expect(() => new AdaptiveResolutionController(profiles)).toThrow(
      RangeError,
    ),
  );
  it.each([
    { overloadSamplesBeforeDecrease: 0 },
    { healthySamplesBeforeIncrease: 0 },
    { cooldownMs: -1 },
    { requiredBandwidthHeadroom: 1 },
  ])("rejects invalid policy %#", (policy) =>
    expect(
      () =>
        new AdaptiveResolutionController(DEFAULT_RESOLUTION_PROFILES, policy),
    ).toThrow(RangeError),
  );
  it("does not adjust without an estimate", () =>
    expect(new AdaptiveResolutionController().evaluate().reason).toBe(
      "insufficient_data",
    ));
  it("requires sustained pressure then decreases exactly one level", () => {
    let now = 0;
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { overloadSamplesBeforeDecrease: 2, cooldownMs: 0 },
      () => now,
    );
    const overloaded = sample({
      estimate: { ...sample().estimate, pressure: "overloaded" },
    });
    expect(controller.evaluate(overloaded).direction).toBe("unchanged");
    now = 1;
    const decision = controller.evaluate(overloaded);
    expect(decision).toMatchObject({
      direction: "decreased",
      profile: "medium",
    });
  });
  it("clamps decrease at the minimum profile", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { overloadSamplesBeforeDecrease: 1, cooldownMs: 0 },
    );
    const decision = controller.evaluate(
      sample({
        currentProfile: "low",
        estimate: { ...sample().estimate, pressure: "overloaded" },
      }),
    );
    expect(decision.profile).toBe("low");
  });
  it("requires quality floor except for transport failures", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { overloadSamplesBeforeDecrease: 1, cooldownMs: 0 },
    );
    expect(
      controller.evaluate(
        sample({
          jpegQuality: 0.8,
          estimate: { ...sample().estimate, pressure: "overloaded" },
        }),
      ).reason,
    ).toBe("quality_floor");
    expect(
      controller.evaluate(
        sample({
          jpegQuality: 0.8,
          estimate: {
            ...sample().estimate,
            pressure: "overloaded",
            sendFailureDelta: 1,
          },
        }),
      ).direction,
    ).toBe("decreased");
  });
  it("increases exactly one level after healthy headroom", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { healthySamplesBeforeIncrease: 1, cooldownMs: 0 },
    );
    expect(
      controller.evaluate(sample({ currentProfile: "low" })),
    ).toMatchObject({ direction: "increased", profile: "medium" });
  });
  it("blocks adjustments in fixed mode and resets on disconnect", () => {
    const controller = new AdaptiveResolutionController();
    controller.setMode("fixed");
    expect(
      controller.evaluate(
        sample({ estimate: { ...sample().estimate, pressure: "overloaded" } }),
      ).reason,
    ).toBe("fixed_mode");
    controller.setMode("adaptive");
    expect(controller.evaluate(sample({ socketOpen: false })).reason).toBe(
      "disconnected",
    );
    expect(controller.getState().hasBaseline).toBe(false);
  });
  it("returns immutable decisions and state", () => {
    const controller = new AdaptiveResolutionController();
    expect(Object.isFrozen(controller.evaluate(sample()))).toBe(true);
    expect(Object.isFrozen(controller.getState())).toBe(true);
  });
  it("rejects invalid current profiles", () => {
    expect(() =>
      new AdaptiveResolutionController().evaluate(
        sample({ currentProfile: "missing" }),
      ),
    ).toThrow(RangeError);
  });
  it("uses a baseline for unavailable bandwidth", () => {
    expect(
      new AdaptiveResolutionController().evaluate(
        sample({
          estimate: {
            ...sample().estimate,
            confidence: "unavailable",
            pressure: "unknown",
            smoothedBitrateBps: null,
          },
        }),
      ),
    ).toMatchObject({ direction: "reset", reason: "insufficient_data" });
  });
  it("does not skip profiles when decreasing", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { overloadSamplesBeforeDecrease: 1, cooldownMs: 0 },
    );
    const overload = sample({
      estimate: { ...sample().estimate, pressure: "overloaded" },
    });
    expect(controller.evaluate(overload).profile).toBe("medium");
    expect(
      controller.evaluate(sample({ ...overload, currentProfile: "medium" })),
    ).toMatchObject({ profile: "low", direction: "decreased" });
  });
  it("produces one decrease for multiple pressure signals", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { overloadSamplesBeforeDecrease: 1, cooldownMs: 0 },
    );
    const decision = controller.evaluate(
      sample({
        estimate: {
          ...sample().estimate,
          pressure: "overloaded",
          sendFailureDelta: 1,
          backpressureDropDelta: 1,
        },
      }),
    );
    expect(decision).toMatchObject({
      direction: "decreased",
      profile: "medium",
      overloadSamples: 0,
    });
  });
  it("blocks repeated decreases during cooldown", () => {
    let now = 0;
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { overloadSamplesBeforeDecrease: 1, cooldownMs: 100 },
      () => now,
    );
    const overload = sample({
      estimate: { ...sample().estimate, pressure: "overloaded" },
    });
    controller.evaluate(overload);
    now = 1;
    expect(
      controller.evaluate(sample({ ...overload, currentProfile: "medium" }))
        .reason,
    ).toBe("cooldown");
  });
  it("clears healthy accumulation on overload", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { healthySamplesBeforeIncrease: 3, overloadSamplesBeforeDecrease: 2 },
    );
    controller.evaluate(sample({ currentProfile: "medium" }));
    controller.evaluate(
      sample({
        currentProfile: "medium",
        estimate: { ...sample().estimate, pressure: "overloaded" },
      }),
    );
    expect(controller.getState().healthySamples).toBe(0);
  });
  it("does not increase before the healthy window", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { healthySamplesBeforeIncrease: 2, cooldownMs: 0 },
    );
    expect(
      controller.evaluate(sample({ currentProfile: "low" })).direction,
    ).toBe("unchanged");
  });
  it("never skips or exceeds the maximum profile when increasing", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { healthySamplesBeforeIncrease: 1, cooldownMs: 0 },
    );
    expect(
      controller.evaluate(sample({ currentProfile: "medium" })).profile,
    ).toBe("high");
    expect(
      controller.evaluate(sample({ currentProfile: "high" })),
    ).toMatchObject({ profile: "high", direction: "unchanged" });
  });
  it("requires bandwidth headroom for a healthy increase", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { healthySamplesBeforeIncrease: 1, cooldownMs: 0 },
    );
    expect(
      controller.evaluate(
        sample({
          currentProfile: "low",
          estimate: { ...sample().estimate, smoothedBitrateBps: 1 },
        }),
      ).reason,
    ).toBe("insufficient_bandwidth");
  });
  it("interrupts healthy accumulation on a missing sample", () => {
    const controller = new AdaptiveResolutionController(
      DEFAULT_RESOLUTION_PROFILES,
      { healthySamplesBeforeIncrease: 3 },
    );
    controller.evaluate(sample({ currentProfile: "low" }));
    controller.evaluate();
    expect(controller.getState().healthySamples).toBe(1);
  });
  it("mode changes preserve profile input while requiring a new baseline", () => {
    const controller = new AdaptiveResolutionController();
    controller.evaluate(sample({ currentProfile: "medium" }));
    controller.setMode("fixed");
    expect(controller.getState()).toMatchObject({
      mode: "fixed",
      hasBaseline: false,
    });
    controller.setMode("adaptive");
    expect(
      controller.evaluate(
        sample({
          currentProfile: "medium",
          estimate: {
            ...sample().estimate,
            confidence: "unavailable",
            pressure: "unknown",
            smoothedBitrateBps: null,
          },
        }),
      ).direction,
    ).toBe("reset");
  });
  it("resets history on explicit stream-stop and counter-reset lifecycle samples", () => {
    const controller = new AdaptiveResolutionController();
    controller.evaluate(sample());
    expect(controller.evaluate(sample({ streaming: false })).reason).toBe(
      "stream_stopped",
    );
    expect(controller.getState().hasBaseline).toBe(false);
    expect(
      controller.evaluate(
        sample({
          estimate: {
            ...sample().estimate,
            confidence: "unavailable",
            pressure: "unknown",
            smoothedBitrateBps: null,
          },
        }),
      ).direction,
    ).toBe("reset");
  });
});
