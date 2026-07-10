import { describe, expect, it } from "vitest";

import {
  FrontendProtocolError,
  FrontendProtocolErrorCode,
  parseServerMessage,
  validateServerMessage,
} from "../src/protocol";

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
});
