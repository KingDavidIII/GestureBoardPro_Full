import { beforeEach, describe, expect, it } from "vitest";

import { DiagnosticDashboard } from "../src/dashboard";
import { GestureWebSocketClient } from "../src/websocket";
import { FakeWebSocket } from "./fake-websocket";

describe("DiagnosticDashboard", () => {
  let root: HTMLDivElement;
  let socket: FakeWebSocket;
  let dashboard: DiagnosticDashboard;

  beforeEach(() => {
    root = document.createElement("div");
    socket = new FakeWebSocket();
    dashboard = new DiagnosticDashboard(
      root,
      new GestureWebSocketClient("ws://board.test/ws/", {
        socketFactory: () => socket,
      }),
    );
  });

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
});
