import type { GestureRecognition } from "../protocol/messages";

export interface RecognitionState {
  readonly availability: "unavailable" | "available";
  readonly capabilityAvailable: boolean;
  readonly epoch: number;
  readonly recognition: GestureRecognition | null;
  readonly announcedEventId: number | null;
  readonly shouldAnnounce: boolean;
}
export type RecognitionStateListener = (snapshot: RecognitionState) => void;

export const emptyRecognitionState = (epoch = 0): RecognitionState =>
  Object.freeze({
    availability: "unavailable",
    capabilityAvailable: false,
    epoch,
    recognition: null,
    announcedEventId: null,
    shouldAnnounce: false,
  });

const copy = (value: GestureRecognition): GestureRecognition =>
  Object.freeze({
    ...value,
    primary_hand:
      value.primary_hand && Object.freeze({ ...value.primary_hand }),
    candidate: value.candidate && Object.freeze({ ...value.candidate }),
    stable: value.stable && Object.freeze({ ...value.stable }),
    transition: value.transition && Object.freeze({ ...value.transition }),
  });

export class RecognitionStateStore {
  private state: RecognitionState = emptyRecognitionState();
  private readonly listeners = new Set<RecognitionStateListener>();
  private destroyed = false;
  getSnapshot(): RecognitionState {
    return this.state;
  }
  subscribe(listener: RecognitionStateListener): () => void {
    if (!this.destroyed) this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  beginEpoch(epoch: number): void {
    if (epoch >= this.state.epoch) this.publish(emptyRecognitionState(epoch));
  }
  setCapabilityAvailable(available: boolean, epoch: number): void {
    if (epoch !== this.state.epoch || this.destroyed) return;
    this.publish(
      Object.freeze({
        ...this.state,
        capabilityAvailable: available,
        availability:
          available && this.state.recognition ? "available" : "unavailable",
        shouldAnnounce: false,
      }),
    );
  }
  applyRecognition(
    recognition: GestureRecognition | null | undefined,
    epoch: number,
  ): void {
    if (epoch !== this.state.epoch || this.destroyed) return;
    if (recognition == null) {
      this.publish(
        Object.freeze({
          ...emptyRecognitionState(epoch),
          capabilityAvailable: this.state.capabilityAvailable,
        }),
      );
      return;
    }
    const value = copy(recognition);
    const announce =
      value.transition !== null &&
      value.transition.event_id !== this.state.announcedEventId;
    const eventId = value.transition?.event_id;
    this.publish(
      Object.freeze({
        availability: "available",
        capabilityAvailable: this.state.capabilityAvailable,
        epoch,
        recognition: value,
        announcedEventId:
          announce && eventId !== undefined
            ? eventId
            : this.state.announcedEventId,
        shouldAnnounce: announce,
      }),
    );
  }
  clear(epoch: number): void {
    this.applyRecognition(null, epoch);
  }
  destroy(): void {
    this.destroyed = true;
    this.listeners.clear();
  }
  private publish(state: RecognitionState): void {
    this.state = state;
    for (const listener of [...this.listeners]) listener(state);
  }
}
