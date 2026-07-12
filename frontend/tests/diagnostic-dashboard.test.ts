import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CameraState, type CameraController } from "../src/camera";
import { DiagnosticDashboard, type ObjectUrlApi } from "../src/dashboard";
import {
  FrameStreamState,
  type AdaptiveStreamSnapshot,
  type FrameStreamController,
} from "../src/streaming";
import {
  GestureWebSocketClient,
  type GestureWebSocketClientEvent,
  type ReconnectTimerApi,
} from "../src/websocket";
import { FakeWebSocket } from "./fake-websocket";

function annotatedEnvelope(
  sequence: number,
  width: number,
  height: number,
  bytes: readonly number[],
): ArrayBuffer {
  const view = new DataView(new ArrayBuffer(20 + bytes.length));
  for (const [index, value] of [..."GBF1"].entries())
    view.setUint8(index, value.charCodeAt(0));
  view.setUint8(4, 1);
  view.setUint8(5, 1);
  view.setUint16(6, 0, false);
  view.setUint32(8, sequence, false);
  view.setUint16(12, width, false);
  view.setUint16(14, height, false);
  view.setUint32(16, bytes.length, false);
  new Uint8Array(view.buffer, 20).set(bytes);
  return view.buffer;
}

class DashboardReconnectTimers implements ReconnectTimerApi {
  readonly callbacks: Array<() => void> = [];
  set(callback: () => void): unknown {
    this.callbacks.push(callback);
    return callback;
  }
  clear(handle: unknown): void {
    const index = this.callbacks.indexOf(handle as () => void);
    if (index >= 0) this.callbacks.splice(index, 1);
  }
  runNext(): void {
    const callback = this.callbacks.shift();
    if (!callback) throw new Error("No reconnect callback is pending.");
    callback();
  }
}

describe("DiagnosticDashboard", () => {
  let root: HTMLDivElement;
  let socket: FakeWebSocket;
  let dashboard: DiagnosticDashboard;
  let client: GestureWebSocketClient;
  let createObjectURL: ReturnType<typeof vi.fn<(blob: Blob) => string>>;
  let revokeObjectURL: ReturnType<typeof vi.fn<(url: string) => void>>;
  let cameraStop: ReturnType<typeof vi.fn>;
  let streamStop: ReturnType<typeof vi.fn>;
  let cameraDetach: ReturnType<typeof vi.fn>;
  let streamState: FrameStreamState;
  let reconnectTimers: DashboardReconnectTimers;
  let sockets: FakeWebSocket[];
  let adaptiveSnapshot: AdaptiveStreamSnapshot;
  let adaptiveListeners: Array<(snapshot: AdaptiveStreamSnapshot) => void>;
  let adaptiveReset: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    root = document.createElement("div");
    document.body.append(root);
    socket = new FakeWebSocket();
    sockets = [];
    reconnectTimers = new DashboardReconnectTimers();
    client = new GestureWebSocketClient("ws://board.test/ws/", {
      socketFactory: () => {
        const next = sockets.length === 0 ? socket : new FakeWebSocket();
        sockets.push(next);
        return next;
      },
      reconnectTimers,
      random: () => 0.5,
      reconnectPolicy: {
        initialDelayMs: 100,
        maximumDelayMs: 200,
        multiplier: 2,
        maximumAttempts: 2,
        jitterRatio: 0,
      },
    });
    let objectUrlSequence = 0;
    createObjectURL = vi.fn(() => {
      objectUrlSequence += 1;
      return `blob:annotation-${objectUrlSequence}`;
    });
    revokeObjectURL = vi.fn();
    cameraStop = vi.fn();
    cameraDetach = vi.fn();
    streamStop = vi.fn(() => {
      streamState = FrameStreamState.STOPPED;
    });
    streamState = FrameStreamState.IDLE;
    adaptiveSnapshot = Object.freeze({
      mode: "adaptive",
      targetFps: 8,
      minimumFps: 5,
      maximumFps: 8,
      latestDecision: null,
      healthySamples: 0,
      overloadSamples: 0,
      estimatedCapacityFps: null,
      cooldownActive: false,
    });
    adaptiveListeners = [];
    adaptiveReset = vi.fn();
    const camera = {
      getState: () => CameraState.IDLE,
      getMetadata: () => null,
      attachPreview: vi.fn(async () => undefined),
      detachPreview: cameraDetach,
      start: vi.fn(async () => ({
        width: null,
        height: null,
        frameRate: null,
        facingMode: null,
      })),
      stop: cameraStop,
      subscribe: vi.fn(() => vi.fn()),
    } as unknown as CameraController;
    const stream = {
      targetFps: 8,
      getState: () => streamState,
      getMetrics: () => ({
        startedAt: null,
        stoppedAt: null,
        framesAttempted: 0,
        framesEncoded: 0,
        framesSent: 0,
        framesDroppedForBackpressure: 0,
        framesDroppedForTiming: 0,
        encodingFailures: 0,
        sendFailures: 0,
        lastFrameSize: null,
        lastFrameWidth: null,
        lastFrameHeight: null,
        effectiveFps: 0,
      }),
      start: vi.fn(() => {
        streamState = FrameStreamState.STREAMING;
      }),
      stop: streamStop,
      subscribe: vi.fn(() => vi.fn()),
    } as unknown as FrameStreamController;
    dashboard = new DiagnosticDashboard(root, client, {
      camera,
      stream,
      objectUrls: {
        createObjectURL,
        revokeObjectURL,
      } satisfies ObjectUrlApi,
      adaptive: {
        getSnapshot: () => adaptiveSnapshot,
        setMode: vi.fn((mode) => {
          adaptiveSnapshot = Object.freeze({
            ...adaptiveSnapshot,
            mode,
            latestDecision: null,
            healthySamples: 0,
            overloadSamples: 0,
          });
          for (const listener of adaptiveListeners) listener(adaptiveSnapshot);
        }),
        reset: adaptiveReset,
        subscribe: (listener) => {
          adaptiveListeners.push(listener);
          return () => {
            adaptiveListeners = adaptiveListeners.filter(
              (candidate) => candidate !== listener,
            );
          };
        },
      },
    });
  });

  const updateAdaptive = (update: Partial<AdaptiveStreamSnapshot>): void => {
    adaptiveSnapshot = Object.freeze({ ...adaptiveSnapshot, ...update });
    for (const listener of adaptiveListeners) listener(adaptiveSnapshot);
  };

  afterEach(() => {
    dashboard.destroy();
    document.body.replaceChildren();
    vi.restoreAllMocks();
  });

  async function connectAndEnableAnnotations(): Promise<void> {
    root.querySelector<HTMLButtonElement>('[data-action="connect"]')?.click();
    socket.open();
    await Promise.resolve();
    socket.message(
      '{"protocol_version":1,"type":"connection.ready","capabilities":["annotated_frame.jpeg.v1"]}',
    );
    root
      .querySelector<HTMLButtonElement>('[data-action="annotation"]')
      ?.click();
    socket.message(
      '{"protocol_version":1,"type":"annotated_frame.set.ack","enabled":true}',
    );
  }

  async function emitAnnotatedFrame(
    sequence: number,
    width: number,
    height: number,
    bytes: readonly number[],
  ): Promise<Blob> {
    let acceptedBlob: Blob | null = null;
    const unsubscribe = client.subscribe(
      (event: GestureWebSocketClientEvent) => {
        if (event.type === "annotated-frame") acceptedBlob = event.frame.blob;
      },
    );
    socket.message(annotatedEnvelope(sequence, width, height, bytes));
    await Promise.resolve();
    await Promise.resolve();
    unsubscribe();
    if (!acceptedBlob) throw new Error("Annotated frame was not accepted.");
    return acceptedBlob;
  }

  const annotationImage = (): HTMLImageElement => {
    const image = root.querySelector<HTMLImageElement>(".annotated-preview");
    if (!image) throw new Error("Annotated preview is missing.");
    return image;
  };

  const annotationDiagnostics = (): string =>
    root.querySelector(".annotation-diagnostics")?.textContent ?? "";

  const annotationStatus = (): string =>
    root.querySelector(".annotation-status")?.textContent ?? "";

  it("renders accessible connection controls and reacts to lifecycle events", async () => {
    const status = root.querySelector("output");
    const connect = root.querySelector<HTMLButtonElement>(
      '[data-action="connect"]',
    );
    const ping = root.querySelector<HTMLButtonElement>('[data-action="ping"]');

    expect(status?.getAttribute("aria-live")).toBe("polite");
    expect(connect?.disabled).toBe(false);
    expect(ping?.disabled).toBe(true);

    connect?.click();
    socket.open();
    await Promise.resolve();

    expect(status?.textContent).toContain("OPEN");
    expect(ping?.disabled).toBe(false);
  });

  it("shows received protocol messages in the log", async () => {
    root.querySelector<HTMLButtonElement>('[data-action="connect"]')?.click();
    socket.open();
    await Promise.resolve();
    socket.message('{"protocol_version":1,"type":"connection.ready"}');

    expect(root.querySelector(".message-log")?.textContent).toContain(
      "connection.ready",
    );
  });

  it("cleans up its DOM on destruction", () => {
    dashboard.destroy();

    expect(root.childElementCount).toBe(0);
  });

  it("renders separate accessible annotation and camera previews", () => {
    const video = root.querySelector("video");
    const image = root.querySelector("img");
    const annotation = root.querySelector<HTMLButtonElement>(
      '[data-action="annotation"]',
    );
    expect(video).not.toBeNull();
    expect(image).not.toBe(video);
    expect(image?.getAttribute("alt")).toContain("annotated");
    expect(annotation?.getAttribute("aria-label")).toContain("annotated");
    expect(annotation?.disabled).toBe(true);
  });

  it("gates annotation controls on an advertised capability and acknowledgement", async () => {
    const control = root.querySelector<HTMLButtonElement>(
      '[data-action="annotation"]',
    );
    root.querySelector<HTMLButtonElement>('[data-action="connect"]')?.click();
    socket.open();
    await Promise.resolve();
    socket.message(
      '{"protocol_version":1,"type":"connection.ready","capabilities":["annotated_frame.jpeg.v1"]}',
    );
    expect(control?.disabled).toBe(false);
    control?.click();
    expect(socket.sent).toContain(
      '{"protocol_version":1,"type":"annotated_frame.set","enabled":true}',
    );
    socket.message(
      '{"protocol_version":1,"type":"annotated_frame.set.ack","enabled":true}',
    );
    expect(root.querySelector(".annotation-status")?.textContent).toContain(
      "enabled",
    );
  });

  it("creates the first annotated preview only after an accepted frame", async () => {
    const localVideo = root.querySelector<HTMLVideoElement>(".camera-preview");
    const localStream = Object.freeze({ id: "local-camera" });
    if (localVideo)
      localVideo.srcObject = localStream as unknown as MediaProvider;

    expect(createObjectURL).not.toHaveBeenCalled();
    root.querySelector<HTMLButtonElement>('[data-action="connect"]')?.click();
    socket.open();
    await Promise.resolve();
    expect(createObjectURL).not.toHaveBeenCalled();
    socket.message(
      '{"protocol_version":1,"type":"connection.ready","capabilities":["annotated_frame.jpeg.v1"]}',
    );
    expect(createObjectURL).not.toHaveBeenCalled();
    root
      .querySelector<HTMLButtonElement>('[data-action="annotation"]')
      ?.click();
    socket.message(
      '{"protocol_version":1,"type":"annotated_frame.set.ack","enabled":true}',
    );
    expect(createObjectURL).not.toHaveBeenCalled();

    const blob = await emitAnnotatedFrame(7, 640, 360, [1, 2, 3, 4]);

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    expect(annotationImage().getAttribute("src")).toBe("blob:annotation-1");
    expect(root.querySelector(".camera-preview")).not.toBe(annotationImage());
    expect(localVideo?.srcObject).toBe(localStream);
    expect(annotationDiagnostics()).toContain("Sequence 7");
    expect(annotationDiagnostics()).toContain("640×360");
    expect(annotationDiagnostics()).toContain("4 bytes");
    expect(annotationStatus()).toContain("frame available");
  });

  it("replaces a frame after revoking only its stale object URL", async () => {
    await connectAndEnableAnnotations();
    const firstBlob = await emitAnnotatedFrame(10, 320, 180, [1, 2]);
    const firstCreateOrder = createObjectURL.mock.invocationCallOrder[0];
    expect(annotationImage().getAttribute("src")).toBe("blob:annotation-1");

    const secondBlob = await emitAnnotatedFrame(11, 800, 450, [3, 4, 5, 6, 7]);

    expect(createObjectURL.mock.calls).toEqual([[firstBlob], [secondBlob]]);
    expect(revokeObjectURL.mock.calls).toEqual([["blob:annotation-1"]]);
    expect(annotationImage().getAttribute("src")).toBe("blob:annotation-2");
    expect(annotationDiagnostics()).toContain("Sequence 11");
    expect(annotationDiagnostics()).toContain("800×450");
    expect(annotationDiagnostics()).toContain("5 bytes");
    expect(annotationDiagnostics()).not.toContain("Sequence 10");
    expect(revokeObjectURL).not.toHaveBeenCalledWith("blob:annotation-2");
    expect(revokeObjectURL.mock.invocationCallOrder[0]).toBeGreaterThan(
      firstCreateOrder ?? 0,
    );
    expect(revokeObjectURL.mock.invocationCallOrder[0]).toBeLessThan(
      createObjectURL.mock.invocationCallOrder[1] ?? Number.MAX_SAFE_INTEGER,
    );
  });

  it("cleans the preview idempotently after annotation is disabled", async () => {
    await connectAndEnableAnnotations();
    await emitAnnotatedFrame(2, 400, 240, [1, 2, 3]);
    const streamStopsBeforeDisable = streamStop.mock.calls.length;

    socket.message(
      '{"protocol_version":1,"type":"annotated_frame.set.ack","enabled":false}',
    );

    expect(revokeObjectURL.mock.calls).toEqual([["blob:annotation-1"]]);
    expect(annotationImage().hasAttribute("src")).toBe(false);
    expect(annotationDiagnostics()).toBe("No annotated frame received.");
    expect(annotationStatus()).toContain("disabled");
    expect(client.getState()).toBe("OPEN");
    expect(cameraStop).not.toHaveBeenCalled();
    expect(streamStop).toHaveBeenCalledTimes(streamStopsBeforeDisable);

    socket.message(
      '{"protocol_version":1,"type":"annotated_frame.set.ack","enabled":false}',
    );
    expect(revokeObjectURL).toHaveBeenCalledOnce();
  });

  it("cleans annotation and capability state on explicit disconnect", async () => {
    await connectAndEnableAnnotations();
    await emitAnnotatedFrame(3, 500, 300, [1, 2, 3]);
    streamState = FrameStreamState.STREAMING;

    root
      .querySelector<HTMLButtonElement>('[data-action="disconnect"]')
      ?.click();

    const control = root.querySelector<HTMLButtonElement>(
      '[data-action="annotation"]',
    );
    expect(revokeObjectURL.mock.calls).toEqual([["blob:annotation-1"]]);
    expect(annotationImage().hasAttribute("src")).toBe(false);
    expect(control?.disabled).toBe(true);
    expect(client.getAnnotatedFramesEnabled()).toBe(false);
    expect(annotationStatus()).toContain("unavailable");
    expect(streamStop).toHaveBeenCalled();

    socket.remoteClose();
    expect(revokeObjectURL).toHaveBeenCalledOnce();
  });

  it("cleans annotation and stops streaming after a remote close", async () => {
    await connectAndEnableAnnotations();
    await emitAnnotatedFrame(4, 600, 320, [1, 2, 3]);
    streamState = FrameStreamState.STREAMING;

    socket.remoteClose(1006, "network lost");

    expect(revokeObjectURL.mock.calls).toEqual([["blob:annotation-1"]]);
    expect(annotationImage().hasAttribute("src")).toBe(false);
    expect(
      root.querySelector<HTMLButtonElement>('[data-action="annotation"]')
        ?.disabled,
    ).toBe(true);
    expect(client.getAnnotatedFramesEnabled()).toBe(false);
    expect(annotationStatus()).toContain("unavailable");
    expect(streamStop).toHaveBeenCalled();

    socket.remoteClose(1006, "duplicate");
    expect(revokeObjectURL).toHaveBeenCalledOnce();
  });

  it("revokes the preview and unsubscribes idempotently on destruction", async () => {
    await connectAndEnableAnnotations();
    await emitAnnotatedFrame(5, 640, 480, [1, 2, 3]);

    dashboard.destroy();
    dashboard.destroy();

    expect(revokeObjectURL.mock.calls).toEqual([["blob:annotation-1"]]);
    expect(root.childElementCount).toBe(0);
    expect(cameraDetach).toHaveBeenCalledOnce();
    socket.message(annotatedEnvelope(6, 640, 480, [4, 5, 6]));
    socket.remoteClose();
    await Promise.resolve();
    await Promise.resolve();
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledOnce();
    expect(root.childElementCount).toBe(0);
  });

  it("can be destroyed safely before any annotated frame arrives", () => {
    expect(() => {
      dashboard.destroy();
      dashboard.destroy();
    }).not.toThrow();
    expect(revokeObjectURL).not.toHaveBeenCalled();
  });

  it("renders annotation failures as bounded plain text", async () => {
    const malicious = "<img src=x onerror=alert(1)>";
    await connectAndEnableAnnotations();
    socket.message(
      JSON.stringify({
        protocol_version: 1,
        type: "error",
        error: {
          code: "internal_error",
          message: "Annotation encoding unavailable",
        },
      }),
    );
    socket.message(new ArrayBuffer(2));
    await Promise.resolve();
    await Promise.resolve();
    expect(root.querySelector(".message-log")?.textContent).toContain(
      "Annotation encoding unavailable",
    );
    expect(root.querySelector(".message-log")?.textContent).toContain(
      "INVALID_PROTOCOL_MESSAGE",
    );
    vi.spyOn(client, "setAnnotatedFramesEnabled").mockImplementation(() => {
      throw new Error(malicious);
    });
    root
      .querySelector<HTMLButtonElement>('[data-action="annotation"]')
      ?.click();
    for (let index = 0; index < 55; index += 1) {
      socket.message(
        JSON.stringify({
          protocol_version: 1,
          type: "error",
          error: { code: "internal_error", message: malicious },
        }),
      );
    }

    const log = root.querySelector(".message-log");
    expect(log?.textContent).toContain(malicious);
    expect(log?.children.length).toBe(50);
    expect(root.querySelectorAll("img").length).toBe(1);
    expect(root.querySelector("[onerror]")).toBeNull();
    expect(log?.querySelector("img")).toBeNull();
    expect(log?.textContent).not.toContain("Error:");
    expect(client.getAnnotatedFramesEnabled()).toBe(true);
  });

  it("displays a scheduled retry and manual cancellation details", async () => {
    await connectAndEnableAnnotations();
    socket.remoteClose(1006, "network lost");

    expect(root.querySelector(".connection-status")?.textContent).toContain(
      "attempt 1",
    );
    expect(root.querySelector(".connection-status")?.textContent).toContain(
      "100 ms",
    );

    root
      .querySelector<HTMLButtonElement>('[data-action="disconnect"]')
      ?.click();

    expect(reconnectTimers.callbacks).toHaveLength(0);
    expect(root.querySelector(".connection-status")?.textContent).toBe(
      "Manually disconnected",
    );
  });

  it("shows retry progress and clears warnings after successful reconnect", async () => {
    await connectAndEnableAnnotations();
    socket.remoteClose();
    reconnectTimers.runNext();

    expect(root.querySelector(".connection-status")?.textContent).toContain(
      "in progress",
    );
    sockets[1]?.open();
    await Promise.resolve();
    expect(root.querySelector(".connection-status")?.textContent).toContain(
      "Connected after retry 1",
    );
  });

  it("renders retry exhaustion without unsafe markup", async () => {
    await connectAndEnableAnnotations();
    socket.remoteClose();
    reconnectTimers.runNext();
    sockets[1]?.error();
    reconnectTimers.runNext();
    sockets[2]?.error();

    expect(root.querySelector(".connection-status")?.textContent).toContain(
      "exhausted after 2 attempts",
    );
    expect(root.querySelector(".message-log")?.textContent).toContain(
      "Reconnect exhausted",
    );
  });

  it("requires fresh annotation opt-in and leaves streaming stopped after reconnect", async () => {
    await connectAndEnableAnnotations();
    streamState = FrameStreamState.STREAMING;
    socket.remoteClose();

    expect(streamStop).toHaveBeenCalled();
    expect(streamState).toBe(FrameStreamState.STOPPED);
    expect(client.getAnnotatedFramesEnabled()).toBe(false);
    expect(
      root.querySelector<HTMLButtonElement>('[data-action="annotation"]')
        ?.disabled,
    ).toBe(true);

    reconnectTimers.runNext();
    sockets[1]?.open();
    await Promise.resolve();
    expect(streamState).toBe(FrameStreamState.STOPPED);
    expect(client.getAnnotatedFramesEnabled()).toBe(false);
    expect(
      root.querySelector<HTMLButtonElement>('[data-action="annotation"]')
        ?.disabled,
    ).toBe(true);
    expect(sockets[1]?.sent).toEqual([]);

    sockets[1]?.message(
      '{"protocol_version":1,"type":"connection.ready","capabilities":["annotated_frame.jpeg.v1"]}',
    );
    expect(
      root.querySelector<HTMLButtonElement>('[data-action="annotation"]')
        ?.disabled,
    ).toBe(false);
    expect(client.getAnnotatedFramesEnabled()).toBe(false);
  });

  it("renders a malicious remote-close reason only as plain text", async () => {
    const malicious = "<img src=x onerror=alert(1)>";
    await connectAndEnableAnnotations();
    socket.remoteClose(1006, malicious);

    expect(root.querySelector(".message-log")?.textContent).toContain(
      malicious,
    );
    expect(root.querySelectorAll("img")).toHaveLength(1);
    expect(root.querySelector("[onerror]")).toBeNull();
  });

  it("renders distinct server scheduler metrics and replaces old values", async () => {
    root.querySelector<HTMLButtonElement>('[data-action="connect"]')?.click();
    socket.open();
    await Promise.resolve();
    const sendMetrics = (received: number, dropped: number) =>
      socket.message(
        JSON.stringify({
          protocol_version: 1,
          type: "gesture.result",
          sequence: received,
          timestamp: 1,
          detected_hand_count: 0,
          selection: { decision: "NO_HANDS", identity: null },
          hand: null,
          gesture: { label: null, engine_decision: "NO_HAND" },
          action_executed: false,
          dispatch: null,
          scheduler: {
            received_frames: received,
            processed_frames: received - dropped,
            dropped_frames: dropped,
            processing_failures: 0,
            pending_frames: 1,
            queue_delay_ms: 12.345,
            processing_time_ms: 45.678,
          },
        }),
      );
    sendMetrics(10, 2);
    const server = root.querySelector(".server-scheduler-diagnostics");
    expect(server?.textContent).toContain("Server received: 10");
    expect(server?.textContent).toContain("20.0%");
    expect(server?.textContent).toContain("12.3 ms");
    expect(server?.textContent).toContain("45.7 ms");
    expect(root.querySelector(".stream-diagnostics")).not.toBe(server);
    sendMetrics(20, 5);
    expect(server?.textContent).toContain("Server received: 20");
    expect(server?.textContent).not.toContain("Server received: 10");
  });

  it("resets server metrics on close and keeps them empty after reconnect", async () => {
    root.querySelector<HTMLButtonElement>('[data-action="connect"]')?.click();
    socket.open();
    await Promise.resolve();
    socket.message(
      JSON.stringify({
        protocol_version: 1,
        type: "gesture.result",
        sequence: 1,
        timestamp: 1,
        detected_hand_count: 0,
        selection: { decision: "NO_HANDS", identity: null },
        hand: null,
        gesture: { label: null, engine_decision: "NO_HAND" },
        action_executed: false,
        dispatch: null,
        scheduler: {
          received_frames: 1,
          processed_frames: 1,
          dropped_frames: 0,
          processing_failures: 0,
          pending_frames: 0,
          queue_delay_ms: 0,
          processing_time_ms: 1,
        },
      }),
    );
    socket.remoteClose();
    const server = root.querySelector(".server-scheduler-diagnostics");
    expect(server?.textContent).toContain("No server scheduler metrics");
    reconnectTimers.runNext();
    sockets[1]?.open();
    await Promise.resolve();
    expect(server?.textContent).toContain("No server scheduler metrics");
  });

  it("renders a separate accessible adaptive section with default policy", () => {
    const adaptive = root.querySelector(".adaptive-stream-diagnostics");
    const browser = root.querySelector(".stream-diagnostics");
    const server = root.querySelector(".server-scheduler-diagnostics");
    const control = root.querySelector<HTMLButtonElement>(
      '[data-action="adaptive-mode"]',
    );
    expect(adaptive).not.toBe(browser);
    expect(adaptive).not.toBe(server);
    expect(adaptive?.textContent).toContain("Mode: Adaptive");
    expect(adaptive?.textContent).toContain("current target FPS: 8");
    expect(adaptive?.textContent).toContain("minimum FPS: 5");
    expect(adaptive?.textContent).toContain("maximum FPS: 8");
    expect(control?.getAttribute("aria-label")).toContain("adaptive");
  });

  it("renders overload, healthy, cooldown and capacity diagnostics safely", () => {
    updateAdaptive({
      targetFps: 6,
      healthySamples: 0,
      overloadSamples: 1,
      estimatedCapacityFps: 12.345,
      cooldownActive: true,
      latestDecision: Object.freeze({
        previousTargetFps: 8,
        targetFps: 6,
        direction: "decreased",
        reason: "server_drop",
        healthySamples: 0,
        overloadSamples: 1,
        estimatedCapacityFps: 12.345,
        adjustedAt: 100,
      }),
    });
    const diagnostics = root.querySelector(".adaptive-stream-diagnostics");
    expect(diagnostics?.textContent).toContain("latest direction: decreased");
    expect(diagnostics?.textContent).toContain("server_drop");
    expect(diagnostics?.textContent).toContain("12.3");
    expect(diagnostics?.textContent).toContain("cooldown: active");
    const previousDecision = adaptiveSnapshot.latestDecision;
    if (!previousDecision) throw new Error("Expected an adaptive decision.");
    updateAdaptive({
      latestDecision: Object.freeze({
        ...previousDecision,
        direction: "increased",
        reason: "healthy_window",
      }),
    });
    expect(diagnostics?.textContent).toContain("latest direction: increased");
  });

  it("switches fixed and adaptive modes without changing stream state", () => {
    streamState = FrameStreamState.STREAMING;
    const control = root.querySelector<HTMLButtonElement>(
      '[data-action="adaptive-mode"]',
    );
    control?.click();
    expect(
      root.querySelector(".adaptive-stream-diagnostics")?.textContent,
    ).toContain("Mode: Fixed");
    expect(streamState).toBe(FrameStreamState.STREAMING);
    updateAdaptive({ healthySamples: 4, overloadSamples: 2 });
    control?.click();
    expect(
      root.querySelector(".adaptive-stream-diagnostics")?.textContent,
    ).toContain("healthy samples: 0");
  });

  it("uses placeholders and never displays Infinity for missing capacity", () => {
    updateAdaptive({ estimatedCapacityFps: null, cooldownActive: false });
    const text = root.querySelector(
      ".adaptive-stream-diagnostics",
    )?.textContent;
    expect(text).toContain("estimated sustainable server FPS: unavailable");
    expect(text).toContain("cooldown: inactive");
    expect(text).not.toContain("Infinity");
    expect(text).not.toContain("NaN");
  });

  it("renders untrusted decision text only as plain text", () => {
    const malicious = '<img src=x onerror="alert(1)">';
    updateAdaptive({
      latestDecision: Object.freeze({
        previousTargetFps: 8,
        targetFps: 8,
        direction: malicious as never,
        reason: malicious as never,
        healthySamples: 0,
        overloadSamples: 0,
        estimatedCapacityFps: null,
        adjustedAt: null,
      }),
    });
    expect(
      root.querySelector(".adaptive-stream-status")?.textContent,
    ).toContain(malicious);
    expect(root.querySelector(".adaptive-stream-status img")).toBeNull();
    expect(root.querySelector("[onerror]")).toBeNull();
  });

  it("resets adaptive state on dashboard destruction", () => {
    dashboard.destroy();
    expect(adaptiveReset).toHaveBeenCalledOnce();
  });
});
