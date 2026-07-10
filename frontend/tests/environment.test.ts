import { describe, expect, it } from "vitest";

import {
  FrontendConfigurationError,
  resolveWebSocketUrl,
} from "../src/config/environment";

describe("resolveWebSocketUrl", () => {
  it("derives a secure URL from an HTTPS location", () => {
    expect(
      resolveWebSocketUrl(undefined, {
        protocol: "https:",
        host: "board.test",
      }),
    ).toBe("wss://board.test/ws/");
  });

  it("uses a valid configured WebSocket URL", () => {
    expect(resolveWebSocketUrl("ws://localhost:8000/ws/")).toBe(
      "ws://localhost:8000/ws/",
    );
  });

  it("rejects non-WebSocket configured URLs", () => {
    expect(() => resolveWebSocketUrl("https://board.test/ws/")).toThrow(
      FrontendConfigurationError,
    );
  });
});
