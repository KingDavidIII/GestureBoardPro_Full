export interface FrameEncoderConfig {
  readonly jpegQuality?: number;
  readonly maximumWidth?: number;
  readonly maximumHeight?: number;
  readonly canvasFactory?: () => HTMLCanvasElement;
}

export interface EncodedFrame {
  readonly blob: Blob;
  readonly width: number;
  readonly height: number;
  readonly size: number;
  readonly mimeType: "image/jpeg";
  readonly capturedAt: number;
}

export enum FrameEncodingErrorCode {
  VIDEO_NOT_READY = "VIDEO_NOT_READY",
  INVALID_SOURCE_DIMENSIONS = "INVALID_SOURCE_DIMENSIONS",
  CANVAS_CONTEXT_UNAVAILABLE = "CANVAS_CONTEXT_UNAVAILABLE",
  DRAW_FAILED = "DRAW_FAILED",
  ENCODING_FAILED = "ENCODING_FAILED",
  INVALID_CONFIGURATION = "INVALID_CONFIGURATION",
}

export class FrameEncodingError extends Error {
  constructor(
    readonly code: FrameEncodingErrorCode,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "FrameEncodingError";
  }
}

export type VideoFrameSource = Pick<
  HTMLVideoElement,
  "videoWidth" | "videoHeight" | "readyState"
>;

const HAVE_CURRENT_DATA = 2;
const DEFAULT_JPEG_QUALITY = 0.8;
const DEFAULT_MAXIMUM_WIDTH = 640;
const DEFAULT_MAXIMUM_HEIGHT = 480;

export interface FrameEncoder {
  readonly jpegQuality: number;
  readonly outputWidth?: number;
  readonly outputHeight?: number;
  setQuality(quality: number): void;
  setOutputDimensions?(width: number, height: number): void;
  encode(source: VideoFrameSource): Promise<EncodedFrame>;
}

export class CanvasFrameEncoder implements FrameEncoder {
  private currentJpegQuality: number;
  private currentOutputWidth: number;
  private currentOutputHeight: number;
  private hasExplicitOutputDimensions = false;
  readonly maximumWidth: number;
  readonly maximumHeight: number;
  private readonly canvasFactory: () => HTMLCanvasElement;
  private canvas: HTMLCanvasElement | null = null;

  constructor(config: FrameEncoderConfig = {}) {
    this.currentJpegQuality = config.jpegQuality ?? DEFAULT_JPEG_QUALITY;
    this.maximumWidth = this.positiveInteger(
      config.maximumWidth ?? DEFAULT_MAXIMUM_WIDTH,
      "maximumWidth",
    );
    this.maximumHeight = this.positiveInteger(
      config.maximumHeight ?? DEFAULT_MAXIMUM_HEIGHT,
      "maximumHeight",
    );
    this.currentOutputWidth = this.maximumWidth;
    this.currentOutputHeight = this.maximumHeight;
    if (
      typeof this.currentJpegQuality !== "number" ||
      !Number.isFinite(this.currentJpegQuality) ||
      this.currentJpegQuality <= 0 ||
      this.currentJpegQuality > 1
    ) {
      throw new FrameEncodingError(
        FrameEncodingErrorCode.INVALID_CONFIGURATION,
        "jpegQuality must be between 0 and 1.",
      );
    }
    this.canvasFactory =
      config.canvasFactory ?? (() => document.createElement("canvas"));
  }

  get jpegQuality(): number {
    return this.currentJpegQuality;
  }
  get outputWidth(): number {
    return this.currentOutputWidth;
  }
  get outputHeight(): number {
    return this.currentOutputHeight;
  }

  setQuality(quality: number): void {
    if (
      typeof quality !== "number" ||
      !Number.isFinite(quality) ||
      quality <= 0 ||
      quality > 1
    )
      throw new FrameEncodingError(
        FrameEncodingErrorCode.INVALID_CONFIGURATION,
        "jpegQuality must be greater than 0 and at most 1.",
      );
    if (quality === this.currentJpegQuality) return;
    this.currentJpegQuality = quality;
  }
  setOutputDimensions(width: number, height: number): void {
    const validatedWidth = this.positiveInteger(width, "width");
    const validatedHeight = this.positiveInteger(height, "height");
    if (
      validatedWidth > this.maximumWidth ||
      validatedHeight > this.maximumHeight
    )
      throw new FrameEncodingError(
        FrameEncodingErrorCode.INVALID_CONFIGURATION,
        "Output dimensions exceed encoder limits.",
      );
    if (
      validatedWidth === this.currentOutputWidth &&
      validatedHeight === this.currentOutputHeight
    )
      return;
    this.currentOutputWidth = validatedWidth;
    this.currentOutputHeight = validatedHeight;
    this.hasExplicitOutputDimensions = true;
  }

  async encode(source: VideoFrameSource): Promise<EncodedFrame> {
    if (source.readyState < HAVE_CURRENT_DATA)
      throw this.error(
        FrameEncodingErrorCode.VIDEO_NOT_READY,
        "Video has no current frame.",
      );
    if (source.videoWidth < 1 || source.videoHeight < 1)
      throw this.error(
        FrameEncodingErrorCode.INVALID_SOURCE_DIMENSIONS,
        "Video dimensions must be positive.",
      );
    const { width, height } = this.hasExplicitOutputDimensions
      ? { width: this.currentOutputWidth, height: this.currentOutputHeight }
      : this.resize(source.videoWidth, source.videoHeight);
    const canvas = this.canvas ?? (this.canvas = this.canvasFactory());
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context)
      throw this.error(
        FrameEncodingErrorCode.CANVAS_CONTEXT_UNAVAILABLE,
        "Canvas context is unavailable.",
      );
    try {
      context.drawImage(source as CanvasImageSource, 0, 0, width, height);
    } catch (cause) {
      throw this.error(
        FrameEncodingErrorCode.DRAW_FAILED,
        "Video frame could not be drawn.",
        cause,
      );
    }
    const quality = this.currentJpegQuality;
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", quality),
    );
    if (!blob || blob.size < 1)
      throw this.error(
        FrameEncodingErrorCode.ENCODING_FAILED,
        "JPEG encoding produced no frame.",
      );
    return Object.freeze({
      blob,
      width,
      height,
      size: blob.size,
      mimeType: "image/jpeg",
      capturedAt: performance.now(),
    });
  }

  private positiveInteger(value: number, name: string): number {
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1)
      throw new FrameEncodingError(
        FrameEncodingErrorCode.INVALID_CONFIGURATION,
        `${name} must be a positive integer.`,
      );
    return value;
  }

  private resize(
    width: number,
    height: number,
  ): { width: number; height: number } {
    const scale = Math.min(
      1,
      this.maximumWidth / width,
      this.maximumHeight / height,
    );
    return {
      width: Math.max(1, Math.round(width * scale)),
      height: Math.max(1, Math.round(height * scale)),
    };
  }

  private error(
    code: FrameEncodingErrorCode,
    message: string,
    cause?: unknown,
  ): FrameEncodingError {
    return new FrameEncodingError(
      code,
      message,
      cause === undefined ? undefined : { cause },
    );
  }
}
