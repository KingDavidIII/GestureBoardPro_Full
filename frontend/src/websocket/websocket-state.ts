import type { ServerMessage } from "../protocol";
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
  | { readonly type: "protocol.message"; readonly message: ServerMessage }
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
