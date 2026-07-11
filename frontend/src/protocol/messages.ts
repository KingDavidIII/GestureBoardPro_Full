export type ProtocolVersion = 1;

export const PROTOCOL_VERSION: ProtocolVersion = 1;

export type ProtocolErrorCode =
  | "invalid_message"
  | "invalid_json"
  | "unsupported_message"
  | "invalid_frame"
  | "frame_too_large"
  | "runtime_failure"
  | "reset_failure"
  | "internal_error";

export type HandSelectionDecision =
  | "NO_HANDS"
  | "FIRST_DETECTED"
  | "HIGHEST_CONFIDENCE"
  | "PREFERRED_HANDEDNESS"
  | "PREFERRED_FALLBACK"
  | "STICKY_RETAINED"
  | "HAND_SWITCHED";

export type GestureLabel =
  | "UNKNOWN"
  | "OPEN_PALM"
  | "FIST"
  | "POINT"
  | "PEACE"
  | "PINCH";

export type GestureEngineDecision =
  | "LOW_CONFIDENCE"
  | "UNKNOWN"
  | "NO_HAND"
  | "ACCUMULATING"
  | "ACTIVATED"
  | "DISPATCHED"
  | "UNMAPPED"
  | "HELD_SUPPRESSED"
  | "COOLDOWN_SUPPRESSED"
  | "REPEAT_WAITING"
  | "REPEATED"
  | "RELEASE_ACCUMULATING"
  | "RELEASED";

export interface ConnectionReadyMessage {
  readonly protocol_version: ProtocolVersion;
  readonly type: "connection.ready";
  readonly capabilities?: readonly string[];
}

export interface GestureSelectionMetadata {
  readonly decision: HandSelectionDecision;
  readonly identity: {
    readonly hand_index: number;
    readonly handedness: string;
  } | null;
}

export interface SelectedHandMetadata {
  readonly index: number;
  readonly handedness: string;
  readonly detection_confidence: number;
}

export interface GestureMetadata {
  readonly label: GestureLabel | null;
  readonly engine_decision: GestureEngineDecision;
}

export interface DispatchMetadata {
  readonly gesture_label: GestureLabel;
  readonly action_kind: "TAP_KEY" | "HOTKEY" | "TYPE_TEXT" | null;
  readonly executed: boolean;
}

export interface GestureResultMessage {
  readonly protocol_version: ProtocolVersion;
  readonly type: "gesture.result";
  readonly sequence: number;
  readonly timestamp: number;
  readonly detected_hand_count: number;
  readonly selection: GestureSelectionMetadata;
  readonly hand: SelectedHandMetadata | null;
  readonly gesture: GestureMetadata;
  readonly action_executed: boolean;
  readonly dispatch: DispatchMetadata | null;
  readonly annotation?: {
    readonly enabled: boolean;
    readonly available: boolean;
    readonly format?: "jpeg";
    readonly envelope_version?: number;
    readonly sequence?: number;
    readonly width?: number;
    readonly height?: number;
    readonly byte_length?: number;
    readonly error_code?: string;
  };
}

export interface PongMessage {
  readonly protocol_version: ProtocolVersion;
  readonly type: "pong";
  readonly request_id?: string;
}

export interface RuntimeResetAckMessage {
  readonly protocol_version: ProtocolVersion;
  readonly type: "runtime.reset.ack";
  readonly request_id?: string;
}
export interface AnnotatedFrameSetAckMessage {
  readonly protocol_version: ProtocolVersion;
  readonly type: "annotated_frame.set.ack";
  readonly enabled: boolean;
  readonly request_id?: string;
}

export interface ErrorMessage {
  readonly protocol_version: ProtocolVersion;
  readonly type: "error";
  readonly error: {
    readonly code: ProtocolErrorCode;
    readonly message: string;
  };
  readonly request_id?: string;
}

export type ServerMessage =
  | ConnectionReadyMessage
  | GestureResultMessage
  | PongMessage
  | RuntimeResetAckMessage
  | AnnotatedFrameSetAckMessage
  | ErrorMessage;

export interface PingMessage {
  readonly protocol_version: ProtocolVersion;
  readonly type: "ping";
  readonly request_id?: string;
}

export interface RuntimeResetMessage {
  readonly protocol_version: ProtocolVersion;
  readonly type: "runtime.reset";
  readonly request_id?: string;
}
export interface AnnotatedFrameSetMessage {
  readonly protocol_version: ProtocolVersion;
  readonly type: "annotated_frame.set";
  readonly enabled: boolean;
  readonly request_id?: string;
}

export type ClientControlMessage =
  | PingMessage
  | RuntimeResetMessage
  | AnnotatedFrameSetMessage;
