import { describe, expect, it, vi } from "vitest";

import { ResourceCleanupError } from "../src/lifecycle/resource-cleanup";
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

function deferred<T>(): {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
  readonly reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
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

  it("cancels pending acquisition and disposes a late media stream", async () => {
    const request = deferred<MediaStream>();
    const mediaStream = stream();
    const track = mediaStream.getTracks()[0] as MediaStreamTrack & {
      stop: ReturnType<typeof vi.fn>;
    };
    const camera = new CameraController({
      mediaDevices: { getUserMedia: vi.fn(() => request.promise) },
    });
    const errors: unknown[] = [];
    camera.subscribe((event) => {
      if (event.type === "error") errors.push(event.error);
    });

    const starting = camera.start();
    const cancelled = expect(starting).rejects.toMatchObject({
      code: CameraErrorCode.CAMERA_START_CANCELLED,
    });
    expect(camera.getState()).toBe(CameraState.REQUESTING_PERMISSION);

    camera.stop();
    expect(camera.getState()).toBe(CameraState.STOPPED);
    request.resolve(mediaStream);
    await cancelled;

    expect(track.stop).toHaveBeenCalledOnce();
    expect(camera.getStream()).toBeNull();
    expect(camera.getMetadata()).toBeNull();
    expect(camera.getState()).toBe(CameraState.STOPPED);
    expect(errors).toEqual([]);
  });

  it("does not let an older acquisition overwrite a later successful start", async () => {
    const firstRequest = deferred<MediaStream>();
    const secondRequest = deferred<MediaStream>();
    const firstStream = stream();
    const secondStream = stream();
    const firstTrack = firstStream.getTracks()[0] as MediaStreamTrack & {
      stop: ReturnType<typeof vi.fn>;
    };
    const secondTrack = secondStream.getTracks()[0] as MediaStreamTrack & {
      stop: ReturnType<typeof vi.fn>;
    };
    const getUserMedia = vi
      .fn()
      .mockImplementationOnce(() => firstRequest.promise)
      .mockImplementationOnce(() => secondRequest.promise);
    const camera = new CameraController({
      mediaDevices: { getUserMedia },
    });

    const firstStart = camera.start();
    const firstCancelled = expect(firstStart).rejects.toMatchObject({
      code: CameraErrorCode.CAMERA_START_CANCELLED,
    });
    camera.stop();

    const secondStart = camera.start();
    secondRequest.resolve(secondStream);
    await expect(secondStart).resolves.toMatchObject({
      width: 800,
      height: 600,
    });

    firstRequest.resolve(firstStream);
    await firstCancelled;

    expect(firstTrack.stop).toHaveBeenCalledOnce();
    expect(secondTrack.stop).not.toHaveBeenCalled();
    expect(camera.getStream()).toBe(secondStream);
    expect(camera.getState()).toBe(CameraState.READY);
  });

  it("releases acquired media when preview playback fails during startup", async () => {
    const mediaStream = stream();
    const track = mediaStream.getTracks()[0] as MediaStreamTrack & {
      stop: ReturnType<typeof vi.fn>;
    };
    const video = preview();
    video.play.mockRejectedValueOnce(
      new DOMException("blocked", "NotAllowedError"),
    );
    const camera = new CameraController({
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(mediaStream) },
    });
    await camera.attachPreview(video);

    await expect(camera.start()).rejects.toMatchObject({
      code: CameraErrorCode.CAMERA_PLAYBACK_FAILED,
    });

    expect(track.stop).toHaveBeenCalledOnce();
    expect(video.srcObject).toBeNull();
    expect(camera.getStream()).toBeNull();
    expect(camera.getMetadata()).toBeNull();
    expect(camera.getState()).toBe(CameraState.ERROR);
  });

  it("releases a ready camera when a newly attached preview cannot play", async () => {
    const mediaStream = stream();
    const track = mediaStream.getTracks()[0] as MediaStreamTrack & {
      stop: ReturnType<typeof vi.fn>;
    };
    const camera = new CameraController({
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(mediaStream) },
    });
    await camera.start();
    const video = preview();
    video.play.mockRejectedValueOnce(new Error("playback failed"));

    await expect(camera.attachPreview(video)).rejects.toMatchObject({
      code: CameraErrorCode.CAMERA_PLAYBACK_FAILED,
    });

    expect(track.stop).toHaveBeenCalledOnce();
    expect(video.srcObject).toBeNull();
    expect(camera.getStream()).toBeNull();
    expect(camera.getState()).toBe(CameraState.ERROR);
  });

  it("destroys a ready camera terminally without retaining preview or listeners", async () => {
    const mediaStream = stream();
    const track = mediaStream.getTracks()[0] as MediaStreamTrack & {
      stop: ReturnType<typeof vi.fn>;
    };
    const camera = new CameraController({
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(mediaStream) },
    });
    const video = preview();
    const listener = vi.fn();

    camera.subscribe(listener);
    await camera.attachPreview(video);
    await camera.start();
    const eventCount = listener.mock.calls.length;

    camera.destroy();
    camera.destroy();

    expect(track.stop).toHaveBeenCalledOnce();
    expect(video.srcObject).toBeNull();
    expect(camera.getPreview()).toBeNull();
    expect(camera.getStream()).toBeNull();
    expect(camera.getMetadata()).toBeNull();
    expect(camera.getState()).toBe(CameraState.STOPPED);
    expect(listener).toHaveBeenCalledTimes(eventCount);

    const lateListener = vi.fn();
    const lateUnsubscribe = camera.subscribe(lateListener);
    camera.stop();
    lateUnsubscribe();

    expect(lateListener).not.toHaveBeenCalled();
    await expect(camera.start()).rejects.toMatchObject({
      code: CameraErrorCode.INVALID_STATE,
      message: "Camera controller has been destroyed.",
    });
    await expect(camera.attachPreview(preview())).rejects.toMatchObject({
      code: CameraErrorCode.INVALID_STATE,
      message: "Camera controller has been destroyed.",
    });
    expect(camera.getPreview()).toBeNull();
  });

  it("cancels pending acquisition when destroyed and releases the late stream", async () => {
    const request = deferred<MediaStream>();
    const mediaStream = stream();
    const track = mediaStream.getTracks()[0] as MediaStreamTrack & {
      stop: ReturnType<typeof vi.fn>;
    };
    const camera = new CameraController({
      mediaDevices: { getUserMedia: vi.fn(() => request.promise) },
    });
    const listener = vi.fn();
    camera.subscribe(listener);

    const starting = camera.start();
    const cancelled = expect(starting).rejects.toMatchObject({
      code: CameraErrorCode.CAMERA_START_CANCELLED,
    });
    expect(camera.getState()).toBe(CameraState.REQUESTING_PERMISSION);
    const eventCount = listener.mock.calls.length;

    camera.destroy();
    request.resolve(mediaStream);
    await cancelled;

    expect(track.stop).toHaveBeenCalledOnce();
    expect(camera.getStream()).toBeNull();
    expect(camera.getMetadata()).toBeNull();
    expect(camera.getPreview()).toBeNull();
    expect(camera.getState()).toBe(CameraState.STOPPED);
    expect(listener).toHaveBeenCalledTimes(eventCount);
    expect(() => camera.destroy()).not.toThrow();
  });

  it("remains terminal when media-track release fails during destruction", async () => {
    const mediaStream = stream();
    const track = mediaStream.getTracks()[0] as MediaStreamTrack & {
      stop: ReturnType<typeof vi.fn>;
    };
    const releaseFailure = new Error("track release failed");
    track.stop.mockImplementationOnce(() => {
      throw releaseFailure;
    });
    const camera = new CameraController({
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(mediaStream) },
    });
    const video = preview();
    const listener = vi.fn();

    camera.subscribe(listener);
    await camera.attachPreview(video);
    await camera.start();
    const eventCount = listener.mock.calls.length;

    let cleanupError: unknown;
    try {
      camera.destroy();
    } catch (error) {
      cleanupError = error;
    }

    expect(cleanupError).toBeInstanceOf(ResourceCleanupError);
    expect((cleanupError as ResourceCleanupError).failures).toEqual([
      { operation: "camera.stop", error: releaseFailure },
    ]);
    expect(track.stop).toHaveBeenCalledOnce();
    expect(video.srcObject).toBeNull();
    expect(camera.getPreview()).toBeNull();
    expect(camera.getStream()).toBeNull();
    expect(camera.getMetadata()).toBeNull();
    expect(camera.getState()).toBe(CameraState.STOPPED);
    expect(listener).toHaveBeenCalledTimes(eventCount);
    expect(() => camera.destroy()).not.toThrow();
  });
});
