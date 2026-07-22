import {
  PROTOCOL_VERSION,
  decodeAnnotatedFrameEnvelope,
  parseServerMessageWithDiagnostics,
} from "../protocol";
import {
  releaseResourceOperations,
  type ResourceCleanupOperation,
} from "../lifecycle/resource-cleanup";
import type {
  AnnotatedFrameMessage,
  ClientControlMessage,
  ServerMessage,
} from "../protocol";
import {
  browserReconnectTimers,
  calculateReconnectDelay,
  createReconnectPolicy,
  type ReconnectPolicy,
  type ReconnectPolicyConfig,
  type ReconnectTimerApi,
} from "./reconnect-policy";
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
  readonly reconnectPolicy?: ReconnectPolicyConfig;
  readonly reconnectTimers?: ReconnectTimerApi;
  readonly random?: () => number;
}

interface ActiveConnection {
  readonly socket: WebSocketLike;
  readonly epoch: number;
  readonly reconnectAttempt: number;
  readonly handlers: Readonly<
    Record<"open" | "message" | "error" | "close", EventListener>
  >;
  readonly resolve: () => void;
  readonly reject: (error: GestureWebSocketClientError) => void;
}

const OPEN = 1;
const MAXIMUM_REQUEST_ID_LENGTH = 128;
const DEFAULT_MAXIMUM_FRAME_SIZE = 5 * 1024 * 1024;
const SOCKET_EVENT_TYPES = ["open", "message", "error", "close"] as const;

export class GestureWebSocketClient {
  readonly url: string;
  readonly maximumFrameSize: number;
  readonly reconnectPolicy: Readonly<ReconnectPolicy>;
  private readonly socketFactory: WebSocketFactory;
  private readonly subscriberErrorHandler: (error: unknown) => void;
  private readonly reconnectTimers: ReconnectTimerApi;
  private readonly random: () => number;
  private readonly listeners = new Set<GestureWebSocketClientListener>();
  private state = WebSocketClientState.IDLE;
  private active: ActiveConnection | null = null;
  private lastMessage: ServerMessage | null = null;
  private lastError: GestureWebSocketClientError | null = null;
  private annotatedFramesEnabled = false;
  private latestAnnotatedFrame: AnnotatedFrameMessage | null = null;
  private epoch = 0;
  private retryAttempts = 0;
  private reconnectTimer: unknown | null = null;
  private reconnectGeneration = 0;
  private intentionalDisconnect = false;
  private disposed = false;

  constructor(url: string, options: GestureWebSocketClientOptions = {}) {
    const parsed = new URL(url);
    if (parsed.protocol !== "ws:" && parsed.protocol !== "wss:")
      throw new GestureWebSocketClientError(
        GestureWebSocketClientErrorCode.INVALID_URL,
        "WebSocket URL must use ws: or wss:.",
      );
    const maximum = options.maximumFrameSize ?? DEFAULT_MAXIMUM_FRAME_SIZE;
    if (!Number.isSafeInteger(maximum) || maximum < 1)
      throw new GestureWebSocketClientError(
        GestureWebSocketClientErrorCode.INVALID_FRAME,
        "maximumFrameSize must be a positive integer.",
      );
    this.url = parsed.toString();
    this.maximumFrameSize = maximum;
    this.reconnectPolicy = createReconnectPolicy(options.reconnectPolicy);
    this.socketFactory =
      options.socketFactory ?? ((target) => new WebSocket(target));
    this.subscriberErrorHandler =
      options.subscriberErrorHandler ??
      ((error) => console.error("Subscriber failed", error));
    this.reconnectTimers = options.reconnectTimers ?? browserReconnectTimers;
    this.random = options.random ?? Math.random;
  }

  connect(): Promise<void> {
    if (this.disposed)
      return Promise.reject(
        this.clientError(
          GestureWebSocketClientErrorCode.INVALID_STATE,
          "WebSocket client has been destroyed.",
        ),
      );
    if (
      this.state === WebSocketClientState.CONNECTING ||
      this.state === WebSocketClientState.OPEN
    )
      return Promise.reject(
        this.clientError(
          GestureWebSocketClientErrorCode.INVALID_STATE,
          "WebSocket is already connecting or open.",
        ),
      );
    this.cancelReconnect("Manual connection requested");
    this.retryAttempts = 0;
    this.intentionalDisconnect = false;
    return this.startConnection(0);
  }

  disconnect(): void {
    if (!this.requiresDisconnect()) return;
    releaseResourceOperations(
      "GestureWebSocketClient.disconnect",
      this.prepareDisconnectOperations(),
    );
  }

  destroy(): void {
    if (this.disposed) return;
    this.disposed = true;
    const operations = this.requiresDisconnect()
      ? this.prepareDisconnectOperations()
      : [];
    operations.push(["listeners.clear", () => this.listeners.clear()]);
    releaseResourceOperations("GestureWebSocketClient.destroy", operations);
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
  setAnnotatedFramesEnabled(enabled: boolean, requestId?: string): void {
    if (typeof enabled !== "boolean")
      throw this.clientError(
        GestureWebSocketClientErrorCode.INVALID_CONTROL_MESSAGE,
        "enabled must be a boolean.",
      );
    this.sendControl(
      this.controlMessage("annotated_frame.set", requestId, enabled),
    );
  }
  getAnnotatedFramesEnabled(): boolean {
    return this.annotatedFramesEnabled;
  }
  getLatestAnnotatedFrame(): AnnotatedFrameMessage | null {
    return this.latestAnnotatedFrame;
  }
  subscribe(listener: GestureWebSocketClientListener): () => void {
    if (this.disposed) return () => undefined;
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
    return this.active?.socket.bufferedAmount ?? 0;
  }
  getReconnectAttempt(): number {
    return this.retryAttempts;
  }

  private startConnection(reconnectAttempt: number): Promise<void> {
    this.lastError = null;
    this.setState(WebSocketClientState.CONNECTING);
    const epoch = ++this.epoch;
    return new Promise<void>((resolve, reject) => {
      let socket: WebSocketLike;
      try {
        socket = this.socketFactory(this.url);
        socket.binaryType = "arraybuffer";
      } catch (cause) {
        const error = this.clientError(
          GestureWebSocketClientErrorCode.CONNECTION_FAILED,
          "WebSocket could not be constructed.",
          cause,
        );
        this.lastError = error;
        this.setState(WebSocketClientState.ERROR);
        reject(error);
        this.scheduleReconnect();
        return;
      }
      const handlers = {
        open: () => this.handleOpen(epoch),
        message: (event: Event) => this.handleMessage(epoch, event),
        error: (event: Event) => this.handleFailure(epoch, event),
        close: (event: Event) => this.handleClose(epoch, event),
      } satisfies ActiveConnection["handlers"];
      const connection: ActiveConnection = {
        socket,
        epoch,
        reconnectAttempt,
        handlers,
        resolve,
        reject,
      };
      this.active = connection;
      this.attach(connection);
    });
  }

  private handleOpen(epoch: number): void {
    const connection = this.current(epoch);
    if (!connection) return;
    this.retryAttempts = 0;
    this.setState(WebSocketClientState.OPEN);
    connection.resolve();
    if (connection.reconnectAttempt > 0)
      this.emit(
        Object.freeze({
          type: "reconnect.succeeded",
          attempt: connection.reconnectAttempt,
        }),
      );
  }

  private handleMessage(epoch: number, event: Event): void {
    if (!this.current(epoch)) return;
    const data = (event as MessageEvent<unknown>).data;
    if (typeof data !== "string") {
      if (data instanceof ArrayBuffer || data instanceof Blob)
        void this.handleAnnotatedFrame(epoch, data);
      else
        this.reportProtocolError(
          GestureWebSocketClientErrorCode.UNSUPPORTED_SERVER_MESSAGE,
          "Binary server messages are not supported.",
        );
      return;
    }
    try {
      const validated = parseServerMessageWithDiagnostics(data);
      const message = validated.message;
      if (!this.current(epoch)) return;
      this.lastMessage = message;
      if (message.type === "annotated_frame.set.ack")
        this.annotatedFramesEnabled = message.enabled;
      else if (message.type === "runtime.reset.ack")
        this.resetAnnotatedFrameState();
      this.emit(
        Object.freeze({
          type: "protocol.message" as const,
          message,
          ...(validated.recognitionIntegrity
            ? { recognitionIntegrity: validated.recognitionIntegrity }
            : {}),
        }),
      );
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
  }

  private handleFailure(epoch: number, event: Event): void {
    const connection = this.current(epoch);
    if (!connection) return;
    const error = this.clientError(
      GestureWebSocketClientErrorCode.CONNECTION_FAILED,
      "WebSocket connection failed.",
      event,
    );
    this.lastError = error;
    this.setState(WebSocketClientState.ERROR);
    this.emit(Object.freeze({ type: "socket.error", error }));
    this.finishUnexpected(connection, error, true);
  }

  private handleClose(epoch: number, event: Event): void {
    const connection = this.current(epoch);
    if (!connection) return;
    const closeEvent = event as CloseEvent;
    this.emit(
      Object.freeze({
        type: "socket.closed",
        code: closeEvent.code,
        reason: closeEvent.reason,
      }),
    );
    const error = this.clientError(
      GestureWebSocketClientErrorCode.CONNECTION_CLOSED,
      "WebSocket connection closed unexpectedly.",
    );
    this.setState(WebSocketClientState.CLOSED);
    this.finishUnexpected(connection, error, false);
  }

  private finishUnexpected(
    connection: ActiveConnection,
    error: GestureWebSocketClientError,
    closeSocket: boolean,
  ): void {
    if (!this.current(connection.epoch)) return;
    this.active = null;
    this.resetConnectionEpoch();
    const operations: ResourceCleanupOperation[] = [
      ...this.detachOperations(connection),
      ["connection.reject", () => connection.reject(error)],
    ];
    if (closeSocket)
      operations.push(["socket.close", () => connection.socket.close()]);
    operations.push(["reconnect.schedule", () => this.scheduleReconnect()]);
    releaseResourceOperations(
      "GestureWebSocketClient unexpected termination",
      operations,
    );
  }

  private scheduleReconnect(): void {
    if (
      !this.reconnectPolicy.enabled ||
      this.intentionalDisconnect ||
      this.disposed ||
      this.reconnectTimer !== null ||
      this.active
    )
      return;
    if (this.retryAttempts >= this.reconnectPolicy.maximumAttempts) {
      this.emit(
        Object.freeze({
          type: "reconnect.exhausted",
          attempts: this.retryAttempts,
        }),
      );
      return;
    }
    const attempt = this.retryAttempts + 1;
    const delayMs = calculateReconnectDelay(
      this.reconnectPolicy,
      attempt,
      this.random(),
    );
    this.retryAttempts = attempt;
    const generation = ++this.reconnectGeneration;
    let armed = false;
    const handle = this.reconnectTimers.set(() => {
      if (!armed || generation !== this.reconnectGeneration) return;
      this.reconnectTimer = null;
      if (this.intentionalDisconnect || this.disposed || this.active) return;
      const connection = this.startConnection(attempt);
      this.emit(Object.freeze({ type: "reconnect.started", attempt }));
      void connection.catch(() => undefined);
    }, delayMs);
    this.reconnectTimer = handle;
    armed = true;
    this.emit(Object.freeze({ type: "reconnect.scheduled", attempt, delayMs }));
  }

  private cancelReconnect(reason: string): void {
    const handle = this.reconnectTimer;
    if (handle === null) return;
    this.reconnectTimer = null;
    this.reconnectGeneration += 1;
    releaseResourceOperations("GestureWebSocketClient reconnect cancellation", [
      ["reconnect.timer.clear", () => this.reconnectTimers.clear(handle)],
      [
        "reconnect.cancelled.emit",
        () => this.emit(Object.freeze({ type: "reconnect.cancelled", reason })),
      ],
    ]);
  }

  private resetConnectionEpoch(): void {
    this.annotatedFramesEnabled = false;
    this.resetAnnotatedFrameState();
  }

  private resetAnnotatedFrameState(): void {
    this.latestAnnotatedFrame = null;
  }

  private controlMessage(
    type: ClientControlMessage["type"],
    requestId?: string,
    enabled?: boolean,
  ): ClientControlMessage {
    if (
      requestId !== undefined &&
      (typeof requestId !== "string" ||
        !requestId.trim() ||
        requestId.length > MAXIMUM_REQUEST_ID_LENGTH)
    )
      throw this.clientError(
        GestureWebSocketClientErrorCode.INVALID_CONTROL_MESSAGE,
        "Invalid request_id.",
      );
    return {
      protocol_version: PROTOCOL_VERSION,
      type,
      ...(requestId === undefined ? {} : { request_id: requestId }),
      ...(type === "annotated_frame.set" ? { enabled } : {}),
    } as ClientControlMessage;
  }

  private async handleAnnotatedFrame(
    epoch: number,
    data: ArrayBuffer | Blob,
  ): Promise<void> {
    try {
      const frame = await decodeAnnotatedFrameEnvelope(data);
      if (!this.current(epoch)) return;
      if (
        this.latestAnnotatedFrame &&
        frame.sequence <= this.latestAnnotatedFrame.sequence
      )
        return;
      this.latestAnnotatedFrame = frame;
      this.emit(Object.freeze({ type: "annotated-frame", frame }));
    } catch (cause) {
      if (this.current(epoch))
        this.reportProtocolError(
          GestureWebSocketClientErrorCode.INVALID_PROTOCOL_MESSAGE,
          "Invalid annotated frame envelope.",
          cause,
        );
    }
  }

  private sendControl(message: ClientControlMessage): void {
    this.requireOpen();
    this.send(JSON.stringify(message));
  }
  private send(payload: string | Blob | ArrayBuffer | Uint8Array): void {
    try {
      this.active?.socket.send(payload);
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
      !this.active ||
      this.state !== WebSocketClientState.OPEN ||
      this.active.socket.readyState !== OPEN
    )
      throw this.clientError(
        GestureWebSocketClientErrorCode.INVALID_STATE,
        "WebSocket is not open.",
      );
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
  private current(epoch: number): ActiveConnection | null {
    return this.active?.epoch === epoch ? this.active : null;
  }

  private requiresDisconnect(): boolean {
    return !(
      this.intentionalDisconnect &&
      !this.active &&
      this.reconnectTimer === null
    );
  }

  private prepareDisconnectOperations(): ResourceCleanupOperation[] {
    this.intentionalDisconnect = true;
    const operations: ResourceCleanupOperation[] = [];
    const reconnectTimer = this.reconnectTimer;
    if (reconnectTimer !== null) {
      this.reconnectTimer = null;
      this.reconnectGeneration += 1;
      operations.push(
        [
          "reconnect.timer.clear",
          () => this.reconnectTimers.clear(reconnectTimer),
        ],
        [
          "reconnect.cancelled.emit",
          () =>
            this.emit(
              Object.freeze({
                type: "reconnect.cancelled",
                reason: "Manually disconnected",
              }),
            ),
        ],
      );
    }

    this.resetConnectionEpoch();
    const connection = this.active;
    if (connection) {
      this.active = null;
      this.epoch += 1;
      const error = this.clientError(
        GestureWebSocketClientErrorCode.CONNECTION_CLOSED,
        "Connection was manually closed.",
      );
      operations.push(
        ["state.closing", () => this.setState(WebSocketClientState.CLOSING)],
        ...this.detachOperations(connection),
        ["connection.reject", () => connection.reject(error)],
        [
          "socket.close",
          () => connection.socket.close(1000, "Client disconnect"),
        ],
      );
    }

    operations.push([
      "state.closed",
      () => this.setState(WebSocketClientState.CLOSED),
    ]);
    return operations;
  }

  private attach(connection: ActiveConnection): void {
    for (const type of SOCKET_EVENT_TYPES)
      connection.socket.addEventListener(type, connection.handlers[type]);
  }

  private detachOperations(
    connection: ActiveConnection,
  ): ResourceCleanupOperation[] {
    return SOCKET_EVENT_TYPES.map(
      (type) =>
        [
          `socket.listener.${type}.remove`,
          () =>
            connection.socket.removeEventListener(
              type,
              connection.handlers[type],
            ),
        ] as const,
    );
  }
}
