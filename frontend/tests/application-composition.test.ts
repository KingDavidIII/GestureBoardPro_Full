import { describe, expect, it } from "vitest";

import { createRecognitionEventComposition } from "../src/application/application-composition";
import type { GestureRecognition } from "../src/protocol/messages";
import { RecognitionStateStore } from "../src/recognition";
import { FrameStreamState } from "../src/streaming";
import { WebSocketClientState } from "../src/websocket";

const recognition: GestureRecognition = {
  schema_version: 1,
  frame_sequence: 1,
  hand_count: 0,
  primary_hand: null,
  candidate: null,
  stable: null,
  transition: null,
};

const gestureResult = (
  value: GestureRecognition | undefined = recognition,
) => ({
  protocol_version: 1 as const,
  type: "gesture.result" as const,
  sequence: 1,
  timestamp: 0,
  detected_hand_count: 0,
  selection: { decision: "NO_HANDS" as const, identity: null },
  hand: null,
  gesture: { label: null, engine_decision: "NO_HAND" as const },
  action_executed: false,
  dispatch: null,
  ...(value === undefined ? {} : { recognition: value }),
});

describe("recognition application composition", () => {
  it("routes validated capability and result messages through one epoch", () => {
    const store = new RecognitionStateStore();
    const composition = createRecognitionEventComposition(store);
    composition.handleSocketEvent({
      type: "protocol.message",
      message: {
        protocol_version: 1,
        type: "connection.ready",
        capabilities: ["gesture.recognition.v1"],
      },
    });
    composition.handleSocketEvent({
      type: "protocol.message",
      message: {
        protocol_version: 1,
        type: "gesture.result",
        sequence: 1,
        timestamp: 0,
        detected_hand_count: 0,
        selection: { decision: "NO_HANDS", identity: null },
        hand: null,
        gesture: { label: null, engine_decision: "NO_HAND" },
        action_executed: false,
        dispatch: null,
        recognition,
      },
    });
    expect(store.getSnapshot()).toMatchObject({
      capabilityAvailable: true,
      recognition,
    });
  });
  it("clears on stream stop and begins a fresh epoch after socket closure", () => {
    const store = new RecognitionStateStore();
    const composition = createRecognitionEventComposition(store);
    composition.handleSocketEvent({
      type: "protocol.message",
      message: {
        protocol_version: 1,
        type: "gesture.result",
        sequence: 1,
        timestamp: 0,
        detected_hand_count: 0,
        selection: { decision: "NO_HANDS", identity: null },
        hand: null,
        gesture: { label: null, engine_decision: "NO_HAND" },
        action_executed: false,
        dispatch: null,
        recognition,
      },
    });
    composition.handleStreamEvent({
      type: "state.changed",
      state: FrameStreamState.IDLE,
    });
    expect(store.getSnapshot().recognition).toBeNull();
    composition.handleSocketEvent({
      type: "state.changed",
      state: WebSocketClientState.CLOSED,
    });
    expect(store.getSnapshot()).toMatchObject({
      epoch: 1,
      capabilityAvailable: false,
      recognition: null,
    });
    composition.destroy();
    composition.handleSocketEvent({
      type: "state.changed",
      state: WebSocketClientState.CLOSED,
    });
    expect(store.getSnapshot().epoch).toBe(1);
  });
  it("accepts a restarted recognition sequence after reconnecting", () => {
    const store = new RecognitionStateStore();
    const composition = createRecognitionEventComposition(store);
    composition.handleSocketEvent({
      type: "protocol.message",
      message: gestureResult({ ...recognition, frame_sequence: 5 }),
      recognitionIntegrity: { kind: "valid" },
    });
    composition.handleSocketEvent({
      type: "state.changed",
      state: WebSocketClientState.CLOSED,
    });
    composition.handleSocketEvent({
      type: "protocol.message",
      message: gestureResult({ ...recognition, frame_sequence: 1 }),
      recognitionIntegrity: { kind: "valid" },
    });
    expect(store.getSnapshot()).toMatchObject({
      epoch: 1,
      recognition: { frame_sequence: 1 },
      lastAcceptedFrameSequence: 1,
    });
  });
  it("accepts unadvertised recognition without changing capability availability", () => {
    const store = new RecognitionStateStore();
    const composition = createRecognitionEventComposition(store);
    composition.handleSocketEvent({
      type: "protocol.message",
      message: gestureResult(),
      recognitionIntegrity: { kind: "valid" },
    });
    expect(store.getSnapshot()).toMatchObject({
      capabilityAvailable: false,
      availability: "unavailable",
      integrity: { kind: "unadvertised" },
      recognition,
    });
  });
  it("preserves accepted recognition for malformed and stale protocol results", () => {
    const store = new RecognitionStateStore();
    const composition = createRecognitionEventComposition(store);
    composition.handleSocketEvent({
      type: "protocol.message",
      message: gestureResult({ ...recognition, frame_sequence: 2 }),
      recognitionIntegrity: { kind: "valid" },
    });
    composition.handleSocketEvent({
      type: "protocol.message",
      message: gestureResult(undefined),
      recognitionIntegrity: {
        kind: "malformed",
        reason: "invalid recognition",
      },
    });
    expect(store.getSnapshot()).toMatchObject({
      recognition: { frame_sequence: 2 },
      integrity: { kind: "malformed" },
    });
    composition.handleSocketEvent({
      type: "protocol.message",
      message: gestureResult({ ...recognition, frame_sequence: 1 }),
      recognitionIntegrity: { kind: "valid" },
    });
    expect(store.getSnapshot()).toMatchObject({
      recognition: { frame_sequence: 2 },
      integrity: { kind: "stale" },
      shouldAnnounce: false,
    });
  });
});
