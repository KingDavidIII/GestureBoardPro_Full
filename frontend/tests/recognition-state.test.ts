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
    store.applyRecognition(recognition(1), 1);
    store.applyRecognition(undefined, 1);
    expect(store.getSnapshot()).toMatchObject({
      recognition: null,
      shouldAnnounce: false,
    });
  });
});
