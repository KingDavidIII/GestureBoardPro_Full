"""Transport-neutral decoding and serialization bridge for gesture frames."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Any, Protocol

import cv2
import numpy as np
from gestureboard.mouse.button_output import WindowsMouseButtonApi
from gestureboard.mouse.composition import build_mouse_runtime_dependencies
from gestureboard.mouse.config import (
    GestureMouseRuntimeConfig,
    load_gesture_mouse_config,
)
from gestureboard.mouse.models import MouseOutputError
from gestureboard.mouse.output import WindowsCursorApi
from gestureboard.mouse.ownership import WindowsCursorOwnershipLease
from gestureboard.mouse.runtime import GestureMouseRuntimeCoordinator
from gestureboard.recognition.models import GestureId
from gestureboard.recognition.service import RecognitionService, serialize_recognition

from .annotated_frame_encoder import (
    ANNOTATED_FRAME_ENVELOPE_VERSION,
    AnnotatedFrameBinaryEnvelope,
    AnnotatedFrameEncoder,
)
from .gesture_engine import GestureObservation
from .gesture_runtime import GestureRuntime, GestureRuntimeResult

logger = logging.getLogger(__name__)


class WebSocketRuntimeBridgeStage(StrEnum):
    PAYLOAD_VALIDATION = "payload validation"
    FRAME_DECODING = "frame decoding"
    RUNTIME_PROCESSING = "runtime processing"
    RESULT_SERIALISATION = "result serialisation"
    RESET = "reset"
    LIFECYCLE = "lifecycle"


class WebSocketProtocolMessageType(StrEnum):
    GESTURE_RESULT = "gesture.result"
    ERROR = "error"
    CONNECTION_READY = "connection.ready"
    PING = "ping"
    PONG = "pong"
    RUNTIME_RESET = "runtime.reset"
    RUNTIME_RESET_ACK = "runtime.reset.ack"
    ANNOTATED_FRAME_SET = "annotated_frame.set"
    ANNOTATED_FRAME_SET_ACK = "annotated_frame.set.ack"


class WebSocketProtocolErrorCode(StrEnum):
    INVALID_MESSAGE = "invalid_message"
    INVALID_JSON = "invalid_json"
    UNSUPPORTED_MESSAGE = "unsupported_message"
    INVALID_FRAME = "invalid_frame"
    FRAME_TOO_LARGE = "frame_too_large"
    RUNTIME_FAILURE = "runtime_failure"
    RESET_FAILURE = "reset_failure"
    INTERNAL_ERROR = "internal_error"
    INVALID_ANNOTATION_CONTROL = "invalid_annotation_control"
    ANNOTATION_ENCODING_FAILED = "annotation_encoding_failed"


class WebSocketRuntimeBridgeError(RuntimeError):
    """Typed bridge failure safe for conversion to a protocol error."""

    def __init__(
        self,
        stage: WebSocketRuntimeBridgeStage,
        code: WebSocketProtocolErrorCode,
        message: str,
    ) -> None:
        self.stage = stage
        self.code = code
        self.public_message = message
        super().__init__(f"{stage.value}: {message}")


@dataclass(frozen=True, slots=True)
class WebSocketRuntimeBridgeConfig:
    protocol_version: int = 1
    maximum_encoded_frame_size: int = 5 * 1024 * 1024
    expose_diagnostic_errors: bool = False
    maximum_decoded_width: int | None = None
    maximum_decoded_height: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.protocol_version, bool) or self.protocol_version != 1:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.PAYLOAD_VALIDATION,
                WebSocketProtocolErrorCode.INVALID_MESSAGE,
                "protocol_version must be 1.",
            )
        if (
            isinstance(self.maximum_encoded_frame_size, bool)
            or not isinstance(self.maximum_encoded_frame_size, int)
            or self.maximum_encoded_frame_size < 1
        ):
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.PAYLOAD_VALIDATION,
                WebSocketProtocolErrorCode.INVALID_MESSAGE,
                "maximum_encoded_frame_size must be a positive integer.",
            )
        if not isinstance(self.expose_diagnostic_errors, bool):
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.PAYLOAD_VALIDATION,
                WebSocketProtocolErrorCode.INVALID_MESSAGE,
                "expose_diagnostic_errors must be a bool.",
            )
        for name, value in (
            ("maximum_decoded_width", self.maximum_decoded_width),
            ("maximum_decoded_height", self.maximum_decoded_height),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise WebSocketRuntimeBridgeError(
                    WebSocketRuntimeBridgeStage.FRAME_DECODING,
                    WebSocketProtocolErrorCode.INVALID_FRAME,
                    f"{name} must be a positive integer or None.",
                )


class FrameDecoder(Protocol):
    def decode(self, payload: bytes) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class WebSocketFrameResponse:
    metadata: Mapping[str, object]
    sequence: int
    annotated_envelope: bytes | None = None


class OpenCVFrameDecoder:
    """Decode JPEG/PNG-style encoded bytes into a three-channel BGR frame."""

    def __init__(
        self,
        maximum_width: int | None = None,
        maximum_height: int | None = None,
    ) -> None:
        for name, value in (
            ("maximum_width", maximum_width),
            ("maximum_height", maximum_height),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise WebSocketRuntimeBridgeError(
                    WebSocketRuntimeBridgeStage.FRAME_DECODING,
                    WebSocketProtocolErrorCode.INVALID_FRAME,
                    f"{name} must be a positive integer or None.",
                )
        self.maximum_width = maximum_width
        self.maximum_height = maximum_height

    def decode(self, payload: bytes) -> np.ndarray:
        is_jpeg = payload.startswith(b"\xff\xd8\xff")
        is_png = payload.startswith(b"\x89PNG\r\n\x1a\n")
        if not (is_jpeg or is_png):
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.FRAME_DECODING,
                WebSocketProtocolErrorCode.INVALID_FRAME,
                "Encoded frame must be a JPEG or PNG image.",
            )
        try:
            encoded = np.frombuffer(payload, dtype=np.uint8)
            frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        except Exception as error:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.FRAME_DECODING,
                WebSocketProtocolErrorCode.INVALID_FRAME,
                "Encoded frame could not be decoded.",
            ) from error
        if frame is None:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.FRAME_DECODING,
                WebSocketProtocolErrorCode.INVALID_FRAME,
                "Encoded frame could not be decoded.",
            )
        _validate_decoded_frame(frame, self.maximum_width, self.maximum_height)
        return frame


class WebSocketRuntimeBridge:
    """Decode one payload, run one frame, and return protocol-v1 metadata."""

    def __init__(
        self,
        runtime: GestureRuntime | None = None,
        decoder: FrameDecoder | None = None,
        config: WebSocketRuntimeBridgeConfig | None = None,
        annotated_frame_encoder: AnnotatedFrameEncoder | None = None,
        recognition_service: RecognitionService | None = None,
        mouse_config: GestureMouseRuntimeConfig | None = None,
        mouse_coordinator: GestureMouseRuntimeCoordinator | None = None,
        mouse_windows_api: WindowsCursorApi | None = None,
        mouse_button_windows_api: WindowsMouseButtonApi | None = None,
        mouse_ownership_lease: WindowsCursorOwnershipLease | None = None,
    ) -> None:
        self._owns_runtime = runtime is None
        self._owns_decoder = decoder is None
        self._owns_annotated_frame_encoder = annotated_frame_encoder is None
        self._owns_recognition_service = recognition_service is None
        self._owns_mouse_coordinator = mouse_coordinator is None
        self.runtime = None
        self.decoder = None
        self.annotated_frame_encoder = None
        self.recognition_service = None
        self.mouse_coordinator = None
        try:
            self.config = (
                config if config is not None else WebSocketRuntimeBridgeConfig()
            )
            self.runtime = runtime if runtime is not None else GestureRuntime()
            self.decoder = (
                decoder
                if decoder is not None
                else OpenCVFrameDecoder(
                    self.config.maximum_decoded_width,
                    self.config.maximum_decoded_height,
                )
            )
            self.annotated_frame_encoder = (
                annotated_frame_encoder
                if annotated_frame_encoder is not None
                else AnnotatedFrameEncoder()
            )
            self.recognition_service = (
                recognition_service
                if recognition_service is not None
                else RecognitionService()
            )
            self.mouse_config = (
                mouse_config
                if mouse_config is not None
                else load_gesture_mouse_config()
            )
            self.mouse_coordinator = (
                mouse_coordinator
                if mouse_coordinator is not None
                else self._create_mouse_coordinator(
                    mouse_windows_api=mouse_windows_api,
                    mouse_button_windows_api=mouse_button_windows_api,
                    mouse_ownership_lease=mouse_ownership_lease,
                )
            )
        except Exception:
            self._cleanup_construction_failure()
            raise
        self._annotation_enabled = False
        self._last_sequence: int | None = None
        self._closed = False

    def _create_mouse_coordinator(
        self,
        *,
        mouse_windows_api: WindowsCursorApi | None,
        mouse_button_windows_api: WindowsMouseButtonApi | None,
        mouse_ownership_lease: WindowsCursorOwnershipLease | None,
    ) -> GestureMouseRuntimeCoordinator:
        return build_mouse_runtime_dependencies(
            self.mouse_config,
            owner_id=f"bridge-{id(self)}",
            cursor_api=mouse_windows_api,
            button_api=mouse_button_windows_api,
            ownership_lease=mouse_ownership_lease,
        ).coordinator

    def _cleanup_construction_failure(self) -> None:
        for dependency, owned, operation_name in (
            (
                getattr(self, "recognition_service", None),
                self._owns_recognition_service,
                "reset",
            ),
            (
                getattr(self, "annotated_frame_encoder", None),
                self._owns_annotated_frame_encoder,
                "close",
            ),
            (getattr(self, "decoder", None), self._owns_decoder, "close"),
            (getattr(self, "runtime", None), self._owns_runtime, "close"),
        ):
            operation = getattr(dependency, operation_name, None)
            if owned and callable(operation):
                try:
                    operation()
                except Exception:
                    pass

    def process_frame(
        self,
        payload: bytes,
        *,
        timestamp: float | None = None,
        sequence: int | None = None,
    ) -> Mapping[str, object]:
        return self.process_frame_response(
            payload, timestamp=timestamp, sequence=sequence
        ).metadata

    def process_frame_response(
        self,
        payload: bytes,
        *,
        timestamp: float | None = None,
        sequence: int | None = None,
    ) -> WebSocketFrameResponse:
        self._ensure_open("process a frame")
        self._validate_payload(payload)
        next_sequence = self._sequence(sequence)

        try:
            frame = self.decoder.decode(payload)
        except WebSocketRuntimeBridgeError:
            self._reset_mouse_safely()
            raise
        except Exception as error:
            self._reset_mouse_safely()
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.FRAME_DECODING,
                WebSocketProtocolErrorCode.INVALID_FRAME,
                "Encoded frame could not be decoded.",
            ) from error
        _validate_decoded_frame(
            frame,
            self.config.maximum_decoded_width,
            self.config.maximum_decoded_height,
        )

        try:
            runtime_result = self.runtime.process(frame, timestamp=timestamp)
        except Exception as error:
            self._reset_mouse_safely()
            message = "Gesture runtime could not process the frame."
            if self.config.expose_diagnostic_errors:
                message = f"{message} {str(error)}"
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.RUNTIME_PROCESSING,
                WebSocketProtocolErrorCode.RUNTIME_FAILURE,
                message,
            ) from error

        try:
            result = self._serialize(runtime_result, next_sequence)
        except WebSocketRuntimeBridgeError:
            raise
        except Exception as error:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.RESULT_SERIALISATION,
                WebSocketProtocolErrorCode.INTERNAL_ERROR,
                "Runtime result could not be serialised.",
            ) from error
        recognition_result = None
        try:
            mediapipe_result = runtime_result.pipeline_result.mediapipe_result
            recognition_result = (
                self.recognition_service.process(
                    mediapipe_result,
                    frame_sequence=next_sequence,
                )
                if mediapipe_result is not None
                else None
            )
            result["recognition"] = (
                dict(serialize_recognition(recognition_result))
                if recognition_result is not None
                else None
            )
        except Exception:
            # Recognition is optional telemetry: its failure cannot poison a frame.
            logger.exception(
                "Recognition metadata could not be produced for frame %s", next_sequence
            )
            result["recognition"] = None
        self._process_mouse(recognition_result, next_sequence)
        envelope: bytes | None = None
        annotation: dict[str, object] = {
            "enabled": self._annotation_enabled,
            "available": False,
        }
        if self._annotation_enabled:
            try:
                encoded = self.annotated_frame_encoder.encode(
                    runtime_result.annotated_frame
                )
                envelope = AnnotatedFrameBinaryEnvelope(
                    next_sequence, encoded.width, encoded.height, encoded.jpeg_bytes
                ).to_bytes()
                annotation = {
                    "enabled": True,
                    "available": True,
                    "format": "jpeg",
                    "envelope_version": ANNOTATED_FRAME_ENVELOPE_VERSION,
                    "sequence": next_sequence,
                    "width": encoded.width,
                    "height": encoded.height,
                    "byte_length": encoded.payload_size,
                }
            except Exception:
                annotation = {
                    "enabled": True,
                    "available": False,
                    "error_code": WebSocketProtocolErrorCode.ANNOTATION_ENCODING_FAILED.value,
                }
        result["annotation"] = annotation
        self._last_sequence = next_sequence
        return WebSocketFrameResponse(result, next_sequence, envelope)

    @property
    def is_annotation_enabled(self) -> bool:
        return self._annotation_enabled

    def set_annotation_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.PAYLOAD_VALIDATION,
                WebSocketProtocolErrorCode.INVALID_ANNOTATION_CONTROL,
                "enabled must be a bool.",
            )
        self._annotation_enabled = enabled

    def _validate_payload(self, payload: bytes) -> None:
        if not isinstance(payload, bytes):
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.PAYLOAD_VALIDATION,
                WebSocketProtocolErrorCode.INVALID_FRAME,
                "Frame payload must be bytes.",
            )
        if not payload:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.PAYLOAD_VALIDATION,
                WebSocketProtocolErrorCode.INVALID_FRAME,
                "Frame payload must not be empty.",
            )
        if len(payload) > self.config.maximum_encoded_frame_size:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.PAYLOAD_VALIDATION,
                WebSocketProtocolErrorCode.FRAME_TOO_LARGE,
                "Encoded frame exceeds the configured size limit.",
            )

    def _sequence(self, sequence: int | None) -> int:
        if sequence is None:
            return 0 if self._last_sequence is None else self._last_sequence + 1
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.PAYLOAD_VALIDATION,
                WebSocketProtocolErrorCode.INVALID_MESSAGE,
                "sequence must be a non-negative integer.",
            )
        if self._last_sequence is not None and sequence < self._last_sequence:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.PAYLOAD_VALIDATION,
                WebSocketProtocolErrorCode.INVALID_MESSAGE,
                "sequence cannot be older than the last accepted sequence.",
            )
        return sequence

    def _serialize(
        self,
        result: GestureRuntimeResult,
        sequence: int,
    ) -> dict[str, object]:
        selected = result.selected_hand
        identity = result.selected_identity
        observation = result.observation
        gesture_label = (
            observation.prediction.label.value
            if isinstance(observation, GestureObservation)
            else None
        )
        dispatch = result.engine_result.dispatch_result
        dispatch_data: dict[str, object] | None = None
        if dispatch is not None:
            dispatch_data = {
                "gesture_label": dispatch.gesture_label.value,
                "action_kind": dispatch.action.kind.value if dispatch.action else None,
                "executed": dispatch.executed,
            }
        return {
            "protocol_version": self.config.protocol_version,
            "type": WebSocketProtocolMessageType.GESTURE_RESULT.value,
            "sequence": sequence,
            "timestamp": result.timestamp,
            "detected_hand_count": result.detected_hand_count,
            "selection": {
                "decision": result.selection_decision.value,
                "identity": (
                    {
                        "hand_index": identity.hand_index,
                        "handedness": identity.handedness,
                    }
                    if identity is not None
                    else None
                ),
            },
            "hand": (
                {
                    "index": selected.hand_index,
                    "handedness": selected.handedness,
                    "detection_confidence": selected.confidence,
                }
                if selected is not None
                else None
            ),
            "gesture": {
                "label": gesture_label,
                "engine_decision": result.engine_result.decision.value,
            },
            "action_executed": result.action_executed,
            "dispatch": dispatch_data,
        }

    def reset(self) -> None:
        self._ensure_open("reset")
        try:
            self.runtime.reset()
        except Exception as error:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.RESET,
                WebSocketProtocolErrorCode.RESET_FAILURE,
                "Gesture runtime could not be reset.",
            ) from error
        self.recognition_service.reset()
        self.mouse_coordinator.tracking_lost()
        self._last_sequence = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failures: list[Exception] = []
        operations = []
        if self._owns_recognition_service:
            operations.append(self.recognition_service.reset)
        if self._owns_mouse_coordinator:
            operations.append(self.mouse_coordinator.close)
        for operation in operations:
            try:
                operation()
            except Exception as error:
                failures.append(error)
        for dependency, owned in (
            (self.runtime, self._owns_runtime),
            (self.decoder, self._owns_decoder),
            (self.annotated_frame_encoder, self._owns_annotated_frame_encoder),
        ):
            close = getattr(dependency, "close", None)
            if owned and callable(close):
                try:
                    close()
                except Exception as error:
                    failures.append(error)
        if failures:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.LIFECYCLE,
                WebSocketProtocolErrorCode.INTERNAL_ERROR,
                "One or more owned bridge dependencies could not be closed.",
            ) from failures[0]

    def _process_mouse(self, recognition_result: object, sequence: int) -> None:
        """Move only for a stabilized point from the cached selected hand."""

        timestamp_ms = 0
        selected = None
        stable = None
        if recognition_result is not None:
            timestamp = getattr(recognition_result, "timestamp_ms", 0)
            if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
                timestamp_ms = max(0, int(timestamp))
            selected = getattr(recognition_result, "primary_hand", None)
            stable = getattr(recognition_result, "stable", None)
            if getattr(stable, "gesture_id", None) is not GestureId.POINT:
                selected = None
        try:
            self.mouse_coordinator.process(
                selected,
                timestamp_ms=timestamp_ms,
                stable_gesture=getattr(stable, "gesture_id", None),
            )
        except MouseOutputError:
            logger.warning("Gesture mouse output failed for frame %s", sequence)

    def _reset_mouse_safely(self) -> None:
        try:
            self.mouse_coordinator.tracking_lost()
        except Exception:
            logger.warning("Gesture mouse tracking reset failed.")

    def _ensure_open(self, operation: str) -> None:
        if self._closed:
            raise WebSocketRuntimeBridgeError(
                WebSocketRuntimeBridgeStage.LIFECYCLE,
                WebSocketProtocolErrorCode.INTERNAL_ERROR,
                f"Cannot {operation} after bridge closure.",
            )

    def __enter__(self) -> WebSocketRuntimeBridge:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _validate_decoded_frame(
    frame: Any,
    maximum_width: int | None,
    maximum_height: int | None,
) -> None:
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise WebSocketRuntimeBridgeError(
            WebSocketRuntimeBridgeStage.FRAME_DECODING,
            WebSocketProtocolErrorCode.INVALID_FRAME,
            "Decoded frame must be a three-channel BGR array.",
        )
    height, width = frame.shape[:2]
    if width < 1 or height < 1:
        raise WebSocketRuntimeBridgeError(
            WebSocketRuntimeBridgeStage.FRAME_DECODING,
            WebSocketProtocolErrorCode.INVALID_FRAME,
            "Decoded frame dimensions must be positive.",
        )
    if maximum_width is not None and width > maximum_width:
        raise WebSocketRuntimeBridgeError(
            WebSocketRuntimeBridgeStage.FRAME_DECODING,
            WebSocketProtocolErrorCode.INVALID_FRAME,
            "Decoded frame exceeds the configured width limit.",
        )
    if maximum_height is not None and height > maximum_height:
        raise WebSocketRuntimeBridgeError(
            WebSocketRuntimeBridgeStage.FRAME_DECODING,
            WebSocketProtocolErrorCode.INVALID_FRAME,
            "Decoded frame exceeds the configured height limit.",
        )
