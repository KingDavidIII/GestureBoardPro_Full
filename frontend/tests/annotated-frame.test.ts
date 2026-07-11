import { describe, expect, it } from "vitest";

import {
  AnnotatedFrameEnvelopeError,
  decodeAnnotatedFrameEnvelope,
} from "../src/protocol";

function envelope(
  overrides: Partial<{
    magic: string;
    version: number;
    kind: number;
    reserved: number;
    width: number;
    height: number;
    length: number;
  }> = {},
): ArrayBuffer {
  const payload = new Uint8Array([1, 2, 3]);
  const view = new DataView(new ArrayBuffer(20 + payload.length));

  for (const [index, value] of [...(overrides.magic ?? "GBF1")].entries()) {
    view.setUint8(index, value.charCodeAt(0));
  }

  view.setUint8(4, overrides.version ?? 1);
  view.setUint8(5, overrides.kind ?? 1);
  view.setUint16(6, overrides.reserved ?? 0);
  view.setUint32(8, 42);
  view.setUint16(12, overrides.width ?? 640);
  view.setUint16(14, overrides.height ?? 480);
  view.setUint32(16, overrides.length ?? payload.length);

  new Uint8Array(view.buffer, 20).set(payload);

  return view.buffer;
}

function readBlobAsArrayBuffer(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) {
        resolve(reader.result);
        return;
      }

      reject(new TypeError("Expected FileReader to return an ArrayBuffer."));
    };

    reader.onerror = () => {
      reject(reader.error ?? new Error("Failed to read Blob."));
    };

    reader.readAsArrayBuffer(blob);
  });
}

describe("decodeAnnotatedFrameEnvelope", () => {
  it("decodes an immutable GBF1 JPEG ArrayBuffer and Blob", async () => {
    const source = envelope();
    const frame = await decodeAnnotatedFrameEnvelope(source);

    expect(frame).toMatchObject({
      sequence: 42,
      width: 640,
      height: 480,
      size: 3,
      mimeType: "image/jpeg",
    });

    const decodedPayload = await readBlobAsArrayBuffer(frame.blob);

    expect([...new Uint8Array(decodedPayload)]).toEqual([1, 2, 3]);

    await expect(
      decodeAnnotatedFrameEnvelope(new Blob([source])),
    ).resolves.toMatchObject({
      sequence: 42,
    });

    expect(() => {
      (frame as { width: number }).width = 1;
    }).toThrow();
  });

  it.each([
    [{ magic: "BAD!" }],
    [{ version: 2 }],
    [{ kind: 2 }],
    [{ reserved: 1 }],
    [{ width: 0 }],
    [{ height: 0 }],
    [{ length: 0 }],
    [{ length: 2 }],
  ])("rejects malformed headers %#", async (overrides) => {
    await expect(
      decodeAnnotatedFrameEnvelope(envelope(overrides)),
    ).rejects.toBeInstanceOf(AnnotatedFrameEnvelopeError);
  });

  it("rejects truncated and oversized envelopes", async () => {
    await expect(
      decodeAnnotatedFrameEnvelope(new ArrayBuffer(19)),
    ).rejects.toBeInstanceOf(AnnotatedFrameEnvelopeError);

    await expect(
      decodeAnnotatedFrameEnvelope(
        envelope({
          length: 6 * 1024 * 1024,
        }),
      ),
    ).rejects.toBeInstanceOf(AnnotatedFrameEnvelopeError);
  });
});
