import {
  CameraState,
  type CameraController,
  type CameraEvent,
} from "../camera";
import type { AnnotatedFrameMessage, ServerMessage } from "../protocol";
import type { SchedulerMetadata } from "../protocol/messages";
import type { RecognitionState, RecognitionStateStore } from "../recognition";
import {
  type AdaptiveQualityMode,
  type AdaptiveQualitySnapshot,
  type AdaptiveMode,
  type AdaptiveStreamSnapshot,
  type AdaptiveResolutionMode,
  type AdaptiveResolutionSnapshot,
  FrameStreamState,
  type FrameStreamController,
  type FrameStreamEvent,
} from "../streaming";
import type {
  GestureWebSocketClient,
  GestureWebSocketClientEvent,
  WebSocketClientState,
} from "../websocket";
import { releaseResourceOperations } from "../lifecycle/resource-cleanup";
import {
  AnnotationCorrelation,
  type AnnotationCorrelationUpdate,
} from "./annotation-correlation";

const MAXIMUM_LOG_ENTRIES = 50;

export interface ObjectUrlApi {
  createObjectURL(blob: Blob): string;
  revokeObjectURL(url: string): void;
}
export interface DiagnosticDashboardOptions {
  readonly annotationCorrelation?: AnnotationCorrelation;
  readonly recognition?: RecognitionStateStore;
  readonly camera?: CameraController;
  readonly stream?: FrameStreamController;
  readonly jpegQuality?: number;
  readonly maximumFrameWidth?: number;
  readonly objectUrls?: ObjectUrlApi;
  readonly adaptive?: {
    getSnapshot(): AdaptiveStreamSnapshot;
    setMode(mode: AdaptiveMode): void;
    reset(): void;
    subscribe(listener: (snapshot: AdaptiveStreamSnapshot) => void): () => void;
  };
  readonly adaptiveQuality?: {
    getSnapshot(): AdaptiveQualitySnapshot;
    setMode(mode: AdaptiveQualityMode): void;
    reset(): void;
    subscribe(
      listener: (snapshot: AdaptiveQualitySnapshot) => void,
    ): () => void;
  };
  readonly adaptiveResolution?: {
    getSnapshot(): AdaptiveResolutionSnapshot;
    setMode(mode: AdaptiveResolutionMode): void;
    reset(): void;
    subscribe(
      listener: (snapshot: AdaptiveResolutionSnapshot) => void,
    ): () => void;
  };
}

export class DiagnosticDashboard {
  private readonly status: HTMLOutputElement;
  private readonly cameraStatus: HTMLOutputElement;
  private readonly streamStatus: HTMLOutputElement;
  private readonly diagnostics: HTMLDivElement;
  private readonly serverDiagnostics: HTMLDivElement;
  private readonly adaptiveDiagnostics: HTMLDivElement;
  private readonly adaptiveStatus: HTMLOutputElement;
  private readonly adaptiveModeButton: HTMLButtonElement;
  private readonly qualityDiagnostics: HTMLDivElement;
  private readonly qualityStatus: HTMLOutputElement;
  private readonly qualityModeButton: HTMLButtonElement;
  private readonly resolutionDiagnostics: HTMLDivElement;
  private readonly resolutionStatus: HTMLOutputElement;
  private readonly resolutionModeButton: HTMLButtonElement;
  private readonly connectButton: HTMLButtonElement;
  private readonly disconnectButton: HTMLButtonElement;
  private readonly pingButton: HTMLButtonElement;
  private readonly resetButton: HTMLButtonElement;
  private readonly startCameraButton: HTMLButtonElement;
  private readonly stopCameraButton: HTMLButtonElement;
  private readonly startStreamButton: HTMLButtonElement;
  private readonly stopStreamButton: HTMLButtonElement;
  private readonly video: HTMLVideoElement;
  private readonly annotationButton: HTMLButtonElement;
  private readonly annotationStatus: HTMLOutputElement;
  private readonly annotationImage: HTMLImageElement;
  private readonly messages: HTMLOListElement;
  private readonly unsubscribe: () => void;
  private readonly unsubscribeCamera: (() => void) | null;
  private readonly unsubscribeStream: (() => void) | null;
  private readonly unsubscribeAdaptive: (() => void) | null;
  private readonly unsubscribeQuality: (() => void) | null;
  private readonly unsubscribeResolution: (() => void) | null;
  private readonly unsubscribeRecognition: (() => void) | null;
  private readonly recognitionDiagnostics: HTMLDivElement;
  private readonly recognitionLive: HTMLOutputElement;
  private cameraState: CameraState | null;
  private streamState: FrameStreamState | null;
  private supportsAnnotations = false;
  private annotationUrl: string | null = null;
  private readonly objectUrls: ObjectUrlApi;
  private readonly annotationCorrelation: AnnotationCorrelation;
  private readonly handlesAnnotationEvents: boolean;
  private readonly unsubscribeAnnotationCorrelation: () => void;
  private destroyed = false;
  private reconnectPending = false;
  private schedulerMetrics: SchedulerMetadata | null = null;
  private adaptiveSnapshot: AdaptiveStreamSnapshot | null;
  private qualitySnapshot: AdaptiveQualitySnapshot | null;
  private resolutionSnapshot: AdaptiveResolutionSnapshot | null;

  constructor(
    private readonly root: HTMLElement,
    readonly client: GestureWebSocketClient,
    private readonly options: DiagnosticDashboardOptions = {},
  ) {
    this.cameraState = this.options.camera?.getState() ?? null;
    this.annotationCorrelation =
      this.options.annotationCorrelation ?? new AnnotationCorrelation();
    this.handlesAnnotationEvents =
      this.options.annotationCorrelation === undefined;
    this.objectUrls = this.options.objectUrls ?? URL;
    this.streamState = this.options.stream?.getState() ?? null;
    this.adaptiveSnapshot = this.options.adaptive?.getSnapshot() ?? null;
    this.qualitySnapshot = this.options.adaptiveQuality?.getSnapshot() ?? null;
    this.resolutionSnapshot =
      this.options.adaptiveResolution?.getSnapshot() ?? null;
    this.root.innerHTML = `<main class="diagnostic-dashboard" aria-labelledby="dashboard-title">
      <header><p class="eyebrow">GestureBoard Pro</p><h1 id="dashboard-title">Protocol diagnostics</h1><output class="connection-status" aria-live="polite" aria-atomic="true"></output></header>
      <section aria-labelledby="connection-controls-title"><h2 id="connection-controls-title">Connection</h2><p class="connection-url"></p><div class="controls"><button type="button" data-action="connect">Connect</button><button type="button" data-action="disconnect">Disconnect</button><button type="button" data-action="ping">Send ping</button><button type="button" data-action="reset">Reset runtime</button></div></section>
      <section class="camera-panel" aria-labelledby="camera-title"><h2 id="camera-title">Camera capture</h2><video class="camera-preview" autoplay muted playsinline aria-label="Local camera preview"></video><output class="camera-status" aria-live="polite" aria-atomic="true"></output><div class="controls"><button type="button" data-action="start-camera" aria-label="Start camera">Start Camera</button><button type="button" data-action="stop-camera" aria-label="Stop camera">Stop Camera</button><button type="button" data-action="start-stream" aria-label="Start frame streaming">Start Streaming</button><button type="button" data-action="stop-stream" aria-label="Stop frame streaming">Stop Streaming</button></div></section>
      <section aria-labelledby="stream-diagnostics-title"><h2 id="stream-diagnostics-title">Streaming diagnostics</h2><output class="stream-status" aria-live="polite" aria-atomic="true"></output><div class="stream-diagnostics"></div></section>
      <section aria-labelledby="server-scheduling-title"><h2 id="server-scheduling-title">Server-side frame scheduling</h2><div class="server-scheduler-diagnostics"></div></section>
      <section aria-labelledby="recognition-title"><h2 id="recognition-title">Gesture recognition</h2><div class="recognition-diagnostics"></div><output class="recognition-live" aria-live="polite" aria-atomic="true"></output></section>
      <section aria-labelledby="adaptive-stream-title"><h2 id="adaptive-stream-title">Adaptive stream control</h2><button type="button" data-action="adaptive-mode" aria-label="Switch adaptive stream mode"></button><output class="adaptive-stream-status" aria-live="polite" aria-atomic="true"></output><div class="adaptive-stream-diagnostics"></div></section>
      <section aria-labelledby="adaptive-quality-title"><h2 id="adaptive-quality-title">Adaptive JPEG quality</h2><button type="button" data-action="quality-mode" aria-label="Switch adaptive JPEG quality mode"></button><output class="adaptive-quality-status" aria-live="polite" aria-atomic="true"></output><div class="adaptive-quality-diagnostics"></div></section>
      <section aria-labelledby="adaptive-resolution-title"><h2 id="adaptive-resolution-title">Adaptive resolution</h2><button type="button" data-action="resolution-mode" aria-label="Switch adaptive resolution mode"></button><output class="adaptive-resolution-status" aria-live="polite" aria-atomic="true"></output><div class="adaptive-resolution-diagnostics"></div></section>
      <section aria-labelledby="annotation-title"><h2 id="annotation-title">Annotated feedback</h2><button type="button" data-action="annotation" aria-label="Enable annotated frame feedback">Enable annotated feedback</button><output class="annotation-status" aria-live="polite"></output><img class="annotated-preview" alt="Latest annotated gesture frame" /><p class="annotation-diagnostics"></p></section>
      <section aria-labelledby="message-log-title"><h2 id="message-log-title">Message log</h2><ol class="message-log" aria-live="polite" aria-relevant="additions"></ol></section>
    </main>`;
    this.status = this.element(".connection-status");
    this.cameraStatus = this.element(".camera-status");
    this.streamStatus = this.element(".stream-status");
    this.diagnostics = this.element(".stream-diagnostics");
    this.serverDiagnostics = this.element(".server-scheduler-diagnostics");
    this.recognitionDiagnostics = this.element(".recognition-diagnostics");
    this.recognitionLive = this.element(".recognition-live");
    this.adaptiveDiagnostics = this.element(".adaptive-stream-diagnostics");
    this.adaptiveStatus = this.element(".adaptive-stream-status");
    this.adaptiveModeButton = this.element('[data-action="adaptive-mode"]');
    this.qualityDiagnostics = this.element(".adaptive-quality-diagnostics");
    this.qualityStatus = this.element(".adaptive-quality-status");
    this.qualityModeButton = this.element('[data-action="quality-mode"]');
    this.resolutionDiagnostics = this.element(
      ".adaptive-resolution-diagnostics",
    );
    this.resolutionStatus = this.element(".adaptive-resolution-status");
    this.resolutionModeButton = this.element('[data-action="resolution-mode"]');
    this.connectButton = this.element('[data-action="connect"]');
    this.disconnectButton = this.element('[data-action="disconnect"]');
    this.pingButton = this.element('[data-action="ping"]');
    this.resetButton = this.element('[data-action="reset"]');
    this.startCameraButton = this.element('[data-action="start-camera"]');
    this.stopCameraButton = this.element('[data-action="stop-camera"]');
    this.startStreamButton = this.element('[data-action="start-stream"]');
    this.stopStreamButton = this.element('[data-action="stop-stream"]');
    this.video = this.element(".camera-preview");
    this.annotationButton = this.element('[data-action="annotation"]');
    this.annotationStatus = this.element(".annotation-status");
    this.annotationImage = this.element(".annotated-preview");
    this.messages = this.element(".message-log");
    this.element<HTMLParagraphElement>(".connection-url").textContent =
      this.client.url;
    this.connectButton.addEventListener("click", () => void this.connect());
    this.disconnectButton.addEventListener("click", () => this.disconnect());
    this.pingButton.addEventListener("click", () =>
      this.sendControl(() => this.client.sendPing()),
    );
    this.resetButton.addEventListener("click", () =>
      this.sendControl(() => this.client.resetRuntime()),
    );
    this.startCameraButton.addEventListener(
      "click",
      () => void this.startCamera(),
    );
    this.stopCameraButton.addEventListener("click", () => this.stopCamera());
    this.startStreamButton.addEventListener("click", () => this.startStream());
    this.stopStreamButton.addEventListener("click", () =>
      this.options.stream?.stop(),
    );
    this.annotationButton.addEventListener("click", () =>
      this.toggleAnnotations(),
    );
    this.adaptiveModeButton.addEventListener("click", () =>
      this.toggleAdaptiveMode(),
    );
    this.qualityModeButton.addEventListener("click", () =>
      this.toggleQualityMode(),
    );
    this.resolutionModeButton.addEventListener("click", () =>
      this.toggleResolutionMode(),
    );
    this.unsubscribe = this.client.subscribe((event) => {
      if (!this.destroyed) this.handleEvent(event);
    });
    this.unsubscribeAnnotationCorrelation =
      this.annotationCorrelation.subscribe((update) => {
        if (!this.destroyed) this.applyAnnotationUpdate(update);
      });
    this.unsubscribeCamera =
      this.options.camera?.subscribe((event) => {
        if (!this.destroyed) this.handleCameraEvent(event);
      }) ?? null;
    this.unsubscribeStream =
      this.options.stream?.subscribe((event) => {
        if (!this.destroyed) this.handleStreamEvent(event);
      }) ?? null;
    this.unsubscribeAdaptive =
      this.options.adaptive?.subscribe((snapshot) => {
        if (this.destroyed) return;
        this.adaptiveSnapshot = snapshot;
        this.renderAdaptive();
      }) ?? null;
    this.unsubscribeQuality =
      this.options.adaptiveQuality?.subscribe((snapshot) => {
        if (this.destroyed) return;
        this.qualitySnapshot = snapshot;
        this.renderQuality();
      }) ?? null;
    this.unsubscribeResolution =
      this.options.adaptiveResolution?.subscribe((snapshot) => {
        if (this.destroyed) return;
        this.resolutionSnapshot = snapshot;
        this.renderResolution();
      }) ?? null;
    this.unsubscribeRecognition =
      this.options.recognition?.subscribe((snapshot) => {
        if (!this.destroyed) this.renderRecognition(snapshot);
      }) ?? null;
    if (this.options.camera) void this.options.camera.attachPreview(this.video);
    this.renderState(this.client.getState());
    this.renderCamera();
    this.renderStream();
    this.renderServerScheduler();
    this.renderAdaptive();
    this.renderQuality();
    this.renderResolution();
    this.renderAnnotation();
    this.renderRecognition(
      this.options.recognition?.getSnapshot() ?? {
        availability: "unavailable",
        capabilityAvailable: false,
        epoch: 0,
        recognition: null,
        announcedEventId: null,
        shouldAnnounce: false,
        integrity: { kind: "omitted" },
        lastAcceptedFrameSequence: null,
      },
    );
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    releaseResourceOperations("DiagnosticDashboard", [
      ["client.unsubscribe", this.unsubscribe],
      [
        "annotation-correlation.unsubscribe",
        this.unsubscribeAnnotationCorrelation,
      ],
      ["camera.unsubscribe", () => this.unsubscribeCamera?.()],
      ["stream.unsubscribe", () => this.unsubscribeStream?.()],
      ["adaptive-stream.unsubscribe", () => this.unsubscribeAdaptive?.()],
      ["adaptive-quality.unsubscribe", () => this.unsubscribeQuality?.()],
      ["adaptive-resolution.unsubscribe", () => this.unsubscribeResolution?.()],
      ["recognition.unsubscribe", () => this.unsubscribeRecognition?.()],
      ["adaptive-stream.reset", () => this.options.adaptive?.reset()],
      ["adaptive-quality.reset", () => this.options.adaptiveQuality?.reset()],
      [
        "adaptive-resolution.reset",
        () => this.options.adaptiveResolution?.reset(),
      ],
      ["camera.detach-preview", () => this.options.camera?.detachPreview()],
      [
        "annotation-correlation.reset",
        () => this.annotationCorrelation.reset(),
      ],
      ["annotation.clear", () => this.clearAnnotation()],
      ["root.clear", () => this.root.replaceChildren()],
    ]);
  }
  private renderRecognition(snapshot: RecognitionState): void {
    const item = snapshot.recognition;
    const dash = "—";
    const label = (value: string | null | undefined): string =>
      value
        ? value
            .replace(/_/g, " ")
            .replace(/\b\w/g, (letter) => letter.toUpperCase())
        : dash;
    const percent = (value: number | undefined): string =>
      value === undefined ? dash : `${(value * 100).toFixed(0)}%`;
    const capability = snapshot.capabilityAvailable
      ? item
        ? "advertised; recognition available"
        : "advertised; no current recognition result"
      : snapshot.integrity.kind === "unadvertised" && item
        ? "not advertised; recognition accepted defensively"
        : "not advertised";
    const integrity =
      snapshot.integrity.kind === "malformed"
        ? `malformed optional recognition (${snapshot.integrity.reason})`
        : snapshot.integrity.kind === "unadvertised"
          ? "valid recognition without advertised capability"
          : snapshot.integrity.kind === "duplicate"
            ? "duplicate sequence ignored"
            : snapshot.integrity.kind === "stale"
              ? "stale sequence ignored"
              : snapshot.integrity.kind === "omitted"
                ? "omitted by server"
                : "valid";
    this.recognitionDiagnostics.textContent = `Capability: ${capability}; integrity: ${integrity}; frame: ${item?.frame_sequence ?? dash}; hands: ${item?.hand_count ?? dash}; primary: ${label(item?.primary_hand?.handedness)} (${percent(item?.primary_hand?.confidence)}); candidate: ${label(item?.candidate?.gesture_id)} (${percent(item?.candidate?.confidence)}); reason: ${item?.candidate?.reason?.replace(/_/g, " ") ?? dash}; stable: ${label(item?.stable?.gesture_id)} (${percent(item?.stable?.confidence)}); confirmed: ${item?.stable?.confirmed_frames ?? dash}; duration: ${item?.stable?.since_ms ?? dash} ms; transition: ${label(item?.transition?.kind)}; previous: ${label(item?.transition?.previous_gesture)}; gesture: ${label(item?.transition?.gesture)}; event: ${item?.transition?.event_id ?? dash}.`;
    if (!snapshot.shouldAnnounce || !item?.transition) {
      if (!item) this.recognitionLive.textContent = "";
      return;
    }
    const transition = item.transition;
    this.recognitionLive.textContent =
      transition.kind === "changed"
        ? `${label(transition.previous_gesture)} changed to ${label(transition.gesture)}`
        : transition.kind === "released"
          ? `${label(transition.previous_gesture)} released`
          : `${label(transition.gesture)} activated`;
  }
  private async connect(): Promise<void> {
    try {
      await this.client.connect();
    } catch (error) {
      this.append("Connection failed", this.errorMessage(error));
    }
  }
  private disconnect(): void {
    if (this.options.stream?.getState() === FrameStreamState.STREAMING)
      this.options.stream.stop();
    this.client.disconnect();
    this.reconnectPending = false;
    this.status.textContent = "Manually disconnected";
    this.renderState(this.client.getState());
    this.status.textContent = "Manually disconnected";
  }
  private async startCamera(): Promise<void> {
    try {
      await this.options.camera?.start();
    } catch (error) {
      this.append("Camera failed", this.errorMessage(error));
    }
  }
  private stopCamera(): void {
    this.options.stream?.stop();
    this.options.camera?.stop();
  }
  private startStream(): void {
    try {
      this.options.stream?.start();
    } catch (error) {
      this.append("Streaming failed", this.errorMessage(error));
    }
  }
  private sendControl(action: () => void): void {
    try {
      action();
    } catch (error) {
      this.append("Control failed", this.errorMessage(error));
    }
  }
  private handleEvent(event: GestureWebSocketClientEvent): void {
    if (event.type === "state.changed") {
      if (event.state !== "OPEN") this.options.stream?.stop();
      if (event.state !== "OPEN") {
        this.supportsAnnotations = false;
        this.annotationCorrelation.reset();
        this.clearAnnotation();
        this.schedulerMetrics = null;
        this.renderServerScheduler();
      }
      this.renderState(event.state);
    } else if (event.type === "protocol.message") {
      if (event.message.type === "connection.ready")
        this.supportsAnnotations =
          event.message.capabilities?.includes("annotated_frame.jpeg.v1") ??
          false;
      if (
        event.message.type === "annotated_frame.set.ack" &&
        !event.message.enabled
      )
        this.applyAnnotationUpdate(
          this.annotationCorrelation.clearPresentation(),
        );
      if (event.message.type === "gesture.result") {
        this.schedulerMetrics = event.message.scheduler ?? null;
        this.renderServerScheduler();
        if (this.handlesAnnotationEvents)
          this.annotationCorrelation.acceptResult(event.message);
      }
      this.append(event.message.type, this.messageSummary(event.message));
      this.renderAnnotation();
    } else if (event.type === "annotated-frame") {
      if (this.handlesAnnotationEvents)
        this.annotationCorrelation.acceptFrame(event.frame);
    } else if (event.type === "reconnect.scheduled") {
      this.reconnectPending = true;
      this.renderState(this.client.getState());
      this.status.textContent = `Reconnect attempt ${event.attempt} scheduled in approximately ${event.delayMs} ms`;
      this.append(
        "Reconnect scheduled",
        `Attempt ${event.attempt}; ${event.delayMs} ms`,
      );
    } else if (event.type === "reconnect.started") {
      this.reconnectPending = false;
      this.status.textContent = `Reconnect attempt ${event.attempt} in progress`;
      this.append("Reconnect started", `Attempt ${event.attempt}`);
    } else if (event.type === "reconnect.succeeded") {
      this.reconnectPending = false;
      this.status.textContent = `Connected after retry ${event.attempt}`;
      this.append("Reconnect succeeded", `Attempt ${event.attempt}`);
    } else if (event.type === "reconnect.exhausted") {
      this.reconnectPending = false;
      this.status.textContent = `Reconnect attempts exhausted after ${event.attempts} attempts`;
      this.append("Reconnect exhausted", `${event.attempts} attempts`);
    } else if (event.type === "reconnect.cancelled") {
      this.reconnectPending = false;
      this.status.textContent = event.reason;
      this.append("Reconnect cancelled", event.reason);
    } else if (event.type === "protocol.error" || event.type === "socket.error")
      this.append(event.error.code, event.error.message);
    else
      this.append(
        "socket.closed",
        `${event.code}: ${event.reason || "No reason supplied"}`,
      );
  }
  private handleCameraEvent(event: CameraEvent): void {
    if (event.type === "state.changed") this.cameraState = event.state;
    else if (event.type === "error")
      this.append(event.error.code, event.error.message);
    this.renderCamera();
  }
  private handleStreamEvent(event: FrameStreamEvent): void {
    if (event.type === "state.changed") this.streamState = event.state;
    else if (event.type === "error")
      this.append(event.error.code, event.error.message);
    this.renderStream();
  }
  private renderState(state: WebSocketClientState): void {
    const connected = state === "OPEN";
    this.status.textContent = `Connection state: ${state}`;
    this.connectButton.disabled =
      state === "CONNECTING" || connected || state === "CLOSING";
    this.disconnectButton.disabled =
      (state === "IDLE" || state === "CLOSED") && !this.reconnectPending;
    this.pingButton.disabled = !connected;
    this.resetButton.disabled = !connected;
    this.renderCamera();
    this.renderAnnotation();
  }
  private renderCamera(): void {
    const state = this.cameraState;
    const available = Boolean(this.options.camera);
    this.cameraStatus.textContent = `Camera state: ${state ?? "UNAVAILABLE"}`;
    this.startCameraButton.disabled =
      !available ||
      state === CameraState.REQUESTING_PERMISSION ||
      state === CameraState.READY;
    this.stopCameraButton.disabled =
      !available ||
      (state !== CameraState.REQUESTING_PERMISSION &&
        state !== CameraState.READY);
    this.startStreamButton.disabled =
      !available ||
      this.client.getState() !== "OPEN" ||
      state !== CameraState.READY ||
      this.streamState === FrameStreamState.STREAMING;
    this.stopStreamButton.disabled =
      !available ||
      (this.streamState !== FrameStreamState.STARTING &&
        this.streamState !== FrameStreamState.STREAMING);
  }
  private renderStream(): void {
    const metrics = this.options.stream?.getMetrics();
    this.streamStatus.textContent = `Streaming state: ${this.streamState ?? "UNAVAILABLE"}`;
    if (!metrics) {
      this.diagnostics.textContent = "Camera streaming is unavailable.";
      return;
    }
    const camera = this.options.camera?.getMetadata();
    this.diagnostics.textContent = `Capture: ${camera?.width ?? "?"}×${camera?.height ?? "?"}; target FPS: ${this.options.stream?.targetFps}; JPEG quality: ${this.options.jpegQuality ?? "default"}; maximum frame width: ${this.options.maximumFrameWidth ?? "default"}; effective FPS: ${metrics.effectiveFps.toFixed(1)}; sent: ${metrics.framesSent}; timing drops: ${metrics.framesDroppedForTiming}; backpressure drops: ${metrics.framesDroppedForBackpressure}; encoding failures: ${metrics.encodingFailures}; send failures: ${metrics.sendFailures}; latest size: ${metrics.lastFrameSize ?? 0} bytes; buffered: ${this.client.getBufferedAmount()} bytes.`;
    this.renderCamera();
  }
  private renderServerScheduler(): void {
    const metrics = this.schedulerMetrics;
    if (!metrics) {
      this.serverDiagnostics.textContent =
        "No server scheduler metrics for this connection.";
      return;
    }
    const dropPercentage =
      metrics.received_frames > 0
        ? `${((metrics.dropped_frames / metrics.received_frames) * 100).toFixed(1)}%`
        : "0.0%";
    const queueDelay = Math.min(metrics.queue_delay_ms, 999999).toFixed(1);
    const processingTime = Math.min(metrics.processing_time_ms, 999999).toFixed(
      1,
    );
    this.serverDiagnostics.textContent = `Server received: ${metrics.received_frames}; server processed: ${metrics.processed_frames}; server stale frames dropped: ${metrics.dropped_frames}; server processing failures: ${metrics.processing_failures}; server pending depth: ${metrics.pending_frames}; latest queue delay: ${queueDelay} ms; latest processing duration: ${processingTime} ms; server drop percentage: ${dropPercentage}.`;
  }
  private toggleAdaptiveMode(): void {
    const snapshot = this.adaptiveSnapshot;
    if (!snapshot) return;
    this.options.adaptive?.setMode(
      snapshot.mode === "adaptive" ? "fixed" : "adaptive",
    );
  }
  private renderAdaptive(): void {
    const snapshot = this.adaptiveSnapshot;
    this.adaptiveModeButton.disabled = !snapshot;
    if (!snapshot) {
      this.adaptiveModeButton.textContent = "Adaptive control unavailable";
      this.adaptiveStatus.textContent = "No adaptive decision available.";
      this.adaptiveDiagnostics.textContent =
        "Adaptive stream diagnostics are unavailable.";
      return;
    }
    const mode = snapshot.mode === "adaptive" ? "Adaptive" : "Fixed";
    this.adaptiveModeButton.textContent =
      snapshot.mode === "adaptive" ? "Use Fixed mode" : "Use Adaptive mode";
    const decision = snapshot.latestDecision;
    const capacity = Number.isFinite(snapshot.estimatedCapacityFps)
      ? snapshot.estimatedCapacityFps?.toFixed(1)
      : null;
    this.adaptiveStatus.textContent = decision
      ? `Latest adjustment: ${decision.direction}; ${decision.reason}.`
      : "Latest adjustment: none in this adaptive epoch.";
    this.adaptiveDiagnostics.textContent = `Mode: ${mode}; current target FPS: ${snapshot.targetFps}; minimum FPS: ${snapshot.minimumFps}; maximum FPS: ${snapshot.maximumFps}; latest direction: ${decision?.direction ?? "none"}; latest reason: ${decision?.reason ?? "none"}; healthy samples: ${snapshot.healthySamples}; overload samples: ${snapshot.overloadSamples}; estimated sustainable server FPS: ${capacity ?? "unavailable"}; cooldown: ${snapshot.cooldownActive ? "active" : "inactive"}.`;
  }
  private toggleQualityMode(): void {
    const snapshot = this.qualitySnapshot;
    if (!snapshot) return;
    this.options.adaptiveQuality?.setMode(
      snapshot.mode === "adaptive" ? "fixed" : "adaptive",
    );
  }
  private renderQuality(): void {
    const snapshot = this.qualitySnapshot;
    this.qualityModeButton.disabled = !snapshot;
    if (!snapshot) {
      this.qualityModeButton.textContent = "Quality control unavailable";
      this.qualityStatus.textContent =
        "No adaptive quality decision available.";
      this.qualityDiagnostics.textContent =
        "Adaptive quality diagnostics are unavailable.";
      return;
    }
    const mode = snapshot.mode === "adaptive" ? "Adaptive" : "Fixed";
    this.qualityModeButton.textContent =
      snapshot.mode === "adaptive"
        ? "Use Fixed quality"
        : "Use Adaptive quality";
    const decision = snapshot.latestDecision;
    this.qualityStatus.textContent = decision
      ? `Latest quality adjustment: ${decision.direction}; ${decision.reason}.`
      : "Latest quality adjustment: none in this transport epoch.";
    const percent = (value: number) =>
      Number.isFinite(value) ? `${(value * 100).toFixed(0)}%` : "unavailable";
    this.qualityDiagnostics.textContent = `Mode: ${mode}; current JPEG quality: ${percent(snapshot.quality)}; minimum quality: ${percent(snapshot.minimumQuality)}; maximum quality: ${percent(snapshot.maximumQuality)}; latest direction: ${decision?.direction ?? "none"}; latest reason: ${decision?.reason ?? "none"}; healthy samples: ${snapshot.healthySamples}; overload samples: ${snapshot.overloadSamples}; latest encoded payload: ${snapshot.latestPayloadBytes} bytes; latest WebSocket buffered: ${snapshot.latestBufferedBytes} bytes; cooldown: ${snapshot.cooldownActive ? "active" : "inactive"}.`;
  }
  private toggleResolutionMode(): void {
    const snapshot = this.resolutionSnapshot;
    if (!snapshot) return;
    this.options.adaptiveResolution?.setMode(
      snapshot.mode === "adaptive" ? "fixed" : "adaptive",
    );
  }
  private renderResolution(): void {
    const snapshot = this.resolutionSnapshot;
    this.resolutionModeButton.disabled = !snapshot;
    if (!snapshot) {
      this.resolutionModeButton.textContent = "Resolution control unavailable";
      this.resolutionStatus.textContent =
        "No adaptive resolution decision available.";
      this.resolutionDiagnostics.textContent =
        "Bandwidth and resolution diagnostics are unavailable.";
      return;
    }
    const decision = snapshot.latestDecision;
    const estimate = snapshot.estimate;
    const bitrate =
      estimate.smoothedBitrateBps === null
        ? "unavailable"
        : `${Math.min(estimate.smoothedBitrateBps, 999999999).toFixed(0)} bps`;
    const headroom =
      decision?.headroomRatio === null || decision?.headroomRatio === undefined
        ? "unavailable"
        : `${Math.min(decision.headroomRatio, 999999).toFixed(2)}×`;
    this.resolutionModeButton.textContent =
      snapshot.mode === "adaptive"
        ? "Use Fixed resolution"
        : "Use Adaptive resolution";
    this.resolutionStatus.textContent = decision
      ? `Latest resolution adjustment: ${decision.direction}; ${decision.reason}.`
      : "Latest resolution adjustment: none in this epoch.";
    this.resolutionDiagnostics.textContent = `Bandwidth estimate: ${bitrate}; confidence: ${estimate.confidence}; pressure: ${estimate.pressure}; estimated bytes per second: ${estimate.estimatedBytesPerSecond === null ? "unavailable" : Math.min(estimate.estimatedBytesPerSecond, 999999999).toFixed(0)}; average frame size: ${estimate.averageFrameBytes ?? "unavailable"}; buffered: ${estimate.latestBufferedBytes}; payload: ${estimate.latestPayloadBytes}. Resolution mode: ${snapshot.mode === "adaptive" ? "Adaptive" : "Fixed"}; current profile: ${snapshot.currentProfile.id}; dimensions: ${snapshot.currentProfile.width}×${snapshot.currentProfile.height}; minimum profile: ${snapshot.minimumProfile.id}; maximum profile: ${snapshot.maximumProfile.id}; healthy samples: ${snapshot.healthySamples}; overload samples: ${snapshot.overloadSamples}; headroom: ${headroom}; cooldown: ${snapshot.cooldownActive ? "active" : "inactive"}.`;
  }
  private append(title: string, detail: string): void {
    const entry = document.createElement("li");
    const heading = document.createElement("strong");
    heading.textContent = title;
    entry.append(heading, ` — ${detail}`);
    this.messages.prepend(entry);
    while (this.messages.children.length > MAXIMUM_LOG_ENTRIES)
      this.messages.lastElementChild?.remove();
  }
  private toggleAnnotations(): void {
    try {
      this.client.setAnnotatedFramesEnabled(
        !this.client.getAnnotatedFramesEnabled(),
      );
    } catch (error) {
      this.append("Annotation control failed", this.errorMessage(error));
    }
  }
  private renderAnnotation(): void {
    const enabled = this.client.getAnnotatedFramesEnabled();
    this.annotationButton.disabled =
      this.client.getState() !== "OPEN" || !this.supportsAnnotations;
    this.annotationButton.textContent = enabled
      ? "Disable annotated feedback"
      : "Enable annotated feedback";
    this.annotationStatus.textContent = this.supportsAnnotations
      ? `Annotation feedback: ${enabled ? "enabled" : "disabled"}`
      : "Annotation feedback unavailable";
  }
  private showAnnotation(frame: AnnotatedFrameMessage): void {
    this.clearAnnotation();
    const url = this.objectUrls.createObjectURL(frame.blob);
    this.annotationUrl = url;
    this.annotationImage.src = url;
    this.annotationStatus.textContent = "Annotation feedback: frame available";
    this.element<HTMLParagraphElement>(".annotation-diagnostics").textContent =
      `Sequence ${frame.sequence}; ${frame.width}×${frame.height}; ${frame.size} bytes.`;
  }
  private applyAnnotationUpdate(update: AnnotationCorrelationUpdate): void {
    if (update.kind === "frame") this.showAnnotation(update.frame);
    else if (update.kind === "clear") this.clearAnnotation();
  }
  private clearAnnotation(): void {
    if (this.annotationUrl) this.objectUrls.revokeObjectURL(this.annotationUrl);
    this.annotationUrl = null;
    if (this.annotationImage) this.annotationImage.removeAttribute("src");
    const diagnostics = this.root.querySelector<HTMLParagraphElement>(
      ".annotation-diagnostics",
    );
    if (diagnostics) diagnostics.textContent = "No annotated frame received.";
  }
  private messageSummary(message: ServerMessage): string {
    if (message.type === "gesture.result")
      return `Sequence ${message.sequence}; gesture ${message.gesture.label ?? "none"}.`;
    if (message.type === "error") return message.error.message;
    return "request_id" in message && message.request_id
      ? `Request ${message.request_id}`
      : "Received";
  }
  private errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }
  private element<T extends Element = HTMLElement>(selector: string): T {
    const element = this.root.querySelector<T>(selector);
    if (!element) throw new Error(`Dashboard element not found: ${selector}`);
    return element;
  }
}
