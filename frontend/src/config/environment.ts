import type { WebSocketLocation } from "../types/common";

export enum FrontendConfigurationErrorCode {
  INVALID_URL = "INVALID_URL",
}

export class FrontendConfigurationError extends Error {
  readonly code: FrontendConfigurationErrorCode;

  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "FrontendConfigurationError";
    this.code = FrontendConfigurationErrorCode.INVALID_URL;
  }
}

export function resolveWebSocketUrl(
  configuredUrl: string | undefined,
  location: WebSocketLocation = window.location,
): string {
  const candidate = configuredUrl?.trim();
  if (!candidate) {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${location.host}/ws/`;
  }
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== "ws:" && parsed.protocol !== "wss:") {
      throw new FrontendConfigurationError(
        "WebSocket URL must use ws: or wss:.",
      );
    }
    return parsed.toString();
  } catch (error) {
    if (error instanceof FrontendConfigurationError) throw error;
    throw new FrontendConfigurationError("WebSocket URL is malformed.", {
      cause: error,
    });
  }
}

export const websocketUrl = (): string =>
  resolveWebSocketUrl(import.meta.env.VITE_GESTUREBOARD_WS_URL);
