import { describe, expect, it } from "vitest";

import {
  FrontendProtocolError,
  FrontendProtocolErrorCode,
  parseServerMessage,
  validateServerMessage,
} from "../src/protocol";

const result = (annotation?: unknown) => ({
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
});

describe("protocol validation", () => {
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
