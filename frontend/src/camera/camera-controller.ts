import { releaseResourceOperations } from "../lifecycle/resource-cleanup";
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
  private readonly releasedStreams = new WeakSet<MediaStream>();
  private state = CameraState.IDLE;
  private stream: MediaStream | null = null;
  private preview: PreviewVideoElement | null = null;
  private metadata: CameraMetadata | null = null;
  private acquisitionEpoch = 0;
  private destroyed = false;

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
    this.assertNotDestroyed();
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

    const epoch = ++this.acquisitionEpoch;
    let acquiredStream: MediaStream | null = null;
    this.setState(CameraState.REQUESTING_PERMISSION);

    try {
      acquiredStream = await this.mediaDevices.getUserMedia({
        audio: false,
        video: {
          width: { ideal: this.preferredWidth },
          height: { ideal: this.preferredHeight },
          frameRate: { ideal: this.preferredFrameRate },
          facingMode: { ideal: this.facingMode },
        },
      });
      this.assertCurrentAcquisition(epoch);

      const track = acquiredStream
        .getVideoTracks()
        .find((candidate) => candidate.readyState === "live");
      if (!track) {
        throw this.error(
          CameraErrorCode.CAMERA_START_FAILED,
          "No live video track was returned.",
        );
      }

      const metadata = this.readMetadata(track);
      this.assertCurrentAcquisition(epoch);
      this.stream = acquiredStream;
      this.metadata = metadata;

      const preview = this.preview;
      if (preview) await this.assignPreview(preview, acquiredStream);
      this.assertCurrentAcquisition(epoch);
      if (this.stream !== acquiredStream) throw this.cancelledStartError();

      this.setState(CameraState.READY);
      this.emit(Object.freeze({ type: "ready", metadata }));
      return metadata;
    } catch (cause) {
      const cancelled =
        !this.isCurrentAcquisition(epoch) || this.isCancellation(cause);
      if (acquiredStream) this.releaseStream(acquiredStream);
      if (cancelled) throw this.cancelledStartError(cause);

      const error =
        cause instanceof CameraError ? cause : this.mapStartError(cause);
      throw this.fail(error);
    }
  }

  async attachPreview(preview: PreviewVideoElement): Promise<void> {
    this.assertNotDestroyed();
    this.preview = preview;
    const stream = this.stream;
    if (!stream) return;

    try {
      await this.assignPreview(preview, stream);
    } catch (cause) {
      if (this.preview !== preview || this.stream !== stream) return;
      this.acquisitionEpoch += 1;
      this.releaseStream(stream);
      const error =
        cause instanceof CameraError ? cause : this.mapStartError(cause);
      throw this.fail(error);
    }
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

    this.acquisitionEpoch += 1;
    this.setState(CameraState.STOPPING);
    const stream = this.stream;
    this.stream = null;
    this.metadata = null;
    if (this.preview) this.preview.srcObject = null;

    try {
      if (stream) this.releaseStream(stream);
    } finally {
      this.setState(CameraState.STOPPED);
    }
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    releaseResourceOperations("CameraController", [
      ["listeners.clear", () => this.listeners.clear()],
      ["preview.detach", () => this.detachPreview()],
      ["camera.stop", () => this.stopForDestroy()],
    ]);
  }

  subscribe(listener: CameraListener): () => void {
    if (this.destroyed) return () => undefined;
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

  private assertNotDestroyed(): void {
    if (this.destroyed)
      throw this.error(
        CameraErrorCode.INVALID_STATE,
        "Camera controller has been destroyed.",
      );
  }

  private stopForDestroy(): void {
    this.stop();
    if (this.state === CameraState.IDLE) this.setState(CameraState.STOPPED);
  }

  private async assignPreview(
    preview: PreviewVideoElement,
    stream: MediaStream,
  ): Promise<void> {
    preview.srcObject = stream;
    try {
      await preview.play();
    } catch (cause) {
      if (preview.srcObject === stream) preview.srcObject = null;
      throw this.error(
        CameraErrorCode.CAMERA_PLAYBACK_FAILED,
        "Camera preview could not play.",
        cause,
      );
    }
  }

  private assertCurrentAcquisition(epoch: number): void {
    if (!this.isCurrentAcquisition(epoch)) throw this.cancelledStartError();
  }

  private isCurrentAcquisition(epoch: number): boolean {
    return this.acquisitionEpoch === epoch;
  }

  private isCancellation(cause: unknown): boolean {
    return (
      cause instanceof CameraError &&
      cause.code === CameraErrorCode.CAMERA_START_CANCELLED
    );
  }

  private cancelledStartError(cause?: unknown): CameraError {
    return this.error(
      CameraErrorCode.CAMERA_START_CANCELLED,
      "Camera start was cancelled.",
      cause,
    );
  }

  private releaseStream(stream: MediaStream): void {
    if (this.stream === stream) {
      this.stream = null;
      this.metadata = null;
    }
    if (this.preview?.srcObject === stream) this.preview.srcObject = null;
    if (this.releasedStreams.has(stream)) return;

    this.releasedStreams.add(stream);
    stream.getTracks().forEach((track) => track.stop());
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
