export enum CameraState {
  IDLE = "IDLE",
  REQUESTING_PERMISSION = "REQUESTING_PERMISSION",
  READY = "READY",
  STOPPING = "STOPPING",
  STOPPED = "STOPPED",
  ERROR = "ERROR",
}

export enum CameraErrorCode {
  MEDIA_DEVICES_UNAVAILABLE = "MEDIA_DEVICES_UNAVAILABLE",
  PERMISSION_DENIED = "PERMISSION_DENIED",
  DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND",
  DEVICE_BUSY = "DEVICE_BUSY",
  CONSTRAINT_UNSATISFIED = "CONSTRAINT_UNSATISFIED",
  CAMERA_START_FAILED = "CAMERA_START_FAILED",
  CAMERA_START_CANCELLED = "CAMERA_START_CANCELLED",
  CAMERA_PLAYBACK_FAILED = "CAMERA_PLAYBACK_FAILED",
  INVALID_STATE = "INVALID_STATE",
  INVALID_CONFIGURATION = "INVALID_CONFIGURATION",
}

export class CameraError extends Error {
  constructor(
    readonly code: CameraErrorCode,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "CameraError";
  }
}

export interface CameraMetadata {
  readonly width: number | null;
  readonly height: number | null;
  readonly frameRate: number | null;
  readonly facingMode: string | null;
}

export type CameraEvent =
  | { readonly type: "state.changed"; readonly state: CameraState }
  | { readonly type: "ready"; readonly metadata: CameraMetadata }
  | { readonly type: "error"; readonly error: CameraError };

export type CameraListener = (event: CameraEvent) => void;
