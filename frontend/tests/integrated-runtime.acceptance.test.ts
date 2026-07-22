// @vitest-environment node

import { afterEach, describe, expect, it } from "vitest";

import { createRecognitionEventComposition } from "../src/application/application-composition";
import { AnnotationCorrelation } from "../src/dashboard";
import { parseServerMessage } from "../src/protocol";
import { RecognitionStateStore } from "../src/recognition";
import { GestureWebSocketClient, WebSocketClientState } from "../src/websocket";
import type { GestureWebSocketClientEvent } from "../src/websocket";

type ProtocolMessageEvent = Extract<
  GestureWebSocketClientEvent,
  { type: "protocol.message" }
>;

const protocol = (event: GestureWebSocketClientEvent): ProtocolMessageEvent => {
  if (event.type !== "protocol.message")
    throw new Error("Expected a protocol message event.");
  return event;
};

const url = process.env.GESTUREBOARD_ACCEPTANCE_WS_URL;
const acceptanceTest = url ? it : it.skip;
const jpeg = Uint8Array.from(
  Buffer.from(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9k=",
    "base64",
  ),
);

const waitFor = async <T>(
  values: readonly T[],
  predicate: (value: T) => boolean,
  message: string,
): Promise<T> => {
  const existing = values.find(predicate);
  if (existing) return existing;
  return await new Promise<T>((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error(message)), 10_000);
    const poll = () => {
      const match = values.find(predicate);
      if (match) {
        clearTimeout(deadline);
        resolve(match);
      } else setTimeout(poll, 10);
    };
    poll();
  });
};

describe("Alpha 7 integrated runtime acceptance", () => {
  const clients: GestureWebSocketClient[] = [];

  afterEach(() => {
    for (const client of clients) client.destroy();
    clients.length = 0;
  });

  acceptanceTest(
    "uses the real loopback ASGI route through reset and reconnect epochs",
    async () => {
      expect(url).toMatch(/^ws:\/\/127\.0\.0\.1:\d+\/ws\/$/);
      const client = new GestureWebSocketClient(url as string, {
        reconnectPolicy: { enabled: false },
      });
      clients.push(client);
      const recognition = new RecognitionStateStore();
      const correlation = new AnnotationCorrelation();
      const composition = createRecognitionEventComposition(
        recognition,
        correlation,
      );
      const events: GestureWebSocketClientEvent[] = [];
      const updates: string[] = [];
      const unsubscribe = client.subscribe((event) => {
        events.push(event);
        composition.handleSocketEvent(event);
      });
      const unsubscribeCorrelation = correlation.subscribe((update) => {
        updates.push(update.kind);
      });

      try {
        await client.connect();
        const readyEvent = await waitFor(
          events,
          (
            event,
          ): event is Extract<
            GestureWebSocketClientEvent,
            { type: "protocol.message" }
          > =>
            event.type === "protocol.message" &&
            event.message.type === "connection.ready",
          "connection.ready was not received",
        );
        const ready = parseServerMessage(
          JSON.stringify(protocol(readyEvent).message),
        );
        expect(ready.type).toBe("connection.ready");
        if (ready.type !== "connection.ready")
          throw new Error("Expected ready.");
        expect(ready.capabilities).toEqual(
          expect.arrayContaining([
            "annotated_frame.jpeg.v1",
            "gesture.recognition.v1",
          ]),
        );

        client.sendPing("alpha7-ping");
        const pong = await waitFor(
          events,
          (event) =>
            event.type === "protocol.message" && event.message.type === "pong",
          "pong was not received",
        );
        expect(protocol(pong).message).toMatchObject({
          request_id: "alpha7-ping",
        });

        client.setAnnotatedFramesEnabled(true, "alpha7-enable");
        const enabled = await waitFor(
          events,
          (event) =>
            event.type === "protocol.message" &&
            event.message.type === "annotated_frame.set.ack" &&
            event.message.enabled,
          "annotation enable acknowledgement was not received",
        );
        expect(protocol(enabled).message).toMatchObject({
          request_id: "alpha7-enable",
          enabled: true,
        });

        client.sendFrame(jpeg);
        const result = await waitFor(
          events,
          (event) =>
            event.type === "protocol.message" &&
            event.message.type === "gesture.result",
          "gesture.result was not received",
        );
        const resultMessage = protocol(result).message;
        if (resultMessage.type !== "gesture.result")
          throw new Error("Expected gesture.result.");
        expect(parseServerMessage(JSON.stringify(resultMessage))).toEqual(
          resultMessage,
        );
        expect(resultMessage.scheduler).toMatchObject({
          received_frames: 1,
          processed_frames: 1,
          pending_frames: 0,
        });
        expect(recognition.getSnapshot().capabilityAvailable).toBe(true);

        const frame = await waitFor(
          events,
          (event) => event.type === "annotated-frame",
          "annotated GBF1 frame was not received",
        );
        const decoded = (
          frame as Extract<
            GestureWebSocketClientEvent,
            { type: "annotated-frame" }
          >
        ).frame;
        expect(decoded.sequence).toBe(resultMessage.sequence);
        expect(decoded.size).toBe(resultMessage.annotation?.byte_length);
        expect(updates).toEqual(["frame"]);

        client.setAnnotatedFramesEnabled(false, "alpha7-disable");
        const disabled = await waitFor(
          events,
          (event) =>
            event.type === "protocol.message" &&
            event.message.type === "annotated_frame.set.ack" &&
            !event.message.enabled,
          "annotation disable acknowledgement was not received",
        );
        expect(protocol(disabled).message).toMatchObject({
          request_id: "alpha7-disable",
          enabled: false,
        });
        expect(correlation.clearPresentation()).toEqual({ kind: "clear" });

        client.resetRuntime("alpha7-reset");
        const reset = await waitFor(
          events,
          (event) =>
            event.type === "protocol.message" &&
            event.message.type === "runtime.reset.ack",
          "runtime.reset acknowledgement was not received",
        );
        expect(protocol(reset).message).toMatchObject({
          request_id: "alpha7-reset",
        });

        client.setAnnotatedFramesEnabled(true, "alpha7-reenable");
        await waitFor(
          events,
          (event) =>
            event.type === "protocol.message" &&
            event.message.type === "annotated_frame.set.ack" &&
            event.message.request_id === "alpha7-reenable",
          "annotation re-enable acknowledgement was not received",
        );
        const firstFrame = frame;
        client.sendFrame(jpeg);
        await waitFor(
          events,
          (event) =>
            event.type === "annotated-frame" &&
            event.frame.sequence === resultMessage.sequence &&
            event !== firstFrame,
          "post-reset annotated frame was not received",
        );
        expect(updates).toEqual(["frame", "frame"]);

        client.disconnect();
        expect(client.getState()).toBe(WebSocketClientState.CLOSED);
        await client.connect();
        await waitFor(
          events,
          (event) =>
            event.type === "protocol.message" &&
            event.message.type === "connection.ready",
          "reconnect connection.ready was not received",
        );
        expect(recognition.getSnapshot().epoch).toBeGreaterThan(0);
      } finally {
        unsubscribeCorrelation();
        unsubscribe();
        composition.destroy();
        correlation.destroy();
      }
    },
    30_000,
  );
});
