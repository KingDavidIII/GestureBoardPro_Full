import { describe, expect, it } from "vitest";

import {
  AdaptiveQualityController,
  type AdaptiveQualityPolicy,
  type TransportQualitySample,
} from "../src/streaming";

const policy: AdaptiveQualityPolicy = {
  initialQuality: 0.8,
  minimumQuality: 0.4,
  maximumQuality: 0.9,
  decreaseStep: 0.1,
  increaseStep: 0.05,
  healthySamplesBeforeIncrease: 3,
  cooldownMs: 100,
  bufferedBytesThreshold: 100,
  payloadBytesThreshold: 200,
};
const sample = (
  values: Partial<TransportQualitySample> = {},
): TransportQualitySample => ({
  sentFrames: 1,
  backpressureDrops: 0,
  sendFailures: 0,
  encodingFailures: 0,
  latestEncodedBytes: 10,
  bufferedAmountBytes: 0,
  ...values,
});

describe("AdaptiveQualityController", () => {
  it("provides a valid immutable default policy", () => {
    const controller = new AdaptiveQualityController({ initialQuality: 0.8 });
    expect(controller.policy).toMatchObject({
      minimumQuality: 0.45,
      maximumQuality: 0.9,
      decreaseStep: 0.1,
      increaseStep: 0.05,
      healthySamplesBeforeIncrease: 10,
      cooldownMs: 2000,
      bufferedBytesThreshold: 262144,
      payloadBytesThreshold: 131072,
    });
    expect(Object.isFrozen(controller.policy)).toBe(true);
  });

  it.each([
    [{ ...policy, minimumQuality: 0 }],
    [{ ...policy, maximumQuality: 2 }],
    [{ ...policy, minimumQuality: 0.95 }],
    [{ ...policy, initialQuality: 0.2 }],
    [{ ...policy, decreaseStep: 0 }],
    [{ ...policy, increaseStep: Number.NaN }],
    [{ ...policy, healthySamplesBeforeIncrease: 1.5 }],
    [{ ...policy, cooldownMs: -1 }],
    [{ ...policy, bufferedBytesThreshold: -1 }],
    [{ ...policy, payloadBytesThreshold: Number.POSITIVE_INFINITY }],
  ])("rejects invalid policy %#", (invalid) => {
    expect(() => new AdaptiveQualityController(invalid)).toThrow(RangeError);
  });

  it("does not adjust without a sample", () => {
    expect(new AdaptiveQualityController(policy).evaluate(0.8)).toMatchObject({
      quality: 0.8,
      direction: "unchanged",
      reason: "insufficient_data",
    });
  });
  it("uses the first sample as a baseline", () => {
    expect(
      new AdaptiveQualityController(policy).evaluate(0.8, sample()),
    ).toMatchObject({ direction: "reset", quality: 0.8 });
  });
  it("decreases for a positive backpressure delta", () => {
    const controller = new AdaptiveQualityController(policy, () => 200);
    controller.evaluate(0.8, sample());
    expect(
      controller.evaluate(0.8, sample({ backpressureDrops: 1 })),
    ).toMatchObject({ direction: "decreased", reason: "backpressure_drop" });
  });
  it("does not repeat a historical backpressure total", () => {
    let now = 200;
    const controller = new AdaptiveQualityController(policy, () => now);
    controller.evaluate(0.8, sample());
    controller.evaluate(0.8, sample({ backpressureDrops: 1 }));
    now = 400;
    expect(
      controller.evaluate(0.7, sample({ sentFrames: 2, backpressureDrops: 1 }))
        .reason,
    ).toBe("healthy_window");
  });
  it("decreases for a positive send-failure delta", () => {
    const controller = new AdaptiveQualityController(policy, () => 200);
    controller.evaluate(0.8, sample());
    expect(controller.evaluate(0.8, sample({ sendFailures: 1 })).reason).toBe(
      "send_failure",
    );
  });
  it("decreases for buffered-byte overload", () => {
    const controller = new AdaptiveQualityController(policy, () => 200);
    controller.evaluate(0.8, sample());
    expect(
      controller.evaluate(0.8, sample({ bufferedAmountBytes: 101 })).reason,
    ).toBe("buffered_amount");
  });
  it("decreases for payload-size overload", () => {
    const controller = new AdaptiveQualityController(policy, () => 200);
    controller.evaluate(0.8, sample());
    expect(
      controller.evaluate(0.8, sample({ latestEncodedBytes: 201 })).reason,
    ).toBe("payload_size");
  });
  it("makes one decrease for multiple overload signals", () => {
    const controller = new AdaptiveQualityController(policy, () => 200);
    controller.evaluate(0.8, sample());
    const decision = controller.evaluate(
      0.8,
      sample({
        sendFailures: 1,
        backpressureDrops: 1,
        bufferedAmountBytes: 500,
      }),
    );
    expect(decision.quality).toBeCloseTo(0.7);
    expect(decision.overloadSamples).toBe(0);
  });
  it("uses deterministic send, drop, buffer, payload reason priority", () => {
    const controller = new AdaptiveQualityController(policy, () => 200);
    controller.evaluate(0.8, sample());
    expect(
      controller.evaluate(
        0.8,
        sample({
          sendFailures: 1,
          backpressureDrops: 1,
          bufferedAmountBytes: 500,
          latestEncodedBytes: 500,
        }),
      ).reason,
    ).toBe("send_failure");
  });
  it("uses the configured decrease step", () => {
    const controller = new AdaptiveQualityController(
      { ...policy, decreaseStep: 0.2 },
      () => 200,
    );
    controller.evaluate(0.8, sample());
    expect(
      controller.evaluate(0.8, sample({ sendFailures: 1 })).quality,
    ).toBeCloseTo(0.6);
  });
  it("clamps decreases to minimum quality", () => {
    const controller = new AdaptiveQualityController(policy, () => 200);
    controller.evaluate(0.45, sample());
    expect(controller.evaluate(0.45, sample({ sendFailures: 1 })).quality).toBe(
      0.4,
    );
  });
  it("prevents repeated immediate decreases during cooldown", () => {
    let now = 200;
    const controller = new AdaptiveQualityController(policy, () => now);
    controller.evaluate(0.8, sample());
    controller.evaluate(0.8, sample({ sendFailures: 1 }));
    now = 250;
    expect(controller.evaluate(0.7, sample({ sendFailures: 2 })).reason).toBe(
      "cooldown",
    );
  });
  it("clears healthy accumulation on overload", () => {
    const controller = new AdaptiveQualityController(policy, () => 200);
    controller.evaluate(0.8, sample());
    controller.evaluate(0.8, sample({ sentFrames: 2 }));
    expect(
      controller.evaluate(0.8, sample({ sentFrames: 3, sendFailures: 1 }))
        .healthySamples,
    ).toBe(0);
  });
  it("does not increase with insufficient healthy samples", () => {
    const controller = new AdaptiveQualityController(policy);
    controller.evaluate(0.7, sample());
    expect(controller.evaluate(0.7, sample({ sentFrames: 2 })).direction).toBe(
      "unchanged",
    );
  });
  it("increases on healthy-window completion", () => {
    const controller = new AdaptiveQualityController(policy);
    controller.evaluate(0.7, sample());
    controller.evaluate(0.7, sample({ sentFrames: 2 }));
    controller.evaluate(0.7, sample({ sentFrames: 3 }));
    expect(controller.evaluate(0.7, sample({ sentFrames: 4 })).direction).toBe(
      "increased",
    );
  });
  it("uses the configured increase step", () => {
    const controller = new AdaptiveQualityController({
      ...policy,
      healthySamplesBeforeIncrease: 1,
    });
    controller.evaluate(0.7, sample());
    expect(
      controller.evaluate(0.7, sample({ sentFrames: 2 })).quality,
    ).toBeCloseTo(0.75);
  });
  it("clamps increases to maximum quality", () => {
    const controller = new AdaptiveQualityController({
      ...policy,
      healthySamplesBeforeIncrease: 1,
    });
    controller.evaluate(0.88, sample());
    expect(controller.evaluate(0.88, sample({ sentFrames: 2 })).quality).toBe(
      0.9,
    );
  });
  it("interrupts health accumulation on missing samples", () => {
    const controller = new AdaptiveQualityController(policy);
    controller.evaluate(0.7, sample());
    controller.evaluate(0.7, sample({ sentFrames: 2 }));
    controller.evaluate(0.7);
    expect(controller.getState().healthySamples).toBe(0);
  });
  it("resets on cumulative counter regression", () => {
    const controller = new AdaptiveQualityController(policy);
    controller.evaluate(0.8, sample({ sentFrames: 5 }));
    expect(controller.evaluate(0.8, sample()).direction).toBe("reset");
  });
  it("handles zero payload and buffered values without Infinity", () => {
    const controller = new AdaptiveQualityController(policy);
    const decision = controller.evaluate(
      0.8,
      sample({ latestEncodedBytes: 0, bufferedAmountBytes: 0 }),
    );
    expect(JSON.stringify(decision)).not.toContain("Infinity");
  });
  it("clears history on disconnect reset", () => {
    const controller = new AdaptiveQualityController(policy);
    controller.evaluate(0.8, sample());
    controller.reset();
    expect(controller.getState().hasBaseline).toBe(false);
  });
  it("clears history on stream-stop reset", () => {
    const controller = new AdaptiveQualityController(policy);
    controller.evaluate(0.8, sample());
    controller.reset();
    expect(controller.evaluate(0.8, sample()).direction).toBe("reset");
  });
  it("does not adjust in fixed mode", () => {
    const controller = new AdaptiveQualityController({
      ...policy,
      enabled: false,
    });
    expect(
      controller.evaluate(0.8, sample({ bufferedAmountBytes: 500 })).reason,
    ).toBe("disabled");
  });
  it("requires a fresh baseline after re-enabling adaptive mode", () => {
    const controller = new AdaptiveQualityController(policy);
    controller.evaluate(0.8, sample());
    controller.setMode("fixed");
    controller.setMode("adaptive");
    expect(controller.evaluate(0.8, sample()).direction).toBe("reset");
  });
  it("returns immutable decisions", () => {
    expect(
      Object.isFrozen(new AdaptiveQualityController(policy).evaluate(0.8)),
    ).toBe(true);
  });
  it("returns immutable controller state", () => {
    expect(
      Object.isFrozen(new AdaptiveQualityController(policy).getState()),
    ).toBe(true);
  });
});
