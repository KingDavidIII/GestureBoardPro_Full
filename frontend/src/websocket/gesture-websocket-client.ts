import { PROTOCOL_VERSION, parseServerMessage } from "../protocol";
import type { ClientControlMessage, ServerMessage } from "../protocol";
import {
  WebSocketClientState,
  type GestureWebSocketClientEvent,
  type GestureWebSocketClientListener,
} from "./websocket-state";

export enum GestureWebSocketClientErrorCode {
  INVALID_URL = "INVALID_URL",
  INVALID_STATE = "INVALID_STATE",
  CONNECTION_FAILED = "CONNECTION_FAILED",
  CONNECTION_CLOSED = "CONNECTION_CLOSED",
  SEND_FAILED = "SEND_FAILED",
  INVALID_CONTROL_MESSAGE = "INVALID_CONTROL_MESSAGE",
  INVALID_FRAME = "INVALID_FRAME",
  FRAME_TOO_LARGE = "FRAME_TOO_LARGE",
  INVALID_JSON = "INVALID_JSON",
  INVALID_PROTOCOL_MESSAGE = "INVALID_PROTOCOL_MESSAGE",
  UNSUPPORTED_PROTOCOL_VERSION = "UNSUPPORTED_PROTOCOL_VERSION",
  UNSUPPORTED_SERVER_MESSAGE = "UNSUPPORTED_SERVER_MESSAGE",
}

export class GestureWebSocketClientError extends Error {
  readonly code: GestureWebSocketClientErrorCode;

  constructor(
    code: GestureWebSocketClientErrorCode,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "GestureWebSocketClientError";
    this.code = code;
  }
}

export interface WebSocketLike {
  binaryType: BinaryType;
  readonly readyState: number;
  readonly bufferedAmount: number;
  addEventListener(type: string, listener: EventListener): void;
  removeEventListener(type: string, listener: EventListener): void;
  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void;
  close(code?: number, reason?: string): void;
}

export type WebSocketFactory = (url: string) => WebSocketLike;

export interface GestureWebSocketClientOptions {
  readonly maximumFrameSize?: number;
  readonly socketFactory?: WebSocketFactory;
  readonly subscriberErrorHandler?: (error: unknown) => void;
}

const OPEN = 1;
const MAXIMUM_REQUEST_ID_LENGTH = 128;
const DEFAULT_MAXIMUM_FRAME_SIZE = 5 * 1024 * 1024;

export class GestureWebSocketClient {
  readonly url: string;
  readonly maximumFrameSize: number;
  private readonly socketFactory: WebSocketFactory;
  private readonly subscriberErrorHandler: (error: unknown) => void;
  private readonly listeners = new Set<GestureWebSocketClientListener>();
  private state = WebSocketClientState.IDLE;
  private socket: WebSocketLike | null = null;
  private lastMessage: ServerMessage | null = null;
  private lastError: GestureWebSocketClientError | null = null;
  private pendingResolve: (() => void) | null = null;
  private pendingReject:
    | ((reason: GestureWebSocketClientError) => void)
    | null = null;

  private readonly onOpen: EventListener = () => {
    this.setState(WebSocketClientState.OPEN);
    this.pendingResolve?.();
    this.clearPending();
  };

  private readonly onMessage: EventListener = (event) => {
    const data = (event as MessageEvent<unknown>).data;
    if (typeof data !== "string") {
      this.reportProtocolError(
        GestureWebSocketClientErrorCode.UNSUPPORTED_SERVER_MESSAGE,
        "Binary server messages are not supported.",
      );
      return;
    }
    try {
      const message = parseServerMessage(data);
      this.lastMessage = message;
      this.emit(Object.freeze({ type: "protocol.message", message }));
    } catch (error) {
      const code =
        typeof error === "object" &&
        error !== null &&
        "code" in error &&
        typeof error.code === "string" &&
        error.code in GestureWebSocketClientErrorCode
          ? (error.code as GestureWebSocketClientErrorCode)
          : GestureWebSocketClientErrorCode.INVALID_PROTOCOL_MESSAGE;
      this.reportProtocolError(code, "Invalid server protocol message.", error);
    }
  };

  private readonly onError: EventListener = (event) => {
    const error = new GestureWebSocketClientError(
      GestureWebSocketClientErrorCode.CONNECTION_FAILED,
      "WebSocket connection failed.",
      { cause: event },
    );
    this.lastError = error;
    this.setState(WebSocketClientState.ERROR);
    this.emit(Object.freeze({ type: "socket.error", error }));
    this.pendingReject?.(error);
    this.clearPending();
    this.disposeSocket(true);
  };

  private readonly onClose: EventListener = (event) => {
    const closeEvent = event as CloseEvent;
    this.disposeSocket(false);
    this.setState(WebSocketClientState.CLOSED);
    this.emit(
      Object.freeze({
        type: "socket.closed",
        code: closeEvent.code,
        reason: closeEvent.reason,
      }),
    );
    if (this.pendingReject) {
      const error = new GestureWebSocketClientError(
        GestureWebSocketClientErrorCode.CONNECTION_CLOSED,
        "WebSocket closed before opening.",
      );
      this.lastError = error;
      this.pendingReject(error);
      this.clearPending();
    }
  };

  constructor(url: string, options: GestureWebSocketClientOptions = {}) {
    const parsed = new URL(url);
    if (parsed.protocol !== "ws:" && parsed.protocol !== "wss:") {
      throw new GestureWebSocketClientError(
        GestureWebSocketClientErrorCode.INVALID_URL,
        "WebSocket URL must use ws: or wss:.",
      );
    }
    const maximum = options.maximumFrameSize ?? DEFAULT_MAXIMUM_FRAME_SIZE;
    if (!Number.isSafeInteger(maximum) || maximum < 1) {
      throw new GestureWebSocketClientError(
        GestureWebSocketClientErrorCode.INVALID_FRAME,
        "maximumFrameSize must be a positive integer.",
      );
    }
    this.url = parsed.toString();
    this.maximumFrameSize = maximum;
    this.socketFactory =
      options.socketFactory ?? ((target) => new WebSocket(target));
    this.subscriberErrorHandler =
      options.subscriberErrorHandler ??
      ((error) => console.error("Subscriber failed", error));
  }

  connect(): Promise<void> {
    if (
      this.state === WebSocketClientState.CONNECTING ||
      this.state === WebSocketClientState.OPEN
    ) {
      return Promise.reject(
        new GestureWebSocketClientError(
          GestureWebSocketClientErrorCode.INVALID_STATE,
          "WebSocket is already connecting or open.",
        ),
      );
    }
    this.lastError = null;
    this.setState(WebSocketClientState.CONNECTING);
    try {
      this.socket = this.socketFactory(this.url);
      this.socket.binaryType = "arraybuffer";
      this.attach(this.socket);
    } catch (cause) {
      const error = new GestureWebSocketClientError(
        GestureWebSocketClientErrorCode.CONNECTION_FAILED,
        "WebSocket could not be constructed.",
        { cause },
      );
      this.lastError = error;
      this.setState(WebSocketClientState.ERROR);
      return Promise.reject(error);
    }
    return new Promise<void>((resolve, reject) => {
      this.pendingResolve = resolve;
      this.pendingReject = reject;
    });
  }

  disconnect(): void {
    if (!this.socket) {
      if (this.state !== WebSocketClientState.IDLE)
        this.setState(WebSocketClientState.CLOSED);
      return;
    }
    this.setState(WebSocketClientState.CLOSING);
    const socket = this.socket;
    this.detach(socket);
    this.socket = null;
    socket.close(1000, "Client disconnect");
    if (this.pendingReject) {
      this.pendingReject(
        new GestureWebSocketClientError(
          GestureWebSocketClientErrorCode.CONNECTION_CLOSED,
          "Connection attempt was cancelled.",
        ),
      );
      this.clearPending();
    }
    this.setState(WebSocketClientState.CLOSED);
  }

  sendPing(requestId?: string): void {
    this.sendControl(this.controlMessage("ping", requestId));
  }

  resetRuntime(requestId?: string): void {
    this.sendControl(this.controlMessage("runtime.reset", requestId));
  }

  sendFrame(payload: Blob | ArrayBuffer | Uint8Array): void {
    this.requireOpen();
    const size = this.frameSize(payload);
    if (size === 0)
      throw this.clientError(
        GestureWebSocketClientErrorCode.INVALID_FRAME,
        "Frame is empty.",
      );
    if (size > this.maximumFrameSize)
      throw this.clientError(
        GestureWebSocketClientErrorCode.FRAME_TOO_LARGE,
        "Frame exceeds the outbound size limit.",
      );
    this.send(payload);
  }

  subscribe(listener: GestureWebSocketClientListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  unsubscribe(listener: GestureWebSocketClientListener): void {
    this.listeners.delete(listener);
  }

  getState(): WebSocketClientState {
    return this.state;
  }
  getLastMessage(): ServerMessage | null {
    return this.lastMessage;
  }
  getLastError(): GestureWebSocketClientError | null {
    return this.lastError;
  }
  getBufferedAmount(): number {
    return this.socket?.bufferedAmount ?? 0;
  }

  private controlMessage(
    type: ClientControlMessage["type"],
    requestId?: string,
  ): ClientControlMessage {
    if (requestId !== undefined) {
      if (
        typeof requestId !== "string" ||
        !requestId.trim() ||
        requestId.length > MAXIMUM_REQUEST_ID_LENGTH
      ) {
        throw this.clientError(
          GestureWebSocketClientErrorCode.INVALID_CONTROL_MESSAGE,
          "Invalid request_id.",
        );
      }
      return {
        protocol_version: PROTOCOL_VERSION,
        type,
        request_id: requestId,
      } as ClientControlMessage;
    }
    return { protocol_version: PROTOCOL_VERSION, type } as ClientControlMessage;
  }

  private sendControl(message: ClientControlMessage): void {
    this.requireOpen();
    this.send(JSON.stringify(message));
  }

  private send(payload: string | Blob | ArrayBuffer | Uint8Array): void {
    try {
      this.socket?.send(payload);
    } catch (cause) {
      throw this.clientError(
        GestureWebSocketClientErrorCode.SEND_FAILED,
        "WebSocket send failed.",
        cause,
      );
    }
  }

  private frameSize(payload: unknown): number {
    if (payload instanceof Blob) return payload.size;
    if (payload instanceof ArrayBuffer) return payload.byteLength;
    if (payload instanceof Uint8Array) return payload.byteLength;
    throw this.clientError(
      GestureWebSocketClientErrorCode.INVALID_FRAME,
      "Unsupported frame payload type.",
    );
  }

  private requireOpen(): void {
    if (
      !this.socket ||
      this.state !== WebSocketClientState.OPEN ||
      this.socket.readyState !== OPEN
    ) {
      throw this.clientError(
        GestureWebSocketClientErrorCode.INVALID_STATE,
        "WebSocket is not open.",
      );
    }
  }

  private reportProtocolError(
    code: GestureWebSocketClientErrorCode,
    message: string,
    cause?: unknown,
  ): void {
    const error = this.clientError(code, message, cause);
    this.lastError = error;
    this.emit(Object.freeze({ type: "protocol.error", error }));
  }

  private clientError(
    code: GestureWebSocketClientErrorCode,
    message: string,
    cause?: unknown,
  ): GestureWebSocketClientError {
    return new GestureWebSocketClientError(
      code,
      message,
      cause === undefined ? undefined : { cause },
    );
  }

  private setState(state: WebSocketClientState): void {
    this.state = state;
    this.emit(Object.freeze({ type: "state.changed", state }));
  }

  private emit(event: GestureWebSocketClientEvent): void {
    for (const listener of [...this.listeners]) {
      try {
        listener(event);
      } catch (error) {
        this.subscriberErrorHandler(error);
      }
    }
  }

  private attach(socket: WebSocketLike): void {
    socket.addEventListener("open", this.onOpen);
    socket.addEventListener("message", this.onMessage);
    socket.addEventListener("error", this.onError);
    socket.addEventListener("close", this.onClose);
  }

  private detach(socket: WebSocketLike): void {
    socket.removeEventListener("open", this.onOpen);
    socket.removeEventListener("message", this.onMessage);
    socket.removeEventListener("error", this.onError);
    socket.removeEventListener("close", this.onClose);
  }

  private disposeSocket(close: boolean): void {
    if (!this.socket) return;
    const socket = this.socket;
    this.detach(socket);
    this.socket = null;
    if (close) socket.close();
  }

  private clearPending(): void {
    this.pendingResolve = null;
    this.pendingReject = null;
  }
}
