import {
  CameraState,
  type CameraController,
  type CameraEvent,
} from "../camera";
import type { AnnotatedFrameMessage, ServerMessage } from "../protocol";
import type { SchedulerMetadata } from "../protocol/messages";
import {
  type AdaptiveMode,
  type AdaptiveStreamSnapshot,
  FrameStreamState,
  type FrameStreamController,
  type FrameStreamEvent,
} from "../streaming";
import type {
  GestureWebSocketClient,
  GestureWebSocketClientEvent,
  WebSocketClientState,
} from "../websocket";

const MAXIMUM_LOG_ENTRIES = 50;

export interface ObjectUrlApi {
  createObjectURL(blob: Blob): string;
  revokeObjectURL(url: string): void;
}
export interface DiagnosticDashboardOptions {
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
  private cameraState: CameraState | null;
  private streamState: FrameStreamState | null;
  private supportsAnnotations = false;
  private annotationUrl: string | null = null;
  private readonly objectUrls: ObjectUrlApi;
  private destroyed = false;
  private reconnectPending = false;
  private schedulerMetrics: SchedulerMetadata | null = null;
  private adaptiveSnapshot: AdaptiveStreamSnapshot | null;

  constructor(
    private readonly root: HTMLElement,
    readonly client: GestureWebSocketClient,
    private readonly options: DiagnosticDashboardOptions = {},
  ) {
    this.cameraState = this.options.camera?.getState() ?? null;
    this.objectUrls = this.options.objectUrls ?? URL;
    this.streamState = this.options.stream?.getState() ?? null;
    this.adaptiveSnapshot = this.options.adaptive?.getSnapshot() ?? null;
    this.root.innerHTML = `<main class="diagnostic-dashboard" aria-labelledby="dashboard-title">
      <header><p class="eyebrow">GestureBoard Pro</p><h1 id="dashboard-title">Protocol diagnostics</h1><output class="connection-status" aria-live="polite" aria-atomic="true"></output></header>
      <section aria-labelledby="connection-controls-title"><h2 id="connection-controls-title">Connection</h2><p class="connection-url"></p><div class="controls"><button type="button" data-action="connect">Connect</button><button type="button" data-action="disconnect">Disconnect</button><button type="button" data-action="ping">Send ping</button><button type="button" data-action="reset">Reset runtime</button></div></section>
      <section class="camera-panel" aria-labelledby="camera-title"><h2 id="camera-title">Camera capture</h2><video class="camera-preview" autoplay muted playsinline aria-label="Local camera preview"></video><output class="camera-status" aria-live="polite" aria-atomic="true"></output><div class="controls"><button type="button" data-action="start-camera" aria-label="Start camera">Start Camera</button><button type="button" data-action="stop-camera" aria-label="Stop camera">Stop Camera</button><button type="button" data-action="start-stream" aria-label="Start frame streaming">Start Streaming</button><button type="button" data-action="stop-stream" aria-label="Stop frame streaming">Stop Streaming</button></div></section>
      <section aria-labelledby="stream-diagnostics-title"><h2 id="stream-diagnostics-title">Streaming diagnostics</h2><output class="stream-status" aria-live="polite" aria-atomic="true"></output><div class="stream-diagnostics"></div></section>
      <section aria-labelledby="server-scheduling-title"><h2 id="server-scheduling-title">Server-side frame scheduling</h2><div class="server-scheduler-diagnostics"></div></section>
      <section aria-labelledby="adaptive-stream-title"><h2 id="adaptive-stream-title">Adaptive stream control</h2><button type="button" data-action="adaptive-mode" aria-label="Switch adaptive stream mode"></button><output class="adaptive-stream-status" aria-live="polite" aria-atomic="true"></output><div class="adaptive-stream-diagnostics"></div></section>
      <section aria-labelledby="annotation-title"><h2 id="annotation-title">Annotated feedback</h2><button type="button" data-action="annotation" aria-label="Enable annotated frame feedback">Enable annotated feedback</button><output class="annotation-status" aria-live="polite"></output><img class="annotated-preview" alt="Latest annotated gesture frame" /><p class="annotation-diagnostics"></p></section>
      <section aria-labelledby="message-log-title"><h2 id="message-log-title">Message log</h2><ol class="message-log" aria-live="polite" aria-relevant="additions"></ol></section>
    </main>`;
    this.status = this.element(".connection-status");
    this.cameraStatus = this.element(".camera-status");
    this.streamStatus = this.element(".stream-status");
    this.diagnostics = this.element(".stream-diagnostics");
    this.serverDiagnostics = this.element(".server-scheduler-diagnostics");
    this.adaptiveDiagnostics = this.element(".adaptive-stream-diagnostics");
    this.adaptiveStatus = this.element(".adaptive-stream-status");
    this.adaptiveModeButton = this.element('[data-action="adaptive-mode"]');
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
    this.unsubscribe = this.client.subscribe((event) =>
      this.handleEvent(event),
    );
    this.unsubscribeCamera =
      this.options.camera?.subscribe((event) =>
        this.handleCameraEvent(event),
      ) ?? null;
    this.unsubscribeStream =
      this.options.stream?.subscribe((event) =>
        this.handleStreamEvent(event),
      ) ?? null;
    this.unsubscribeAdaptive =
      this.options.adaptive?.subscribe((snapshot) => {
        this.adaptiveSnapshot = snapshot;
        this.renderAdaptive();
      }) ?? null;
    if (this.options.camera) void this.options.camera.attachPreview(this.video);
    this.renderState(this.client.getState());
    this.renderCamera();
    this.renderStream();
    this.renderServerScheduler();
    this.renderAdaptive();
    this.renderAnnotation();
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.unsubscribe();
    this.unsubscribeCamera?.();
    this.unsubscribeStream?.();
    this.unsubscribeAdaptive?.();
    this.options.adaptive?.reset();
    this.options.camera?.detachPreview();
    this.clearAnnotation();
    this.root.replaceChildren();
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
        this.clearAnnotation();
      if (event.message.type === "gesture.result") {
        this.schedulerMetrics = event.message.scheduler ?? null;
        this.renderServerScheduler();
      }
      this.append(event.message.type, this.messageSummary(event.message));
      this.renderAnnotation();
    } else if (event.type === "annotated-frame") {
      this.showAnnotation(event.frame);
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
