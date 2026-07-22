import { describe, expect, it, vi } from "vitest";

import { AnnotationCorrelation } from "../src/dashboard";
import type { AnnotatedFrameMessage } from "../src/protocol";
import type { GestureResultMessage } from "../src/protocol/messages";

const frame = (sequence: number): AnnotatedFrameMessage =>
  Object.freeze({
    sequence,
    width: 640,
    height: 480,
    size: 3,
    mimeType: "image/jpeg",
    blob: new Blob([new Uint8Array([1, 2, 3])], { type: "image/jpeg" }),
  });

const result = (
  sequence: number,
  annotation: GestureResultMessage["annotation"],
): GestureResultMessage =>
  ({
    protocol_version: 1,
    type: "gesture.result",
    sequence,
    timestamp: 0,
    detected_hand_count: 0,
    selection: { decision: "NO_HANDS", identity: null },
    hand: null,
    gesture: { label: null, engine_decision: "NO_HAND" },
    action_executed: false,
    dispatch: null,
    annotation,
  }) as GestureResultMessage;

const available = (sequence: number): GestureResultMessage["annotation"] => ({
  enabled: true,
  available: true,
  format: "jpeg",
  envelope_version: 1,
  sequence,
  width: 640,
  height: 480,
  byte_length: 3,
});

describe("AnnotationCorrelation", () => {
  it("correlates metadata before its matching frame", () => {
    const correlation = new AnnotationCorrelation();
    expect(correlation.acceptResult(result(4, available(4)))).toEqual({
      kind: "none",
    });
    expect(correlation.acceptFrame(frame(4))).toMatchObject({
      kind: "frame",
      frame: { sequence: 4 },
    });
  });

  it("correlates a frame received before its metadata", () => {
    const correlation = new AnnotationCorrelation();
    expect(correlation.acceptFrame(frame(4))).toEqual({ kind: "none" });
    expect(correlation.acceptResult(result(4, available(4)))).toMatchObject({
      kind: "frame",
      frame: { sequence: 4 },
    });
  });

  it("does not correlate mismatched sequences and retains only the newest pending frame", () => {
    const correlation = new AnnotationCorrelation();
    correlation.acceptResult(result(4, available(4)));
    expect(correlation.acceptFrame(frame(5))).toEqual({ kind: "none" });
    expect(correlation.acceptFrame(frame(4))).toMatchObject({
      kind: "frame",
      frame: { sequence: 4 },
    });
  });

  it("never replaces a newer correlated frame with an older or duplicate sequence", () => {
    const correlation = new AnnotationCorrelation();
    correlation.acceptResult(result(5, available(5)));
    expect(correlation.acceptFrame(frame(5))).toMatchObject({ kind: "frame" });
    expect(correlation.acceptResult(result(4, available(4)))).toEqual({
      kind: "none",
    });
    expect(correlation.acceptFrame(frame(4))).toEqual({ kind: "none" });
    expect(correlation.acceptFrame(frame(5))).toEqual({ kind: "none" });
  });

  it("clears a correlated frame when annotation becomes unavailable or disabled", () => {
    const correlation = new AnnotationCorrelation();
    correlation.acceptResult(result(5, available(5)));
    correlation.acceptFrame(frame(5));
    expect(
      correlation.acceptResult(result(6, { enabled: true, available: false })),
    ).toEqual({ kind: "clear" });
    expect(
      correlation.acceptResult(result(7, { enabled: false, available: false })),
    ).toEqual({ kind: "none" });
  });

  it("preserves the same-epoch watermark after disabled clearing", () => {
    const correlation = new AnnotationCorrelation();
    correlation.acceptResult(result(10, available(10)));
    expect(correlation.acceptFrame(frame(10))).toMatchObject({ kind: "frame" });
    expect(
      correlation.acceptResult(
        result(11, { enabled: false, available: false }),
      ),
    ).toEqual({ kind: "clear" });
    expect(correlation.acceptResult(result(9, available(9)))).toEqual({
      kind: "none",
    });
    expect(correlation.acceptFrame(frame(9))).toEqual({ kind: "none" });
    correlation.acceptResult(result(11, available(11)));
    expect(correlation.acceptFrame(frame(11))).toMatchObject({ kind: "frame" });
  });

  it("preserves the same-epoch watermark after a disabled acknowledgement clears presentation", () => {
    const correlation = new AnnotationCorrelation();
    correlation.acceptResult(result(10, available(10)));
    correlation.acceptFrame(frame(10));

    expect(correlation.clearPresentation()).toEqual({ kind: "clear" });
    expect(correlation.acceptResult(result(9, available(9)))).toEqual({
      kind: "none",
    });
    expect(correlation.acceptFrame(frame(9))).toEqual({ kind: "none" });

    correlation.acceptResult(result(11, available(11)));
    expect(correlation.acceptFrame(frame(11))).toMatchObject({ kind: "frame" });
  });

  it("permits a restarted sequence only after an epoch reset", () => {
    const correlation = new AnnotationCorrelation();
    correlation.acceptResult(result(10, available(10)));
    correlation.acceptFrame(frame(10));
    correlation.reset();
    correlation.acceptResult(result(1, available(1)));
    expect(correlation.acceptFrame(frame(1))).toMatchObject({ kind: "frame" });
  });

  it.each([
    [{ ...available(4), byte_length: 4 }],
    [{ ...available(4), width: 641 }],
    [{ ...available(4), height: 481 }],
    [{ ...available(4), format: "png" as const }],
  ])(
    "rejects a same-sequence pair with inconsistent metadata %#",
    (annotation) => {
      const correlation = new AnnotationCorrelation();
      correlation.acceptResult(
        result(4, annotation as GestureResultMessage["annotation"]),
      );
      expect(correlation.acceptFrame(frame(4))).toEqual({ kind: "none" });
    },
  );

  it("clears pending and presented state at epoch boundaries", () => {
    const correlation = new AnnotationCorrelation();
    correlation.acceptFrame(frame(8));
    expect(correlation.reset()).toEqual({ kind: "none" });
    expect(correlation.acceptResult(result(8, available(8)))).toEqual({
      kind: "none",
    });
    correlation.acceptResult(result(9, available(9)));
    correlation.acceptFrame(frame(9));
    expect(correlation.reset()).toEqual({ kind: "clear" });
  });

  it("isolates subscriber failures without interrupting later listeners", () => {
    const firstFailure = new Error("first annotation listener failed");
    const secondFailure = new Error("second annotation listener failed");
    const subscriberErrorHandler = vi.fn();
    const healthyListener = vi.fn();
    const correlation = new AnnotationCorrelation({ subscriberErrorHandler });

    correlation.subscribe(() => {
      throw firstFailure;
    });
    correlation.subscribe(() => {
      throw secondFailure;
    });
    correlation.subscribe(healthyListener);

    correlation.acceptResult(result(4, available(4)));
    const update = correlation.acceptFrame(frame(4));

    expect(subscriberErrorHandler.mock.calls).toEqual([
      [firstFailure],
      [secondFailure],
    ]);
    expect(healthyListener).toHaveBeenCalledOnce();
    expect(healthyListener).toHaveBeenCalledWith(update);
  });

  it("makes destruction terminal and rejects late subscriptions", () => {
    const listener = vi.fn();
    const lateListener = vi.fn();
    const correlation = new AnnotationCorrelation();
    correlation.subscribe(listener);
    correlation.acceptResult(result(4, available(4)));
    correlation.acceptFrame(frame(4));
    expect(listener).toHaveBeenCalledOnce();

    correlation.destroy();
    correlation.destroy();
    const unsubscribe = correlation.subscribe(lateListener);

    expect(correlation.acceptResult(result(5, available(5)))).toEqual({
      kind: "none",
    });
    expect(correlation.acceptFrame(frame(5))).toEqual({ kind: "none" });
    expect(correlation.reset()).toEqual({ kind: "none" });
    expect(correlation.clearPresentation()).toEqual({ kind: "none" });
    expect(() => unsubscribe()).not.toThrow();
    expect(listener).toHaveBeenCalledOnce();
    expect(lateListener).not.toHaveBeenCalled();
  });
});
