import { describe, expect, it, vi } from "vitest";

import {
  ApplicationRuntime,
  createApplicationRuntime,
  type ApplicationLifecycleTarget,
  type ApplicationRuntimeResourceFactory,
  type ApplicationRuntimeResources,
} from "../src/application/application-runtime";

type LifecycleEventType = "pagehide" | "beforeunload";

interface FakeLifecycleTargetOptions {
  readonly throwOnAdd?: LifecycleEventType;
  readonly throwOnRemove?: LifecycleEventType;
}

class FakeLifecycleTarget implements ApplicationLifecycleTarget {
  private readonly listeners = new Map<
    LifecycleEventType,
    Set<EventListener>
  >();

  constructor(private readonly options: FakeLifecycleTargetOptions = {}) {}

  addEventListener(type: LifecycleEventType, listener: EventListener): void {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
    if (this.options.throwOnAdd === type)
      throw new Error(`Could not attach ${type}.`);
  }

  removeEventListener(type: LifecycleEventType, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
    if (this.options.throwOnRemove === type)
      throw new Error(`Could not detach ${type}.`);
  }

  dispatch(type: LifecycleEventType): void {
    for (const listener of [...(this.listeners.get(type) ?? [])])
      listener(new Event(type));
  }

  count(type: LifecycleEventType): number {
    return this.listeners.get(type)?.size ?? 0;
  }
}

const resources = (order: string[]) => {
  const operation = (name: string) =>
    vi.fn(() => {
      order.push(name);
    });
  const operations = {
    dashboard: operation("dashboard.destroy"),
    recognitionUnsubscribe: operation("recognition.unsubscribe"),
    streamRecognitionUnsubscribe: operation("stream-recognition.unsubscribe"),
    recognitionComposition: operation("recognition-composition.destroy"),
    adaptive: operation("adaptive-stream.destroy"),
    adaptiveQuality: operation("adaptive-quality.destroy"),
    adaptiveResolution: operation("adaptive-resolution.destroy"),
    stream: operation("stream.destroy"),
    camera: operation("camera.stop"),
    client: operation("client.destroy"),
  };
  const value: ApplicationRuntimeResources = {
    dashboard: { destroy: operations.dashboard },
    recognitionUnsubscribe: operations.recognitionUnsubscribe,
    streamRecognitionUnsubscribe: operations.streamRecognitionUnsubscribe,
    recognitionComposition: {
      destroy: operations.recognitionComposition,
    },
    adaptive: { destroy: operations.adaptive },
    adaptiveQuality: { destroy: operations.adaptiveQuality },
    adaptiveResolution: { destroy: operations.adaptiveResolution },
    stream: { destroy: operations.stream },
    camera: { stop: operations.camera },
    client: { destroy: operations.client },
  };

  return { value, operations };
};

describe("ApplicationRuntime", () => {
  it("owns browser lifecycle listeners and destroys every resource once", () => {
    const order: string[] = [];
    const { value, operations } = resources(order);
    const lifecycle = new FakeLifecycleTarget();
    const cleanupErrorHandler = vi.fn();
    const runtime = new ApplicationRuntime(value, cleanupErrorHandler);

    runtime.attachLifecycle(lifecycle);
    runtime.attachLifecycle(lifecycle);
    expect(lifecycle.count("pagehide")).toBe(1);
    expect(lifecycle.count("beforeunload")).toBe(1);

    lifecycle.dispatch("pagehide");

    expect(runtime.isDestroyed).toBe(true);
    expect(lifecycle.count("pagehide")).toBe(0);
    expect(lifecycle.count("beforeunload")).toBe(0);
    expect(order).toEqual([
      "dashboard.destroy",
      "recognition.unsubscribe",
      "stream-recognition.unsubscribe",
      "recognition-composition.destroy",
      "adaptive-stream.destroy",
      "adaptive-quality.destroy",
      "adaptive-resolution.destroy",
      "stream.destroy",
      "camera.stop",
      "client.destroy",
    ]);

    lifecycle.dispatch("beforeunload");
    runtime.destroy();
    for (const operation of Object.values(operations))
      expect(operation).toHaveBeenCalledTimes(1);
    expect(cleanupErrorHandler).not.toHaveBeenCalled();
  });

  it("rolls back partially attached lifecycle listeners", () => {
    const { value, operations } = resources([]);
    const lifecycle = new FakeLifecycleTarget({
      throwOnAdd: "beforeunload",
    });
    const runtime = new ApplicationRuntime(value, vi.fn());

    expect(() => runtime.attachLifecycle(lifecycle)).toThrow(
      "Could not attach beforeunload.",
    );

    expect(runtime.isDestroyed).toBe(false);
    expect(lifecycle.count("pagehide")).toBe(0);
    expect(lifecycle.count("beforeunload")).toBe(0);

    runtime.destroy();
    for (const operation of Object.values(operations))
      expect(operation).toHaveBeenCalledTimes(1);
  });

  it("continues cleanup after lifecycle detachment fails", () => {
    const order: string[] = [];
    const { value, operations } = resources(order);
    const lifecycle = new FakeLifecycleTarget({
      throwOnRemove: "beforeunload",
    });
    const cleanupErrorHandler = vi.fn();
    const runtime = new ApplicationRuntime(value, cleanupErrorHandler);

    runtime.attachLifecycle(lifecycle);
    runtime.destroy();

    expect(cleanupErrorHandler).toHaveBeenCalledWith(
      "lifecycle.beforeunload.remove",
      expect.objectContaining({ message: "Could not detach beforeunload." }),
    );
    expect(lifecycle.count("pagehide")).toBe(0);
    expect(lifecycle.count("beforeunload")).toBe(0);
    expect(operations.client).toHaveBeenCalledTimes(1);
    expect(order.at(-1)).toBe("client.destroy");
  });

  it("continues cleanup after a resource throws", () => {
    const order: string[] = [];
    const { value, operations } = resources(order);
    const failure = new Error("dashboard cleanup failed");

    operations.dashboard.mockImplementationOnce(() => {
      order.push("dashboard.destroy");
      throw failure;
    });
    const cleanupErrorHandler = vi.fn();
    const runtime = new ApplicationRuntime(value, cleanupErrorHandler);

    runtime.destroy();

    expect(cleanupErrorHandler).toHaveBeenCalledWith(
      "dashboard.destroy",
      failure,
    );
    expect(operations.client).toHaveBeenCalledTimes(1);
    expect(order.at(-1)).toBe("client.destroy");
  });

  it("isolates a failing cleanup error handler", () => {
    const order: string[] = [];
    const { value, operations } = resources(order);
    const cleanupFailure = new Error("dashboard cleanup failed");
    const reportingFailure = new Error("cleanup reporting failed");
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    operations.dashboard.mockImplementationOnce(() => {
      order.push("dashboard.destroy");
      throw cleanupFailure;
    });
    const cleanupErrorHandler = vi.fn(() => {
      throw reportingFailure;
    });
    const runtime = new ApplicationRuntime(value, cleanupErrorHandler);

    runtime.destroy();

    expect(cleanupErrorHandler).toHaveBeenCalledWith(
      "dashboard.destroy",
      cleanupFailure,
    );
    expect(consoleError).toHaveBeenCalledWith(
      "Application cleanup failed during dashboard.destroy cleanup-error-handler.",
      reportingFailure,
    );
    expect(operations.client).toHaveBeenCalledTimes(1);
    expect(order.at(-1)).toBe("client.destroy");

    consoleError.mockRestore();
  });

  it("rejects lifecycle attachment after shutdown", () => {
    const { value } = resources([]);
    const runtime = new ApplicationRuntime(value, vi.fn());
    runtime.destroy();

    expect(() => runtime.attachLifecycle(new FakeLifecycleTarget())).toThrow(
      "Cannot attach lifecycle events after application shutdown.",
    );
  });
});

describe("createApplicationRuntime", () => {
  it("rolls back acquired startup resources in reverse order", () => {
    const order: string[] = [];
    const assemblyFailure = new Error("resource assembly failed");
    const rollbackFailure = new Error("stream rollback failed");
    const cleanupErrorHandler = vi.fn();
    const resourceFactory: ApplicationRuntimeResourceFactory = (
      _root,
      _websocketTarget,
      cleanup,
    ) => {
      cleanup.register("client.destroy", () => order.push("client.destroy"));
      cleanup.register("stream.destroy", () => {
        order.push("stream.destroy");
        throw rollbackFailure;
      });
      cleanup.register("dashboard.destroy", () =>
        order.push("dashboard.destroy"),
      );
      throw assemblyFailure;
    };

    expect(() =>
      createApplicationRuntime(document.createElement("div"), {
        lifecycleTarget: new FakeLifecycleTarget(),
        cleanupErrorHandler,
        websocketUrl: "ws://localhost/ws/gesture/",
        resourceFactory,
      }),
    ).toThrow(assemblyFailure);

    expect(order).toEqual([
      "dashboard.destroy",
      "stream.destroy",
      "client.destroy",
    ]);
    expect(cleanupErrorHandler).toHaveBeenCalledWith(
      "stream.destroy",
      rollbackFailure,
    );
  });

  it("destroys assembled resources when lifecycle attachment fails", () => {
    const order: string[] = [];
    const { value, operations } = resources(order);
    const lifecycle = new FakeLifecycleTarget({
      throwOnAdd: "beforeunload",
    });
    const resourceFactory: ApplicationRuntimeResourceFactory = () => value;

    expect(() =>
      createApplicationRuntime(document.createElement("div"), {
        lifecycleTarget: lifecycle,
        cleanupErrorHandler: vi.fn(),
        websocketUrl: "ws://localhost/ws/gesture/",
        resourceFactory,
      }),
    ).toThrow("Could not attach beforeunload.");

    expect(lifecycle.count("pagehide")).toBe(0);
    expect(lifecycle.count("beforeunload")).toBe(0);
    for (const operation of Object.values(operations))
      expect(operation).toHaveBeenCalledTimes(1);
    expect(order.at(-1)).toBe("client.destroy");
  });
});
