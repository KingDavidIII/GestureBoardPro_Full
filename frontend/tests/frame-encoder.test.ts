import { describe, expect, it, vi } from "vitest";

import { CanvasFrameEncoder, FrameEncodingError } from "../src/camera";

function canvas(
  blob: Blob | null = new Blob(["jpeg"], { type: "image/jpeg" }),
) {
  const drawImage = vi.fn();
  return {
    width: 0,
    height: 0,
    getContext: vi.fn().mockReturnValue({ drawImage }),
    toBlob: (callback: BlobCallback) => callback(blob),
  } as unknown as HTMLCanvasElement;
}
const source = (width = 800, height = 400, readyState = 4) => ({
  videoWidth: width,
  videoHeight: height,
  readyState,
});

describe("CanvasFrameEncoder", () => {
  it("encodes reusable JPEG canvases with proportional downscaling", async () => {
    const element = canvas();
    const factory = vi.fn(() => element);
    const encoder = new CanvasFrameEncoder({
      jpegQuality: 0.75,
      maximumWidth: 400,
      maximumHeight: 400,
      canvasFactory: factory,
    });
    const frame = await encoder.encode(source());
    const second = await encoder.encode(source(200, 100));
    expect(frame).toMatchObject({
      width: 400,
      height: 200,
      size: 4,
      mimeType: "image/jpeg",
    });
    expect(second.width).toBe(200);
    expect(factory).toHaveBeenCalledOnce();
  });

  it("rejects unavailable frames, contexts, and empty encodes", async () => {
    await expect(
      new CanvasFrameEncoder({ canvasFactory: () => canvas() }).encode(
        source(0, 1),
      ),
    ).rejects.toBeInstanceOf(FrameEncodingError);
    await expect(
      new CanvasFrameEncoder({ canvasFactory: () => canvas() }).encode(
        source(1, 1, 1),
      ),
    ).rejects.toBeInstanceOf(FrameEncodingError);
    await expect(
      new CanvasFrameEncoder({
        canvasFactory: () => canvas(new Blob()),
      }).encode(source()),
    ).rejects.toBeInstanceOf(FrameEncodingError);
  });

  it("updates quality for future encodes while reusing dimensions and canvas", async () => {
    const qualities: number[] = [];
    const element = canvas();
    element.toBlob = ((
      callback: BlobCallback,
      type?: string,
      quality?: number,
    ) => {
      qualities.push(quality ?? -1);
      callback(new Blob(["jpeg"], { type: type ?? "image/jpeg" }));
    }) as typeof element.toBlob;
    const factory = vi.fn(() => element);
    const encoder = new CanvasFrameEncoder({
      jpegQuality: 0.8,
      canvasFactory: factory,
    });
    encoder.setQuality(0.7);
    const first = await encoder.encode(source());
    encoder.setQuality(0.6);
    const second = await encoder.encode(source());
    expect(qualities).toEqual([0.7, 0.6]);
    expect(first).toMatchObject({
      mimeType: "image/jpeg",
      width: 640,
      height: 320,
    });
    expect(second.width).toBe(first.width);
    expect(factory).toHaveBeenCalledOnce();
  });

  it("captures quality when an in-flight encode begins", async () => {
    const callbacks: BlobCallback[] = [];
    const qualities: number[] = [];
    const element = canvas();
    element.toBlob = ((
      callback: BlobCallback,
      _type?: string,
      quality?: number,
    ) => {
      callbacks.push(callback);
      qualities.push(quality ?? -1);
    }) as typeof element.toBlob;
    const encoder = new CanvasFrameEncoder({
      jpegQuality: 0.8,
      canvasFactory: () => element,
    });
    const pending = encoder.encode(source());
    encoder.setQuality(0.5);
    callbacks[0]?.(new Blob(["jpeg"], { type: "image/jpeg" }));
    await pending;
    expect(qualities).toEqual([0.8]);
    expect(encoder.jpegQuality).toBe(0.5);
  });

  it("treats same-value updates as idempotent", () => {
    const encoder = new CanvasFrameEncoder({ jpegQuality: 0.8 });
    encoder.setQuality(0.8);
    expect(encoder.jpegQuality).toBe(0.8);
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY, 0, -0.1, 1.1])(
    "rejects invalid runtime quality %s",
    (quality) => {
      const encoder = new CanvasFrameEncoder();
      expect(() => encoder.setQuality(quality)).toThrow(FrameEncodingError);
    },
  );
});
