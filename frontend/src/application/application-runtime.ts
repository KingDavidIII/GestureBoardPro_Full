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

export interface ApplicationRuntimeResources {
  readonly dashboard: Destroyable;
  readonly recognitionUnsubscribe: () => void;
  readonly streamRecognitionUnsubscribe: () => void;
  readonly recognitionComposition: Destroyable;
  readonly annotationCorrelation: Destroyable;
  readonly adaptive: Destroyable;
  readonly adaptiveQuality: Destroyable;
  readonly adaptiveResolution: Destroyable;
  readonly stream: Destroyable;
  readonly camera: Destroyable;
  readonly client: Destroyable;
}

export type ApplicationCleanupErrorHandler = (
  operation: string,
  error: unknown,
) => void;

export interface ApplicationStartupCleanupRegistry {
  register(operation: string, release: () => void): void;
}

export type ApplicationRuntimeResourceFactory = (
  root: HTMLElement,
  websocketTarget: string,
  cleanup: ApplicationStartupCleanupRegistry,
) => ApplicationRuntimeResources;

export interface CreateApplicationRuntimeOptions {
  readonly lifecycleTarget?: ApplicationLifecycleTarget;
  readonly cleanupErrorHandler?: ApplicationCleanupErrorHandler;
  readonly websocketUrl?: string;
  readonly resourceFactory?: ApplicationRuntimeResourceFactory;
}

type CleanupEntry = readonly [operation: string, release: () => void];

const defaultCleanupErrorHandler: ApplicationCleanupErrorHandler = (
  operation,
  error,
) => console.error(`Application cleanup failed during ${operation}.`, error);

const reportCleanupError = (
  handler: ApplicationCleanupErrorHandler,
  operation: string,
  error: unknown,
): void => {
  try {
    handler(operation, error);
  } catch (handlerError) {
    defaultCleanupErrorHandler(
      `${operation} cleanup-error-handler`,
      handlerError,
    );
  }
};

const releaseSafely = (
  entry: CleanupEntry,
  handler: ApplicationCleanupErrorHandler,
): void => {
  const [operation, release] = entry;
  try {
    release();
  } catch (error) {
    reportCleanupError(handler, operation, error);
  }
};

class ApplicationStartupCleanupStack
  implements ApplicationStartupCleanupRegistry
{
  private readonly entries: CleanupEntry[] = [];
  private completed = false;

  register(operation: string, release: () => void): void {
    if (this.completed)
      throw new Error("Application startup cleanup ownership is closed.");
    this.entries.push([operation, release]);
  }

  transferOwnership(): void {
    if (this.completed) return;
    this.completed = true;
    this.entries.length = 0;
  }

  rollback(handler: ApplicationCleanupErrorHandler): void {
    if (this.completed) return;
    this.completed = true;
    for (const entry of this.entries.reverse()) releaseSafely(entry, handler);
    this.entries.length = 0;
  }
}

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

    try {
      target.addEventListener("pagehide", this.shutdownListener);
      target.addEventListener("beforeunload", this.shutdownListener);
    } catch (error) {
      releaseSafely(
        [
          "lifecycle.beforeunload.remove",
          () =>
            target.removeEventListener("beforeunload", this.shutdownListener),
        ],
        this.cleanupErrorHandler,
      );
      releaseSafely(
        [
          "lifecycle.pagehide.remove",
          () => target.removeEventListener("pagehide", this.shutdownListener),
        ],
        this.cleanupErrorHandler,
      );
      throw error;
    }

    this.lifecycleTarget = target;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;

    const lifecycleTarget = this.lifecycleTarget;
    this.lifecycleTarget = null;

    const cleanup: CleanupEntry[] = [];
    if (lifecycleTarget) {
      cleanup.push(
        [
          "lifecycle.beforeunload.remove",
          () =>
            lifecycleTarget.removeEventListener(
              "beforeunload",
              this.shutdownListener,
            ),
        ],
        [
          "lifecycle.pagehide.remove",
          () =>
            lifecycleTarget.removeEventListener(
              "pagehide",
              this.shutdownListener,
            ),
        ],
      );
    }
    cleanup.push(
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
      [
        "annotation-correlation.destroy",
        () => this.resources.annotationCorrelation.destroy(),
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
      ["camera.destroy", () => this.resources.camera.destroy()],
      ["client.destroy", () => this.resources.client.destroy()],
    );

    for (const entry of cleanup) releaseSafely(entry, this.cleanupErrorHandler);
  }

  get isDestroyed(): boolean {
    return this.destroyed;
  }
}

const createDefaultApplicationRuntimeResources: ApplicationRuntimeResourceFactory =
  (root, websocketTarget, cleanup) => {
    const client = new GestureWebSocketClient(websocketTarget);
    cleanup.register("client.destroy", () => client.destroy());

    const recognition = new RecognitionStateStore();
    const annotationCorrelation = new AnnotationCorrelation();
    cleanup.register("annotation-correlation.destroy", () =>
      annotationCorrelation.destroy(),
    );
    const recognitionComposition = createRecognitionEventComposition(
      recognition,
      annotationCorrelation,
    );
    cleanup.register("recognition-composition.destroy", () =>
      recognitionComposition.destroy(),
    );

    const camera = new CameraController();
    cleanup.register("camera.destroy", () => camera.destroy());

    const encoder = new CanvasFrameEncoder();
    const stream = new FrameStreamController(camera, encoder, client);
    cleanup.register("stream.destroy", () => stream.destroy());

    const adaptive = new AdaptiveStreamCoordinator(
      new AdaptiveStreamController({ maximumFps: stream.targetFps }),
      stream,
      client,
    );
    cleanup.register("adaptive-stream.destroy", () => adaptive.destroy());

    const adaptiveQuality = new AdaptiveQualityCoordinator(
      new AdaptiveQualityController({ initialQuality: stream.jpegQuality }),
      stream,
      client,
    );
    cleanup.register("adaptive-quality.destroy", () =>
      adaptiveQuality.destroy(),
    );

    const adaptiveResolution = new AdaptiveResolutionCoordinator(
      new AdaptiveResolutionController(),
      new BandwidthEstimator(),
      stream,
      client,
      adaptiveQuality.controller.policy.minimumQuality,
    );
    cleanup.register("adaptive-resolution.destroy", () =>
      adaptiveResolution.destroy(),
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
    cleanup.register("dashboard.destroy", () => dashboard.destroy());

    const recognitionUnsubscribe = client.subscribe(
      recognitionComposition.handleSocketEvent,
    );
    cleanup.register("recognition.unsubscribe", recognitionUnsubscribe);

    const streamRecognitionUnsubscribe = stream.subscribe(
      recognitionComposition.handleStreamEvent,
    );
    cleanup.register(
      "stream-recognition.unsubscribe",
      streamRecognitionUnsubscribe,
    );

    return {
      dashboard,
      recognitionUnsubscribe,
      streamRecognitionUnsubscribe,
      recognitionComposition,
      annotationCorrelation,
      adaptive,
      adaptiveQuality,
      adaptiveResolution,
      stream,
      camera,
      client,
    };
  };

export function createApplicationRuntime(
  root: HTMLElement,
  options: CreateApplicationRuntimeOptions = {},
): ApplicationRuntime {
  const cleanupErrorHandler =
    options.cleanupErrorHandler ?? defaultCleanupErrorHandler;
  const startupCleanup = new ApplicationStartupCleanupStack();
  let runtime: ApplicationRuntime;

  try {
    const resources = (
      options.resourceFactory ?? createDefaultApplicationRuntimeResources
    )(root, options.websocketUrl ?? websocketUrl(), startupCleanup);
    runtime = new ApplicationRuntime(resources, cleanupErrorHandler);
  } catch (error) {
    startupCleanup.rollback(cleanupErrorHandler);
    throw error;
  }

  startupCleanup.transferOwnership();

  try {
    runtime.attachLifecycle(options.lifecycleTarget ?? window);
  } catch (error) {
    runtime.destroy();
    throw error;
  }

  return runtime;
}
