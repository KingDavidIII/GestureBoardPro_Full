import type { ServerMessage } from "../protocol";
import type { AnnotatedFrameMessage } from "../protocol";
import type { RecognitionIntegrity } from "../protocol/validation";
import type { GestureWebSocketClientError } from "./gesture-websocket-client";

export enum WebSocketClientState {
  IDLE = "IDLE",
  CONNECTING = "CONNECTING",
  OPEN = "OPEN",
  CLOSING = "CLOSING",
  CLOSED = "CLOSED",
  ERROR = "ERROR",
}

export type GestureWebSocketClientEvent =
  | { readonly type: "state.changed"; readonly state: WebSocketClientState }
  | {
      readonly type: "protocol.message";
      readonly message: ServerMessage;
      readonly recognitionIntegrity?: RecognitionIntegrity;
    }
  | { readonly type: "annotated-frame"; readonly frame: AnnotatedFrameMessage }
  | {
      readonly type: "reconnect.scheduled";
      readonly attempt: number;
      readonly delayMs: number;
    }
  | { readonly type: "reconnect.started"; readonly attempt: number }
  | { readonly type: "reconnect.succeeded"; readonly attempt: number }
  | { readonly type: "reconnect.exhausted"; readonly attempts: number }
  | { readonly type: "reconnect.cancelled"; readonly reason: string }
  | {
      readonly type: "protocol.error";
      readonly error: GestureWebSocketClientError;
    }
  | {
      readonly type: "socket.error";
      readonly error: GestureWebSocketClientError;
    }
  | {
      readonly type: "socket.closed";
      readonly code: number;
      readonly reason: string;
    };

export type GestureWebSocketClientListener = (
  event: GestureWebSocketClientEvent,
) => void;
