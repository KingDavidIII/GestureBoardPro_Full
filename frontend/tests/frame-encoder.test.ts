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
});
