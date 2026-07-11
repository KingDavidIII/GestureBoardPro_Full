export interface ReconnectPolicyConfig {
  readonly enabled?: boolean;
  readonly initialDelayMs?: number;
  readonly multiplier?: number;
  readonly maximumDelayMs?: number;
  readonly maximumAttempts?: number;
  readonly jitterRatio?: number;
}

export interface ReconnectPolicy {
  readonly enabled: boolean;
  readonly initialDelayMs: number;
  readonly multiplier: number;
  readonly maximumDelayMs: number;
  readonly maximumAttempts: number;
  readonly jitterRatio: number;
}

export class ReconnectPolicyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReconnectPolicyError";
  }
}

export const DEFAULT_RECONNECT_POLICY: Readonly<ReconnectPolicy> =
  Object.freeze({
    enabled: true,
    initialDelayMs: 500,
    multiplier: 2,
    maximumDelayMs: 8_000,
    maximumAttempts: 5,
    jitterRatio: 0.2,
  });

export function createReconnectPolicy(
  config: ReconnectPolicyConfig = {},
): Readonly<ReconnectPolicy> {
  const policy = {
    ...DEFAULT_RECONNECT_POLICY,
    ...config,
  };
  if (typeof policy.enabled !== "boolean")
    throw new ReconnectPolicyError("enabled must be a boolean.");
  for (const [name, value] of [
    ["initialDelayMs", policy.initialDelayMs],
    ["maximumDelayMs", policy.maximumDelayMs],
  ] as const) {
    if (!Number.isFinite(value) || value < 0)
      throw new ReconnectPolicyError(
        `${name} must be finite and non-negative.`,
      );
  }
  if (!Number.isFinite(policy.multiplier) || policy.multiplier < 1)
    throw new ReconnectPolicyError("multiplier must be finite and at least 1.");
  if (policy.maximumDelayMs < policy.initialDelayMs)
    throw new ReconnectPolicyError(
      "maximumDelayMs must not be below initialDelayMs.",
    );
  if (
    !Number.isSafeInteger(policy.maximumAttempts) ||
    policy.maximumAttempts < 0
  )
    throw new ReconnectPolicyError(
      "maximumAttempts must be a non-negative safe integer.",
    );
  if (
    !Number.isFinite(policy.jitterRatio) ||
    policy.jitterRatio < 0 ||
    policy.jitterRatio > 1
  )
    throw new ReconnectPolicyError("jitterRatio must be between 0 and 1.");
  return Object.freeze(policy);
}

export function calculateReconnectDelay(
  policy: ReconnectPolicy,
  attempt: number,
  randomValue: number,
): number {
  if (!Number.isSafeInteger(attempt) || attempt < 1)
    throw new ReconnectPolicyError("attempt must be a positive safe integer.");
  if (!Number.isFinite(randomValue) || randomValue < 0 || randomValue > 1)
    throw new ReconnectPolicyError("randomValue must be between 0 and 1.");
  const exponential = Math.min(
    policy.maximumDelayMs,
    policy.initialDelayMs * policy.multiplier ** (attempt - 1),
  );
  const jitter = 1 + (randomValue * 2 - 1) * policy.jitterRatio;
  return Math.min(
    policy.maximumDelayMs,
    Math.max(0, Math.round(exponential * jitter)),
  );
}

export interface ReconnectTimerApi {
  set(callback: () => void, delayMs: number): unknown;
  clear(handle: unknown): void;
}

export const browserReconnectTimers: ReconnectTimerApi = {
  set: (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
  clear: (handle) => globalThis.clearTimeout(handle as number),
};
