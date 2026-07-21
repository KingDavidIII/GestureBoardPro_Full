import { describe, expect, it, vi } from "vitest";

import {
  ApplicationRuntime,
  type ApplicationLifecycleTarget,
  type ApplicationRuntimeResources,
} from "../src/application/application-runtime";

type LifecycleEventType = "pagehide" | "beforeunload";

class FakeLifecycleTarget implements ApplicationLifecycleTarget {
  private readonly listeners = new Map<
    LifecycleEventType,
    Set<EventListener>
  >();

  addEventListener(type: LifecycleEventType, listener: EventListener): void {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: LifecycleEventType, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type: LifecycleEventType): void {
    for (const listener of [...(this.listeners.get(type) ?? [])])
      listener(new Event(type));
  }

  count(type: LifecycleEventType): number {
    return this.listeners.get(type)?.size ?? 0;
  }
}

const resources = (
  order: string[],
): {
  value: ApplicationRuntimeResources;
  operations: Record<string, ReturnType<typeof vi.fn>>;
} => {
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

  return {
    value: {
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
    },
    operations,
  };
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

  it("continues cleanup after a resource throws", () => {
    const order: string[] = [];
    const { value, operations } = resources(order);
    const failure = new Error("dashboard cleanup failed");
    const dashboardOperation = operations.dashboard;

    if (!dashboardOperation) {
      throw new Error(
        "Test invariant violated: dashboard operation mock was not created.",
      );
    }

    dashboardOperation.mockImplementationOnce(() => {
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

  it("rejects lifecycle attachment after shutdown", () => {
    const { value } = resources([]);
    const runtime = new ApplicationRuntime(value, vi.fn());
    runtime.destroy();

    expect(() => runtime.attachLifecycle(new FakeLifecycleTarget())).toThrow(
      "Cannot attach lifecycle events after application shutdown.",
    );
  });
});
