import { describe, expect, it, vi } from "vitest";

import { RecognitionStateStore } from "../src/recognition";
import type { GestureRecognition } from "../src/protocol/messages";

const recognition = (eventId: number | null = null): GestureRecognition => ({
  schema_version: 1,
  frame_sequence: 4,
  hand_count: 1,
  primary_hand: { handedness: "right", confidence: 0.9 },
  candidate: {
    gesture_id: "open_palm",
    confidence: 0.8,
    reason: "extended_fingers",
  },
  stable: null,
  transition:
    eventId === null
      ? null
      : {
          event_id: eventId,
          kind: "activated",
          previous_gesture: null,
          gesture: "open_palm",
          confidence: 0.8,
        },
});

describe("RecognitionStateStore", () => {
  it("starts immutable and unavailable", () => {
    const store = new RecognitionStateStore();
    expect(store.getSnapshot()).toMatchObject({
      availability: "unavailable",
      epoch: 0,
    });
    expect(Object.isFrozen(store.getSnapshot())).toBe(true);
  });
  it("copies recognition, announces transitions once, and clears", () => {
    const store = new RecognitionStateStore();
    store.beginEpoch(1);
    const value = recognition(1);
    store.applyRecognition(value, 1);
    (value.primary_hand as { confidence: number }).confidence = 0; // caller mutation
    expect(store.getSnapshot().recognition?.primary_hand?.confidence).toBe(0.9);
    expect(store.getSnapshot().shouldAnnounce).toBe(true);
    store.applyRecognition(recognition(1), 1);
    expect(store.getSnapshot().shouldAnnounce).toBe(false);
    store.clear(1);
    expect(store.getSnapshot().recognition).toBeNull();
  });
  it("rejects stale epochs and resets announcement history", () => {
    const store = new RecognitionStateStore();
    store.beginEpoch(2);
    store.applyRecognition(recognition(1), 2);
    store.applyRecognition(recognition(2), 1);
    expect(store.getSnapshot().epoch).toBe(2);
    store.beginEpoch(3);
    store.applyRecognition(recognition(1), 3);
    expect(store.getSnapshot().shouldAnnounce).toBe(true);
  });
  it("scopes capability and subscriptions to the active epoch", () => {
    const store = new RecognitionStateStore();
    const received: number[] = [];
    const stop = store.subscribe((snapshot) => received.push(snapshot.epoch));
    store.beginEpoch(4);
    store.setCapabilityAvailable(true, 4);
    expect(store.getSnapshot().capabilityAvailable).toBe(true);
    store.setCapabilityAvailable(false, 3);
    expect(store.getSnapshot().capabilityAvailable).toBe(true);
    stop();
    store.clear(4);
    expect(received).toEqual([4, 4]);
  });
  it("destroys listeners without generating a transition", () => {
    const store = new RecognitionStateStore();
    const listener = vi.fn();
    store.subscribe(listener);
    store.beginEpoch(1);
    store.destroy();
    store.applyRecognition(recognition(), 1);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().recognition).toBeNull();
  });
  it("keeps no-hand, stable, and transition payloads immutable", () => {
    const store = new RecognitionStateStore();
    store.beginEpoch(1);
    const noHand = {
      ...recognition(),
      hand_count: 0,
      primary_hand: null,
      candidate: null,
    };
    store.applyRecognition(noHand, 1);
    expect(store.getSnapshot().recognition?.hand_count).toBe(0);
    const stable = {
      ...recognition(2),
      frame_sequence: 5,
      stable: {
        gesture_id: "open_palm" as const,
        confidence: 0.7,
        confirmed_frames: 2,
        since_ms: 0,
      },
    };
    store.applyRecognition(stable, 1);
    (stable.stable as { confidence: number }).confidence = 0;
    expect(store.getSnapshot().recognition?.stable?.confidence).toBe(0.7);
    expect(Object.isFrozen(store.getSnapshot().recognition?.transition)).toBe(
      true,
    );
  });
  it("treats absent recognition as a clear without inventing a transition", () => {
    const store = new RecognitionStateStore();
    store.beginEpoch(1);
    store.applyRecognition({ ...recognition(1), frame_sequence: 5 }, 1);
    store.applyRecognition(undefined, 1);
    expect(store.getSnapshot()).toMatchObject({
      recognition: null,
      shouldAnnounce: false,
      lastAcceptedFrameSequence: 5,
    });
    store.applyRecognition({ ...recognition(2), frame_sequence: 4 }, 1, {
      kind: "valid",
    });
    expect(store.getSnapshot()).toMatchObject({
      recognition: null,
      integrity: { kind: "stale" },
      lastAcceptedFrameSequence: 5,
      shouldAnnounce: false,
    });
  });
  it("retains the sequence watermark when explicit null recognition clears state", () => {
    const store = new RecognitionStateStore();
    store.beginEpoch(1);
    store.applyRecognition({ ...recognition(1), frame_sequence: 5 }, 1, {
      kind: "valid",
    });
    store.applyRecognition(null, 1, { kind: "valid" });
    store.applyRecognition({ ...recognition(2), frame_sequence: 4 }, 1, {
      kind: "valid",
    });
    expect(store.getSnapshot()).toMatchObject({
      recognition: null,
      integrity: { kind: "stale" },
      lastAcceptedFrameSequence: 5,
      shouldAnnounce: false,
    });
  });
  it("accepts only increasing frame sequences within an epoch", () => {
    const store = new RecognitionStateStore();
    store.beginEpoch(1);
    store.applyRecognition({ ...recognition(1), frame_sequence: 4 }, 1, {
      kind: "valid",
    });
    store.applyRecognition({ ...recognition(2), frame_sequence: 5 }, 1, {
      kind: "valid",
    });

    expect(store.getSnapshot()).toMatchObject({
      lastAcceptedFrameSequence: 5,
      integrity: { kind: "valid" },
      recognition: { frame_sequence: 5, transition: { event_id: 2 } },
      shouldAnnounce: true,
    });
  });
  it("records duplicate and stale recognition without regressing its snapshot or announcement", () => {
    const store = new RecognitionStateStore();
    const updates = vi.fn();
    store.subscribe(updates);
    store.beginEpoch(1);
    store.applyRecognition({ ...recognition(2), frame_sequence: 5 }, 1, {
      kind: "valid",
    });
    store.applyRecognition({ ...recognition(3), frame_sequence: 5 }, 1, {
      kind: "valid",
    });
    expect(store.getSnapshot()).toMatchObject({
      integrity: { kind: "duplicate" },
      recognition: { frame_sequence: 5, transition: { event_id: 2 } },
      shouldAnnounce: false,
    });
    store.applyRecognition({ ...recognition(1), frame_sequence: 4 }, 1, {
      kind: "valid",
    });
    expect(store.getSnapshot()).toMatchObject({
      integrity: { kind: "stale" },
      recognition: { frame_sequence: 5, transition: { event_id: 2 } },
      shouldAnnounce: false,
    });
    expect(updates).toHaveBeenCalledTimes(4);
  });
  it("permits sequence restart after a new epoch", () => {
    const store = new RecognitionStateStore();
    store.beginEpoch(1);
    store.applyRecognition({ ...recognition(2), frame_sequence: 5 }, 1, {
      kind: "valid",
    });
    store.beginEpoch(2);
    store.applyRecognition({ ...recognition(1), frame_sequence: 1 }, 2, {
      kind: "valid",
    });
    expect(store.getSnapshot()).toMatchObject({
      epoch: 2,
      lastAcceptedFrameSequence: 1,
      recognition: { frame_sequence: 1, transition: { event_id: 1 } },
    });
  });
  it("retains accepted recognition when an optional recognition payload is malformed", () => {
    const store = new RecognitionStateStore();
    store.beginEpoch(1);
    store.applyRecognition(recognition(1), 1, { kind: "valid" });
    store.applyRecognition(undefined, 1, {
      kind: "malformed",
      reason: "recognition.frame_sequence must be a safe integer.",
    });
    expect(store.getSnapshot()).toMatchObject({
      integrity: {
        kind: "malformed",
        reason: "recognition.frame_sequence must be a safe integer.",
      },
      recognition: { frame_sequence: 4, transition: { event_id: 1 } },
      shouldAnnounce: false,
    });
  });
});
