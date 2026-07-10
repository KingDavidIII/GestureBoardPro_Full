import {
  CameraError,
  CameraErrorCode,
  CameraState,
  type CameraEvent,
  type CameraListener,
  type CameraMetadata,
} from "./camera-state";

export type CameraFacingMode = "user" | "environment";

export interface CameraControllerConfig {
  readonly preferredWidth?: number;
  readonly preferredHeight?: number;
  readonly preferredFrameRate?: number;
  readonly facingMode?: CameraFacingMode;
  readonly mediaDevices?: Pick<MediaDevices, "getUserMedia">;
  readonly subscriberErrorHandler?: (error: unknown) => void;
}

export interface PreviewVideoElement {
  srcObject: MediaProvider | null;
  readonly videoWidth: number;
  readonly videoHeight: number;
  readonly readyState: number;
  play(): Promise<void>;
}

const DEFAULT_WIDTH = 640;
const DEFAULT_HEIGHT = 480;
const DEFAULT_FRAME_RATE = 8;

export class CameraController {
  private readonly mediaDevices: Pick<MediaDevices, "getUserMedia"> | undefined;
  private readonly subscriberErrorHandler: (error: unknown) => void;
  private readonly listeners = new Set<CameraListener>();
  private state = CameraState.IDLE;
  private stream: MediaStream | null = null;
  private preview: PreviewVideoElement | null = null;
  private metadata: CameraMetadata | null = null;

  readonly preferredWidth: number;
  readonly preferredHeight: number;
  readonly preferredFrameRate: number;
  readonly facingMode: CameraFacingMode;

  constructor(config: CameraControllerConfig = {}) {
    this.preferredWidth = this.positiveNumber(
      config.preferredWidth ?? DEFAULT_WIDTH,
      "preferredWidth",
    );
    this.preferredHeight = this.positiveNumber(
      config.preferredHeight ?? DEFAULT_HEIGHT,
      "preferredHeight",
    );
    this.preferredFrameRate = this.positiveNumber(
      config.preferredFrameRate ?? DEFAULT_FRAME_RATE,
      "preferredFrameRate",
    );
    this.facingMode = config.facingMode ?? "user";
    if (this.facingMode !== "user" && this.facingMode !== "environment") {
      throw new CameraError(
        CameraErrorCode.INVALID_CONFIGURATION,
        "facingMode must be 'user' or 'environment'.",
      );
    }
    this.mediaDevices =
      config.mediaDevices ?? globalThis.navigator?.mediaDevices;
    this.subscriberErrorHandler =
      config.subscriberErrorHandler ??
      ((error) => console.error("Camera listener failed", error));
  }

  async start(): Promise<CameraMetadata> {
    if (
      this.state === CameraState.REQUESTING_PERMISSION ||
      this.state === CameraState.READY
    ) {
      throw this.error(
        CameraErrorCode.INVALID_STATE,
        "Camera is already starting or ready.",
      );
    }
    if (!this.mediaDevices?.getUserMedia) {
      throw this.fail(
        this.error(
          CameraErrorCode.MEDIA_DEVICES_UNAVAILABLE,
          "Camera access is unavailable.",
        ),
      );
    }

    this.setState(CameraState.REQUESTING_PERMISSION);
    try {
      const stream = await this.mediaDevices.getUserMedia({
        audio: false,
        video: {
          width: { ideal: this.preferredWidth },
          height: { ideal: this.preferredHeight },
          frameRate: { ideal: this.preferredFrameRate },
          facingMode: { ideal: this.facingMode },
        },
      });
      const track = stream
        .getVideoTracks()
        .find((candidate) => candidate.readyState === "live");
      if (!track) {
        stream.getTracks().forEach((candidate) => candidate.stop());
        throw this.error(
          CameraErrorCode.CAMERA_START_FAILED,
          "No live video track was returned.",
        );
      }
      this.stream = stream;
      this.metadata = this.readMetadata(track);
      if (this.preview) await this.assignPreview(this.preview);
      this.setState(CameraState.READY);
      this.emit(Object.freeze({ type: "ready", metadata: this.metadata }));
      return this.metadata;
    } catch (cause) {
      if (cause instanceof CameraError) throw this.fail(cause);
      throw this.fail(this.mapStartError(cause));
    }
  }

  async attachPreview(preview: PreviewVideoElement): Promise<void> {
    this.preview = preview;
    if (this.stream) await this.assignPreview(preview);
  }

  detachPreview(): void {
    if (this.preview) this.preview.srcObject = null;
    this.preview = null;
  }

  stop(): void {
    if (
      !this.stream &&
      (this.state === CameraState.IDLE || this.state === CameraState.STOPPED)
    )
      return;
    this.setState(CameraState.STOPPING);
    const stream = this.stream;
    this.stream = null;
    this.metadata = null;
    if (this.preview) this.preview.srcObject = null;
    stream?.getTracks().forEach((track) => track.stop());
    this.setState(CameraState.STOPPED);
  }

  subscribe(listener: CameraListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getState(): CameraState {
    return this.state;
  }
  getStream(): MediaStream | null {
    return this.stream;
  }
  getMetadata(): CameraMetadata | null {
    return this.metadata;
  }
  getPreview(): PreviewVideoElement | null {
    return this.preview;
  }

  private async assignPreview(preview: PreviewVideoElement): Promise<void> {
    preview.srcObject = this.stream;
    try {
      await preview.play();
    } catch (cause) {
      throw this.error(
        CameraErrorCode.CAMERA_PLAYBACK_FAILED,
        "Camera preview could not play.",
        cause,
      );
    }
  }

  private readMetadata(track: MediaStreamTrack): CameraMetadata {
    const settings = track.getSettings();
    return Object.freeze({
      width: settings.width ?? null,
      height: settings.height ?? null,
      frameRate: settings.frameRate ?? null,
      facingMode: settings.facingMode ?? null,
    });
  }

  private positiveNumber(value: number, name: string): number {
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
      throw new CameraError(
        CameraErrorCode.INVALID_CONFIGURATION,
        `${name} must be a positive number.`,
      );
    }
    return value;
  }

  private mapStartError(cause: unknown): CameraError {
    const name = cause instanceof DOMException ? cause.name : "";
    const codes: Record<string, CameraErrorCode> = {
      NotAllowedError: CameraErrorCode.PERMISSION_DENIED,
      SecurityError: CameraErrorCode.PERMISSION_DENIED,
      NotFoundError: CameraErrorCode.DEVICE_NOT_FOUND,
      NotReadableError: CameraErrorCode.DEVICE_BUSY,
      OverconstrainedError: CameraErrorCode.CONSTRAINT_UNSATISFIED,
    };
    return this.error(
      codes[name] ?? CameraErrorCode.CAMERA_START_FAILED,
      "Camera could not be started.",
      cause,
    );
  }

  private error(
    code: CameraErrorCode,
    message: string,
    cause?: unknown,
  ): CameraError {
    return new CameraError(
      code,
      message,
      cause === undefined ? undefined : { cause },
    );
  }

  private fail(error: CameraError): CameraError {
    this.setState(CameraState.ERROR);
    this.emit(Object.freeze({ type: "error", error }));
    return error;
  }

  private setState(state: CameraState): void {
    this.state = state;
    this.emit(Object.freeze({ type: "state.changed", state }));
  }

  private emit(event: CameraEvent): void {
    for (const listener of [...this.listeners]) {
      try {
        listener(event);
      } catch (error) {
        this.subscriberErrorHandler(error);
      }
    }
  }
}
