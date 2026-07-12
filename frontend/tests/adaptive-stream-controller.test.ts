import { describe, expect, it } from "vitest";

import type { SchedulerMetadata } from "../src/protocol";
import {
  AdaptiveStreamController,
  type AdaptiveStreamPolicy,
} from "../src/streaming";

const policy: AdaptiveStreamPolicy = {
  minimumFps: 4,
  maximumFps: 20,
  decreaseFactor: 0.5,
  increaseFps: 2,
  healthySamplesBeforeIncrease: 3,
  cooldownMs: 100,
  queueDelayThresholdMs: 50,
  utilisationThreshold: 0.9,
};

const sample = (
  overrides: Partial<SchedulerMetadata> = {},
): SchedulerMetadata => ({
  received_frames: 1,
  processed_frames: 1,
  dropped_frames: 0,
  processing_failures: 0,
  pending_frames: 0,
  queue_delay_ms: 0,
  processing_time_ms: 20,
  ...overrides,
});

describe("AdaptiveStreamController", () => {
  it("provides a valid immutable default policy", () => {
    const controller = new AdaptiveStreamController({ maximumFps: 20 });
    expect(controller.policy.minimumFps).toBeGreaterThan(0);
    expect(Object.isFrozen(controller.policy)).toBe(true);
    expect(() => Object.assign(controller.policy, { minimumFps: 0 })).toThrow();
  });

  it.each([
    [{ ...policy, minimumFps: 0 }],
    [{ ...policy, maximumFps: Number.NaN }],
    [{ ...policy, minimumFps: 21 }],
    [{ ...policy, decreaseFactor: 1 }],
    [{ ...policy, increaseFps: Number.POSITIVE_INFINITY }],
    [{ ...policy, healthySamplesBeforeIncrease: 1.5 }],
    [{ ...policy, cooldownMs: -1 }],
  ])("rejects invalid policy %#", (invalidPolicy) => {
    expect(() => new AdaptiveStreamController(invalidPolicy)).toThrow(
      RangeError,
    );
  });

  it("makes no adjustment without a scheduler sample", () => {
    expect(new AdaptiveStreamController(policy).evaluate(10)).toMatchObject({
      targetFps: 10,
      direction: "unchanged",
      reason: "insufficient_data",
    });
  });

  it("uses the first sample only as a baseline", () => {
    const decision = new AdaptiveStreamController(policy).evaluate(
      10,
      sample(),
    );
    expect(decision.direction).toBe("reset");
    expect(decision.targetFps).toBe(10);
  });

  it("decreases once for a positive dropped-frame delta", () => {
    const controller = new AdaptiveStreamController(policy, () => 200);
    controller.evaluate(10, sample());
    expect(
      controller.evaluate(
        10,
        sample({ received_frames: 2, dropped_frames: 1 }),
      ),
    ).toMatchObject({ direction: "decreased", reason: "server_drop" });
  });

  it("does not decrease again for a historical dropped count", () => {
    let now = 200;
    const controller = new AdaptiveStreamController(policy, () => now);
    controller.evaluate(10, sample());
    controller.evaluate(10, sample({ received_frames: 2, dropped_frames: 1 }));
    now = 400;
    expect(
      controller.evaluate(5, sample({ received_frames: 3, dropped_frames: 1 }))
        .reason,
    ).toBe("healthy_window");
  });

  it("treats one pending frame as overload", () => {
    const controller = new AdaptiveStreamController(policy, () => 200);
    controller.evaluate(10, sample());
    expect(
      controller.evaluate(10, sample({ received_frames: 2, pending_frames: 1 }))
        .reason,
    ).toBe("pending_frame");
  });

  it("treats queue delay over the threshold as overload", () => {
    const controller = new AdaptiveStreamController(policy, () => 200);
    controller.evaluate(10, sample());
    expect(
      controller.evaluate(
        10,
        sample({ received_frames: 2, queue_delay_ms: 51 }),
      ).reason,
    ).toBe("queue_delay");
  });

  it("detects insufficient processing capacity", () => {
    const controller = new AdaptiveStreamController(policy, () => 200);
    controller.evaluate(20, sample());
    expect(
      controller.evaluate(
        20,
        sample({ received_frames: 2, processing_time_ms: 100 }),
      ).reason,
    ).toBe("processing_capacity");
  });

  it("applies only one decrease for multiple overload signals", () => {
    const controller = new AdaptiveStreamController(policy, () => 200);
    controller.evaluate(16, sample());
    const decision = controller.evaluate(
      16,
      sample({
        received_frames: 2,
        dropped_frames: 1,
        pending_frames: 1,
        queue_delay_ms: 100,
      }),
    );
    expect(decision.targetFps).toBe(8);
    expect(decision.overloadSamples).toBe(1);
  });

  it("uses the configured multiplicative decrease factor", () => {
    const controller = new AdaptiveStreamController(policy, () => 200);
    controller.evaluate(18, sample());
    expect(
      controller.evaluate(18, sample({ received_frames: 2, dropped_frames: 1 }))
        .targetFps,
    ).toBe(9);
  });

  it("clamps decreases to the minimum FPS", () => {
    const controller = new AdaptiveStreamController(policy, () => 200);
    controller.evaluate(5, sample());
    expect(
      controller.evaluate(5, sample({ received_frames: 2, dropped_frames: 1 }))
        .targetFps,
    ).toBe(4);
  });

  it("blocks an immediate repeated decrease during cooldown", () => {
    let now = 200;
    const controller = new AdaptiveStreamController(policy, () => now);
    controller.evaluate(16, sample());
    controller.evaluate(16, sample({ received_frames: 2, dropped_frames: 1 }));
    now = 250;
    expect(
      controller.evaluate(8, sample({ received_frames: 3, dropped_frames: 2 })),
    ).toMatchObject({ targetFps: 8, reason: "cooldown" });
  });

  it("resets healthy accumulation on overload", () => {
    const controller = new AdaptiveStreamController(policy, () => 200);
    controller.evaluate(10, sample());
    controller.evaluate(10, sample({ received_frames: 2 }));
    expect(
      controller.evaluate(10, sample({ received_frames: 3, dropped_frames: 1 }))
        .healthySamples,
    ).toBe(0);
  });

  it("does not increase before enough healthy samples", () => {
    const controller = new AdaptiveStreamController(policy);
    controller.evaluate(10, sample());
    expect(
      controller.evaluate(10, sample({ received_frames: 2 })).direction,
    ).toBe("unchanged");
  });

  it("increases after the configured healthy window", () => {
    const controller = new AdaptiveStreamController(policy);
    controller.evaluate(10, sample());
    controller.evaluate(10, sample({ received_frames: 2 }));
    controller.evaluate(10, sample({ received_frames: 3 }));
    expect(
      controller.evaluate(10, sample({ received_frames: 4 })),
    ).toMatchObject({ targetFps: 12, direction: "increased" });
  });

  it("clamps increases to the maximum FPS", () => {
    const controller = new AdaptiveStreamController({
      ...policy,
      healthySamplesBeforeIncrease: 1,
    });
    controller.evaluate(19, sample());
    expect(
      controller.evaluate(19, sample({ received_frames: 2 })).targetFps,
    ).toBe(20);
  });

  it("interrupts healthy accumulation when a sample is missing", () => {
    const controller = new AdaptiveStreamController(policy);
    controller.evaluate(10, sample());
    controller.evaluate(10, sample({ received_frames: 2 }));
    controller.evaluate(10);
    expect(controller.getState().healthySamples).toBe(0);
  });

  it("does not produce infinite capacity when processing time is zero", () => {
    const controller = new AdaptiveStreamController(policy);
    controller.evaluate(10, sample());
    expect(
      controller.evaluate(
        10,
        sample({ received_frames: 2, processing_time_ms: 0 }),
      ).estimatedCapacityFps,
    ).toBeNull();
  });

  it("resets sample history after counter regression", () => {
    const controller = new AdaptiveStreamController(policy);
    controller.evaluate(10, sample({ received_frames: 5, dropped_frames: 2 }));
    expect(controller.evaluate(10, sample()).direction).toBe("reset");
  });

  it("clears history on disconnect reset", () => {
    const controller = new AdaptiveStreamController(policy);
    controller.evaluate(10, sample());
    controller.reset();
    expect(controller.getState().hasBaseline).toBe(false);
  });

  it("clears history on stream-stop reset", () => {
    const controller = new AdaptiveStreamController(policy);
    controller.evaluate(10, sample());
    controller.reset();
    expect(controller.evaluate(10, sample()).direction).toBe("reset");
  });

  it("does not adjust automatically in fixed mode", () => {
    const controller = new AdaptiveStreamController({
      ...policy,
      enabled: false,
    });
    expect(
      controller.evaluate(10, sample({ pending_frames: 1 })),
    ).toMatchObject({ targetFps: 10, reason: "disabled" });
  });

  it("starts with fresh history after switching back to adaptive", () => {
    const controller = new AdaptiveStreamController(policy);
    controller.evaluate(10, sample());
    controller.setMode("fixed");
    controller.setMode("adaptive");
    expect(controller.evaluate(10, sample()).direction).toBe("reset");
  });

  it("returns immutable decisions", () => {
    const decision = new AdaptiveStreamController(policy).evaluate(10);
    expect(Object.isFrozen(decision)).toBe(true);
    expect(() => Object.assign(decision, { targetFps: 1 })).toThrow();
  });

  it("returns immutable exposed controller state", () => {
    const state = new AdaptiveStreamController(policy).getState();
    expect(Object.isFrozen(state)).toBe(true);
    expect(() => Object.assign(state, { healthySamples: 99 })).toThrow();
  });
});
