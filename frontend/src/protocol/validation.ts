import type {
  ErrorMessage,
  GestureEngineDecision,
  GestureLabel,
  GestureResultMessage,
  HandSelectionDecision,
  ProtocolErrorCode,
  ServerMessage,
} from "./messages";

export enum FrontendProtocolErrorCode {
  INVALID_JSON = "INVALID_JSON",
  INVALID_PROTOCOL_MESSAGE = "INVALID_PROTOCOL_MESSAGE",
  UNSUPPORTED_PROTOCOL_VERSION = "UNSUPPORTED_PROTOCOL_VERSION",
  UNSUPPORTED_SERVER_MESSAGE = "UNSUPPORTED_SERVER_MESSAGE",
}

export class FrontendProtocolError extends Error {
  readonly code: FrontendProtocolErrorCode;

  constructor(
    code: FrontendProtocolErrorCode,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "FrontendProtocolError";
    this.code = code;
  }
}

const labels = new Set<GestureLabel>([
  "UNKNOWN",
  "OPEN_PALM",
  "FIST",
  "POINT",
  "PEACE",
  "PINCH",
]);
const decisions = new Set<GestureEngineDecision>([
  "LOW_CONFIDENCE",
  "UNKNOWN",
  "NO_HAND",
  "ACCUMULATING",
  "ACTIVATED",
  "DISPATCHED",
  "UNMAPPED",
  "HELD_SUPPRESSED",
  "COOLDOWN_SUPPRESSED",
  "REPEAT_WAITING",
  "REPEATED",
  "RELEASE_ACCUMULATING",
  "RELEASED",
]);
const selections = new Set<HandSelectionDecision>([
  "NO_HANDS",
  "FIRST_DETECTED",
  "HIGHEST_CONFIDENCE",
  "PREFERRED_HANDEDNESS",
  "PREFERRED_FALLBACK",
  "STICKY_RETAINED",
  "HAND_SWITCHED",
]);
const errorCodes = new Set<ProtocolErrorCode>([
  "invalid_message",
  "invalid_json",
  "unsupported_message",
  "invalid_frame",
  "frame_too_large",
  "runtime_failure",
  "reset_failure",
  "internal_error",
]);

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const nonNegativeInteger = (value: unknown): value is number =>
  finite(value) && Number.isInteger(value) && value >= 0;
const optionalRequestId = (value: unknown): value is string | undefined =>
  value === undefined ||
  (typeof value === "string" && value.length > 0 && value.length <= 128);

function invalid(message: string): never {
  throw new FrontendProtocolError(
    FrontendProtocolErrorCode.INVALID_PROTOCOL_MESSAGE,
    message,
  );
}

function validateGestureResult(
  value: Record<string, unknown>,
): GestureResultMessage {
  const selection = value.selection;
  const gesture = value.gesture;
  if (!nonNegativeInteger(value.sequence) || !finite(value.timestamp))
    invalid("Invalid sequence or timestamp.");
  if (
    !nonNegativeInteger(value.detected_hand_count) ||
    typeof value.action_executed !== "boolean"
  )
    invalid("Invalid result counters.");
  if (
    !record(selection) ||
    !selections.has(selection.decision as HandSelectionDecision)
  )
    invalid("Invalid selection metadata.");
  const identity = selection.identity;
  if (
    identity !== null &&
    (!record(identity) ||
      !nonNegativeInteger(identity.hand_index) ||
      typeof identity.handedness !== "string")
  )
    invalid("Invalid selected identity.");
  const hand = value.hand;
  if (
    hand !== null &&
    (!record(hand) ||
      !nonNegativeInteger(hand.index) ||
      typeof hand.handedness !== "string" ||
      !finite(hand.detection_confidence) ||
      hand.detection_confidence < 0 ||
      hand.detection_confidence > 1)
  )
    invalid("Invalid hand metadata.");
  if (
    !record(gesture) ||
    (gesture.label !== null && !labels.has(gesture.label as GestureLabel)) ||
    !decisions.has(gesture.engine_decision as GestureEngineDecision)
  )
    invalid("Invalid gesture metadata.");
  const dispatch = value.dispatch;
  if (
    dispatch !== null &&
    (!record(dispatch) ||
      !labels.has(dispatch.gesture_label as GestureLabel) ||
      ![null, "TAP_KEY", "HOTKEY", "TYPE_TEXT"].includes(
        dispatch.action_kind as string | null,
      ) ||
      typeof dispatch.executed !== "boolean")
  )
    invalid("Invalid dispatch metadata.");
  return value as unknown as GestureResultMessage;
}

export function validateServerMessage(value: unknown): ServerMessage {
  if (!record(value)) invalid("Server message must be an object.");
  if (value.protocol_version !== 1) {
    throw new FrontendProtocolError(
      FrontendProtocolErrorCode.UNSUPPORTED_PROTOCOL_VERSION,
      "Unsupported protocol version.",
    );
  }
  if (typeof value.type !== "string") invalid("Message type is required.");
  switch (value.type) {
    case "connection.ready":
      return value as unknown as ServerMessage;
    case "gesture.result":
      return validateGestureResult(value);
    case "pong":
    case "runtime.reset.ack":
      if (!optionalRequestId(value.request_id)) invalid("Invalid request_id.");
      return value as unknown as ServerMessage;
    case "error": {
      if (
        !record(value.error) ||
        !errorCodes.has(value.error.code as ProtocolErrorCode) ||
        typeof value.error.message !== "string" ||
        !optionalRequestId(value.request_id)
      )
        invalid("Invalid error message.");
      return value as unknown as ErrorMessage;
    }
    default:
      throw new FrontendProtocolError(
        FrontendProtocolErrorCode.UNSUPPORTED_SERVER_MESSAGE,
        "Unsupported server message type.",
      );
  }
}

export function parseServerMessage(text: string): ServerMessage {
  let value: unknown;
  try {
    value = JSON.parse(text) as unknown;
  } catch (error) {
    throw new FrontendProtocolError(
      FrontendProtocolErrorCode.INVALID_JSON,
      "Server message is not valid JSON.",
      { cause: error },
    );
  }
  return validateServerMessage(value);
}
