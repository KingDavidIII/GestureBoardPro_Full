import { describe, expect, it } from "vitest";

import errorFixtures from "../../contracts/gesture-protocol/v1/fixtures/server-error-messages.json";

import {
  FrontendProtocolError,
  FrontendProtocolErrorCode,
  parseServerMessage,
  validateServerMessage,
} from "../src/protocol";

const result = (annotation?: unknown, scheduler?: unknown) => ({
  protocol_version: 1,
  type: "gesture.result",
  sequence: 4,
  timestamp: 1.5,
  detected_hand_count: 1,
  selection: {
    decision: "FIRST_DETECTED",
    identity: { hand_index: 0, handedness: "Right" },
  },
  hand: { index: 0, handedness: "Right", detection_confidence: 0.9 },
  gesture: { label: "OPEN_PALM", engine_decision: "ACTIVATED" },
  action_executed: true,
  dispatch: {
    gesture_label: "OPEN_PALM",
    action_kind: "TAP_KEY",
    executed: true,
  },
  ...(annotation === undefined ? {} : { annotation }),
  ...(scheduler === undefined ? {} : { scheduler }),
});

const scheduler = {
  received_frames: 8,
  processed_frames: 5,
  dropped_frames: 2,
  processing_failures: 1,
  pending_frames: 1,
  queue_delay_ms: 12.25,
  processing_time_ms: 44.5,
};

describe("protocol validation", () => {
  it("accepts every shared v1 backend error fixture without changing safe fields", () => {
    for (const fixture of errorFixtures) {
      expect(validateServerMessage(fixture)).toEqual(fixture);
    }
  });

  it("rejects an unknown server error code", () => {
    expect(() =>
      validateServerMessage({
        protocol_version: 1,
        type: "error",
        error: { code: "future_error", message: "Unknown." },
      }),
    ).toThrow(FrontendProtocolError);
  });
  it("accepts protocol version 1 server messages", () => {
    expect(
      parseServerMessage('{"protocol_version":1,"type":"connection.ready"}'),
    ).toEqual({
      protocol_version: 1,
      type: "connection.ready",
    });
  });

  it("validates a complete gesture result", () => {
    const message = validateServerMessage({
      protocol_version: 1,
      type: "gesture.result",
      sequence: 4,
      timestamp: 1.5,
      detected_hand_count: 1,
      selection: {
        decision: "FIRST_DETECTED",
        identity: { hand_index: 0, handedness: "Right" },
      },
      hand: { index: 0, handedness: "Right", detection_confidence: 0.9 },
      gesture: { label: "OPEN_PALM", engine_decision: "ACTIVATED" },
      action_executed: true,
      dispatch: {
        gesture_label: "OPEN_PALM",
        action_kind: "TAP_KEY",
        executed: true,
      },
    });

    expect(message.type).toBe("gesture.result");
  });

  it("accepts valid scheduler metadata and legacy results without it", () => {
    expect(validateServerMessage(result()).type).toBe("gesture.result");
    expect(validateServerMessage(result(undefined, scheduler))).toMatchObject({
      scheduler,
    });
  });

  it.each([
    null,
    [],
    "scheduler",
    {},
    { ...scheduler, received_frames: -1 },
    { ...scheduler, processed_frames: 1.5 },
    { ...scheduler, dropped_frames: Number.MAX_SAFE_INTEGER + 1 },
    { ...scheduler, processing_failures: Number.NaN },
    { ...scheduler, pending_frames: -1 },
    { ...scheduler, pending_frames: 2 },
    { ...scheduler, queue_delay_ms: -1 },
    { ...scheduler, queue_delay_ms: Number.POSITIVE_INFINITY },
    { ...scheduler, processing_time_ms: Number.NaN },
    { ...scheduler, processing_time_ms: -0.1 },
  ])("rejects invalid scheduler metadata %#", (metadata) => {
    expect(() => validateServerMessage(result(undefined, metadata))).toThrow(
      FrontendProtocolError,
    );
  });

  it("rejects invalid payloads and protocol versions", () => {
    expect(() => parseServerMessage("not json")).toThrow(FrontendProtocolError);
    expect(() =>
      validateServerMessage({ protocol_version: 2, type: "pong" }),
    ).toThrow(
      expect.objectContaining({
        code: FrontendProtocolErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
      }),
    );
    expect(() =>
      validateServerMessage({ protocol_version: 1, type: "gesture.result" }),
    ).toThrow(FrontendProtocolError);
  });

  it.each([
    [{ enabled: false, available: false }],
    [{ enabled: true, available: false, error: "ANNOTATION_ENCODING_FAILED" }],
    [
      {
        enabled: true,
        available: true,
        format: "jpeg",
        envelope_version: 1,
        sequence: 42,
        width: 640,
        height: 480,
        byte_length: 12345,
      },
    ],
  ])("accepts valid optional annotation %#", (annotation) => {
    expect(validateServerMessage(result(annotation)).type).toBe(
      "gesture.result",
    );
  });

  it.each([
    null,
    [],
    "annotation",
    1,
    true,
    {},
    { enabled: false },
    { available: false },
    { enabled: false, available: true },
    {
      enabled: true,
      available: true,
      format: "png",
      envelope_version: 1,
      sequence: 1,
      width: 1,
      height: 1,
      byte_length: 1,
    },
    {
      enabled: true,
      available: true,
      format: "jpeg",
      envelope_version: 1,
      sequence: -1,
      width: 1,
      height: 1,
      byte_length: 1,
    },
    {
      enabled: true,
      available: true,
      format: "jpeg",
      envelope_version: 1,
      sequence: 1,
      width: 0,
      height: 1,
      byte_length: 1,
    },
    { enabled: true, available: false, error: "" },
  ])("rejects invalid annotation %#", (annotation) => {
    expect(() => validateServerMessage(result(annotation))).toThrow(
      FrontendProtocolError,
    );
  });

  it("validates capabilities and annotation acknowledgement", () => {
    expect(
      validateServerMessage({
        protocol_version: 1,
        type: "connection.ready",
        capabilities: ["annotated_frame.jpeg.v1", "other"],
      }),
    ).toMatchObject({ type: "connection.ready" });
    expect(
      validateServerMessage({
        protocol_version: 1,
        type: "annotated_frame.set.ack",
        enabled: true,
        request_id: "annotation-request",
      }),
    ).toMatchObject({ enabled: true });
    expect(() =>
      validateServerMessage({
        protocol_version: 1,
        type: "connection.ready",
        capabilities: [null],
      }),
    ).toThrow(FrontendProtocolError);
    expect(() =>
      validateServerMessage({
        protocol_version: 1,
        type: "annotated_frame.set.ack",
        enabled: 1,
      }),
    ).toThrow(FrontendProtocolError);
  });
});
