import type { RecognitionStateStore } from "../recognition";
import type { FrameStreamEvent } from "../streaming";
import type { GestureWebSocketClientEvent } from "../websocket";

/** Route already-validated application events into connection-local recognition state. */
export function createRecognitionEventComposition(
  recognition: RecognitionStateStore,
): {
  handleSocketEvent(event: GestureWebSocketClientEvent): void;
  handleStreamEvent(event: FrameStreamEvent): void;
  destroy(): void;
} {
  let epoch = 0;
  let destroyed = false;
  recognition.beginEpoch(epoch);
  return {
    handleSocketEvent(event): void {
      if (destroyed) return;
      if (event.type === "state.changed" && event.state !== "OPEN") {
        recognition.beginEpoch(++epoch);
      } else if (event.type === "protocol.message") {
        if (event.message.type === "connection.ready")
          recognition.setCapabilityAvailable(
            event.message.capabilities?.includes("gesture.recognition.v1") ??
              false,
            epoch,
          );
        else if (event.message.type === "gesture.result")
          recognition.applyRecognition(event.message.recognition, epoch);
      }
    },
    handleStreamEvent(event): void {
      if (destroyed) return;
      if (event.type === "state.changed" && event.state !== "STREAMING")
        recognition.clear(epoch);
    },
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      recognition.destroy();
    },
  };
}
