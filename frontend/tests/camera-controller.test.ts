import { describe, expect, it, vi } from "vitest";

import {
  CameraController,
  CameraErrorCode,
  CameraState,
  type PreviewVideoElement,
} from "../src/camera";

function stream(live = true): MediaStream {
  const track = {
    readyState: live ? "live" : "ended",
    stop: vi.fn(),
    getSettings: () => ({
      width: 800,
      height: 600,
      frameRate: 30,
      facingMode: "user",
    }),
  } as unknown as MediaStreamTrack;
  return {
    getVideoTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
}

function preview(): PreviewVideoElement & { play: ReturnType<typeof vi.fn> } {
  return {
    srcObject: null,
    videoWidth: 800,
    videoHeight: 600,
    readyState: 4,
    play: vi.fn().mockResolvedValue(undefined),
  };
}

describe("CameraController", () => {
  it("does not request camera access until explicitly started and forwards ideal video constraints", async () => {
    const getUserMedia = vi.fn().mockResolvedValue(stream());
    const camera = new CameraController({
      preferredWidth: 800,
      preferredHeight: 600,
      preferredFrameRate: 10,
      facingMode: "environment",
      mediaDevices: { getUserMedia },
    });
    expect(getUserMedia).not.toHaveBeenCalled();
    await camera.start();
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: false,
      video: {
        width: { ideal: 800 },
        height: { ideal: 600 },
        frameRate: { ideal: 10 },
        facingMode: { ideal: "environment" },
      },
    });
    expect(camera.getState()).toBe(CameraState.READY);
    expect(camera.getMetadata()).toMatchObject({ width: 800, height: 600 });
  });

  it("attaches, plays, then releases a preview and tracks safely", async () => {
    const mediaStream = stream();
    const track = mediaStream.getTracks()[0] as MediaStreamTrack & {
      stop: ReturnType<typeof vi.fn>;
    };
    const camera = new CameraController({
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(mediaStream) },
    });
    const video = preview();
    await camera.attachPreview(video);
    await camera.start();
    expect(video.srcObject).toBe(mediaStream);
    expect(video.play).toHaveBeenCalledOnce();
    camera.stop();
    camera.stop();
    expect(track.stop).toHaveBeenCalledOnce();
    expect(video.srcObject).toBeNull();
    expect(camera.getState()).toBe(CameraState.STOPPED);
  });

  it("maps denied permission and isolates failing subscribers", async () => {
    const handler = vi.fn();
    const getUserMedia = vi
      .fn()
      .mockRejectedValue(new DOMException("denied", "NotAllowedError"));
    const camera = new CameraController({
      mediaDevices: { getUserMedia },
      subscriberErrorHandler: handler,
    });
    const received = vi.fn();
    camera.subscribe(() => {
      throw new Error("listener");
    });
    camera.subscribe(received);
    await expect(camera.start()).rejects.toMatchObject({
      code: CameraErrorCode.PERMISSION_DENIED,
    });
    expect(received).toHaveBeenCalled();
    expect(handler).toHaveBeenCalled();
  });
});
