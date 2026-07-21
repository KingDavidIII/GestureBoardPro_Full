import { CameraController, CanvasFrameEncoder } from "../camera";
import { websocketUrl } from "../config/environment";
import { AnnotationCorrelation, DiagnosticDashboard } from "../dashboard";
import { RecognitionStateStore } from "../recognition";
import {
  AdaptiveQualityController,
  AdaptiveQualityCoordinator,
  AdaptiveResolutionController,
  AdaptiveResolutionCoordinator,
  AdaptiveStreamController,
  AdaptiveStreamCoordinator,
  BandwidthEstimator,
  FrameStreamController,
} from "../streaming";
import { GestureWebSocketClient } from "../websocket";
import { createRecognitionEventComposition } from "./application-composition";

type LifecycleEventType = "pagehide" | "beforeunload";

export interface ApplicationLifecycleTarget {
  addEventListener(type: LifecycleEventType, listener: EventListener): void;
  removeEventListener(type: LifecycleEventType, listener: EventListener): void;
}

interface Destroyable {
  destroy(): void;
}

interface Stoppable {
  stop(): void;
}

export interface ApplicationRuntimeResources {
  readonly dashboard: Destroyable;
  readonly recognitionUnsubscribe: () => void;
  readonly streamRecognitionUnsubscribe: () => void;
  readonly recognitionComposition: Destroyable;
  readonly adaptive: Destroyable;
  readonly adaptiveQuality: Destroyable;
  readonly adaptiveResolution: Destroyable;
  readonly stream: Destroyable;
  readonly camera: Stoppable;
  readonly client: Destroyable;
}

export type ApplicationCleanupErrorHandler = (
  operation: string,
  error: unknown,
) => void;

export interface CreateApplicationRuntimeOptions {
  readonly lifecycleTarget?: ApplicationLifecycleTarget;
  readonly cleanupErrorHandler?: ApplicationCleanupErrorHandler;
  readonly websocketUrl?: string;
}

const defaultCleanupErrorHandler: ApplicationCleanupErrorHandler = (
  operation,
  error,
) => console.error(`Application cleanup failed during ${operation}.`, error);

/** Own the complete browser application lifecycle and release each resource once. */
export class ApplicationRuntime {
  private destroyed = false;
  private lifecycleTarget: ApplicationLifecycleTarget | null = null;
  private readonly shutdownListener: EventListener = () => this.destroy();

  constructor(
    private readonly resources: ApplicationRuntimeResources,
    private readonly cleanupErrorHandler: ApplicationCleanupErrorHandler = defaultCleanupErrorHandler,
  ) {}

  attachLifecycle(target: ApplicationLifecycleTarget): void {
    if (this.destroyed)
      throw new Error(
        "Cannot attach lifecycle events after application shutdown.",
      );
    if (this.lifecycleTarget === target) return;
    if (this.lifecycleTarget)
      throw new Error("Application lifecycle events are already attached.");

    this.lifecycleTarget = target;
    target.addEventListener("pagehide", this.shutdownListener);
    target.addEventListener("beforeunload", this.shutdownListener);
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;

    const lifecycleTarget = this.lifecycleTarget;
    this.lifecycleTarget = null;
    if (lifecycleTarget) {
      lifecycleTarget.removeEventListener("pagehide", this.shutdownListener);
      lifecycleTarget.removeEventListener(
        "beforeunload",
        this.shutdownListener,
      );
    }

    const cleanup: ReadonlyArray<readonly [string, () => void]> = [
      ["dashboard.destroy", () => this.resources.dashboard.destroy()],
      ["recognition.unsubscribe", this.resources.recognitionUnsubscribe],
      [
        "stream-recognition.unsubscribe",
        this.resources.streamRecognitionUnsubscribe,
      ],
      [
        "recognition-composition.destroy",
        () => this.resources.recognitionComposition.destroy(),
      ],
      ["adaptive-stream.destroy", () => this.resources.adaptive.destroy()],
      [
        "adaptive-quality.destroy",
        () => this.resources.adaptiveQuality.destroy(),
      ],
      [
        "adaptive-resolution.destroy",
        () => this.resources.adaptiveResolution.destroy(),
      ],
      ["stream.destroy", () => this.resources.stream.destroy()],
      ["camera.stop", () => this.resources.camera.stop()],
      ["client.destroy", () => this.resources.client.destroy()],
    ];

    for (const [operation, release] of cleanup) {
      try {
        release();
      } catch (error) {
        this.cleanupErrorHandler(operation, error);
      }
    }
  }

  get isDestroyed(): boolean {
    return this.destroyed;
  }
}

export function createApplicationRuntime(
  root: HTMLElement,
  options: CreateApplicationRuntimeOptions = {},
): ApplicationRuntime {
  const client = new GestureWebSocketClient(
    options.websocketUrl ?? websocketUrl(),
  );
  const recognition = new RecognitionStateStore();
  const annotationCorrelation = new AnnotationCorrelation();
  const recognitionComposition = createRecognitionEventComposition(
    recognition,
    annotationCorrelation,
  );
  const camera = new CameraController();
  const encoder = new CanvasFrameEncoder();
  const stream = new FrameStreamController(camera, encoder, client);
  const adaptive = new AdaptiveStreamCoordinator(
    new AdaptiveStreamController({ maximumFps: stream.targetFps }),
    stream,
    client,
  );
  const adaptiveQuality = new AdaptiveQualityCoordinator(
    new AdaptiveQualityController({ initialQuality: stream.jpegQuality }),
    stream,
    client,
  );
  const adaptiveResolution = new AdaptiveResolutionCoordinator(
    new AdaptiveResolutionController(),
    new BandwidthEstimator(),
    stream,
    client,
    adaptiveQuality.controller.policy.minimumQuality,
  );
  const dashboard = new DiagnosticDashboard(root, client, {
    camera,
    stream,
    adaptive,
    adaptiveQuality,
    adaptiveResolution,
    recognition,
    annotationCorrelation,
    jpegQuality: encoder.jpegQuality,
    maximumFrameWidth: encoder.maximumWidth,
  });

  const runtime = new ApplicationRuntime(
    {
      dashboard,
      recognitionUnsubscribe: client.subscribe(
        recognitionComposition.handleSocketEvent,
      ),
      streamRecognitionUnsubscribe: stream.subscribe(
        recognitionComposition.handleStreamEvent,
      ),
      recognitionComposition,
      adaptive,
      adaptiveQuality,
      adaptiveResolution,
      stream,
      camera,
      client,
    },
    options.cleanupErrorHandler,
  );
  runtime.attachLifecycle(options.lifecycleTarget ?? window);
  return runtime;
}
