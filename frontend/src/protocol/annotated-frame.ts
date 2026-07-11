export const ANNOTATED_FRAME_MAGIC = "GBF1";
export const ANNOTATED_FRAME_ENVELOPE_VERSION = 1;

const HEADER_SIZE = 20;
const MAXIMUM_JPEG_SIZE = 5 * 1024 * 1024;

export enum AnnotatedFrameMessageKind {
  ANNOTATED_JPEG = 1,
}

export class AnnotatedFrameEnvelopeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AnnotatedFrameEnvelopeError";
  }
}

export interface AnnotatedFrameMessage {
  readonly sequence: number;
  readonly width: number;
  readonly height: number;
  readonly mimeType: "image/jpeg";
  readonly blob: Blob;
  readonly size: number;
}

type BlobWithArrayBuffer = Blob & {
  arrayBuffer?: () => Promise<ArrayBuffer>;
};

function readBlobAsArrayBuffer(blob: Blob): Promise<ArrayBuffer> {
  const readableBlob = blob as BlobWithArrayBuffer;

  if (typeof readableBlob.arrayBuffer === "function") {
    return readableBlob.arrayBuffer.call(blob);
  }

  if (typeof FileReader === "undefined") {
    return Promise.reject(
      new AnnotatedFrameEnvelopeError(
        "Blob input cannot be read in this environment.",
      ),
    );
  }

  return new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();

    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) {
        resolve(reader.result);
        return;
      }

      reject(
        new AnnotatedFrameEnvelopeError(
          "Blob input did not produce an ArrayBuffer.",
        ),
      );
    };

    reader.onerror = () => {
      reject(
        new AnnotatedFrameEnvelopeError(
          reader.error?.message ?? "Failed to read annotated frame Blob.",
        ),
      );
    };

    reader.onabort = () => {
      reject(
        new AnnotatedFrameEnvelopeError(
          "Reading the annotated frame Blob was aborted.",
        ),
      );
    };

    reader.readAsArrayBuffer(blob);
  });
}

export async function decodeAnnotatedFrameEnvelope(
  input: ArrayBuffer | Blob,
): Promise<AnnotatedFrameMessage> {
  const buffer =
    input instanceof Blob ? await readBlobAsArrayBuffer(input) : input;

  if (buffer.byteLength < HEADER_SIZE) {
    throw new AnnotatedFrameEnvelopeError(
      "Annotated frame envelope is truncated.",
    );
  }

  const view = new DataView(buffer);
  const magic = String.fromCharCode(...new Uint8Array(buffer, 0, 4));

  if (magic !== ANNOTATED_FRAME_MAGIC) {
    throw new AnnotatedFrameEnvelopeError("Invalid annotated frame magic.");
  }

  if (view.getUint8(4) !== ANNOTATED_FRAME_ENVELOPE_VERSION) {
    throw new AnnotatedFrameEnvelopeError(
      "Unsupported annotated frame envelope version.",
    );
  }

  if (view.getUint8(5) !== AnnotatedFrameMessageKind.ANNOTATED_JPEG) {
    throw new AnnotatedFrameEnvelopeError("Unsupported annotated frame kind.");
  }

  if (view.getUint16(6, false) !== 0) {
    throw new AnnotatedFrameEnvelopeError(
      "Annotated frame reserved bytes are non-zero.",
    );
  }

  const sequence = view.getUint32(8, false);
  const width = view.getUint16(12, false);
  const height = view.getUint16(14, false);
  const size = view.getUint32(16, false);

  if (
    width === 0 ||
    height === 0 ||
    size === 0 ||
    size > MAXIMUM_JPEG_SIZE ||
    buffer.byteLength !== HEADER_SIZE + size
  ) {
    throw new AnnotatedFrameEnvelopeError(
      "Invalid annotated JPEG payload length.",
    );
  }

  const payload = buffer.slice(HEADER_SIZE);

  return Object.freeze({
    sequence,
    width,
    height,
    mimeType: "image/jpeg",
    blob: new Blob([payload], { type: "image/jpeg" }),
    size,
  });
}
