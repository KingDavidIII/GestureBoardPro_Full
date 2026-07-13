export enum FrameStreamState {
  IDLE = "IDLE",
  STARTING = "STARTING",
  STREAMING = "STREAMING",
  STOPPING = "STOPPING",
  STOPPED = "STOPPED",
  ERROR = "ERROR",
}

export enum FrameStreamDecision {
  SENT = "SENT",
  TIMING_SKIPPED = "TIMING_SKIPPED",
  BACKPRESSURE_SKIPPED = "BACKPRESSURE_SKIPPED",
  SOCKET_NOT_OPEN = "SOCKET_NOT_OPEN",
  CAMERA_NOT_READY = "CAMERA_NOT_READY",
}

export enum FrameStreamErrorCode {
  INVALID_STATE = "INVALID_STATE",
  INVALID_CONFIGURATION = "INVALID_CONFIGURATION",
  CAMERA_NOT_READY = "CAMERA_NOT_READY",
  SOCKET_NOT_OPEN = "SOCKET_NOT_OPEN",
  ENCODING_FAILED = "ENCODING_FAILED",
  SEND_FAILED = "SEND_FAILED",
}

export class FrameStreamError extends Error {
  constructor(
    readonly code: FrameStreamErrorCode,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "FrameStreamError";
  }
}

export interface FrameStreamMetrics {
  readonly targetFps: number;
  readonly jpegQuality: number;
  readonly outputWidth: number;
  readonly outputHeight: number;
  readonly startedAt: number | null;
  readonly stoppedAt: number | null;
  readonly framesAttempted: number;
  readonly framesEncoded: number;
  readonly framesSent: number;
  readonly framesDroppedForBackpressure: number;
  readonly framesDroppedForTiming: number;
  readonly encodingFailures: number;
  readonly sendFailures: number;
  readonly lastFrameSize: number | null;
  readonly lastFrameWidth: number | null;
  readonly lastFrameHeight: number | null;
  readonly effectiveFps: number;
}

export type FrameStreamEvent =
  | { readonly type: "state.changed"; readonly state: FrameStreamState }
  | { readonly type: "decision"; readonly decision: FrameStreamDecision }
  | { readonly type: "metrics.changed"; readonly metrics: FrameStreamMetrics }
  | { readonly type: "error"; readonly error: FrameStreamError };

export type FrameStreamListener = (event: FrameStreamEvent) => void;
