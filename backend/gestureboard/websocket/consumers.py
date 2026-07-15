"""Channels consumer for the versioned gesture runtime protocol."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from gestureboard.services.latest_frame_scheduler import (
    FrameSchedulerMetrics,
    LatestFrameScheduler,
    LatestFrameSchedulerError,
)
from gestureboard.services.websocket_manager import websocket_manager
from gestureboard.services.websocket_runtime_bridge import (
    WebSocketProtocolErrorCode,
    WebSocketProtocolMessageType,
    WebSocketRuntimeBridge,
    WebSocketRuntimeBridgeError,
)

PROTOCOL_VERSION = 1
MAXIMUM_REQUEST_ID_LENGTH = 128
MAXIMUM_DISTINCT_FAILURE_TRACEBACKS = 3
logger = logging.getLogger(__name__)


class GestureConsumer(AsyncWebsocketConsumer):
    """Maintain one ordered, synchronous runtime bridge per connection."""

    bridge_factory = staticmethod(WebSocketRuntimeBridge)
    scheduler_factory = staticmethod(LatestFrameScheduler)

    async def connect(self) -> None:
        self._bridge_closed = False
        self._connection_closed = False
        self._failure_signatures: set[tuple[str, str]] = set()
        self._failure_summary_logged = False
        self._outbound_lock = asyncio.Lock()
        try:
            self.bridge = await sync_to_async(
                self.bridge_factory,
                thread_sensitive=True,
            )()
        except Exception:
            await self.close(code=1011)
            return
        process_response = getattr(self.bridge, "process_frame_response", None)
        processor = (
            process_response
            if callable(process_response)
            else self.bridge.process_frame
        )
        self.scheduler = self.scheduler_factory(
            processor,
            self._frame_processed,
            self._frame_failed,
        )
        self.scheduler.start()
        await self.accept()
        await websocket_manager.register(self)
        await self._send_json_ordered(
            {
                "protocol_version": PROTOCOL_VERSION,
                "type": WebSocketProtocolMessageType.CONNECTION_READY.value,
                "capabilities": ["annotated_frame.jpeg.v1", "gesture.recognition.v1"],
            }
        )

    async def disconnect(self, close_code: int) -> None:
        self._connection_closed = True
        await websocket_manager.unregister(self)
        scheduler = getattr(self, "scheduler", None)
        if scheduler is not None:
            await scheduler.close()
            self.scheduler = None
        await self._close_bridge()

    async def receive(
        self,
        text_data: str | None = None,
        bytes_data: bytes | None = None,
    ) -> None:
        if bytes_data is not None:
            await self._receive_frame(bytes_data)
            return
        if text_data is not None:
            await self._receive_control(text_data)
            return
        await self._send_error(
            WebSocketProtocolErrorCode.INVALID_MESSAGE,
            "A binary frame or JSON control message is required.",
        )

    async def _receive_frame(self, payload: bytes) -> None:
        try:
            self.scheduler.submit(payload)
        except (AttributeError, LatestFrameSchedulerError):
            if not self._connection_closed:
                await self._send_error(
                    WebSocketProtocolErrorCode.INTERNAL_ERROR,
                    "The frame scheduler is unavailable.",
                )

    async def _frame_processed(
        self, response: object, metrics: FrameSchedulerMetrics
    ) -> None:
        if self._connection_closed:
            return
        if isinstance(response, dict):
            metadata = dict(response)
            envelope = None
        else:
            metadata = dict(response.metadata)  # type: ignore[attr-defined]
            envelope = response.annotated_envelope  # type: ignore[attr-defined]
        metadata["scheduler"] = {
            "received_frames": metrics.received_frames,
            "processed_frames": metrics.processed_frames,
            "dropped_frames": metrics.dropped_frames,
            "processing_failures": metrics.processing_failures,
            "pending_frames": metrics.pending_frames,
            "queue_delay_ms": metrics.queue_delay_ms,
            "processing_time_ms": metrics.processing_time_ms,
        }
        async with self._outbound_lock:
            if self._connection_closed:
                return
            await self.send_json(metadata)
            if envelope is not None and not self._connection_closed:
                await self.send(bytes_data=envelope)

    async def _frame_failed(
        self, error: Exception, metrics: FrameSchedulerMetrics
    ) -> None:
        if self._connection_closed:
            return
        self._log_frame_failure(error, metrics)
        if isinstance(error, WebSocketRuntimeBridgeError):
            await self._send_error(error.code, error.public_message)
        else:
            await self._send_error(
                WebSocketProtocolErrorCode.INTERNAL_ERROR,
                "An internal error occurred while processing the frame.",
            )

    def _log_frame_failure(
        self, error: Exception, metrics: FrameSchedulerMetrics
    ) -> None:
        signature = (type(error).__name__, str(error))
        if signature in self._failure_signatures:
            if not self._failure_summary_logged:
                logger.warning(
                    "Suppressing repeated frame-processing tracebacks for connection=%s after first occurrence.",
                    getattr(self, "channel_name", "unknown"),
                )
                self._failure_summary_logged = True
            return
        if len(self._failure_signatures) >= MAXIMUM_DISTINCT_FAILURE_TRACEBACKS:
            if not self._failure_summary_logged:
                logger.warning(
                    "Suppressing further distinct frame-processing tracebacks for connection=%s after %s signatures.",
                    getattr(self, "channel_name", "unknown"),
                    MAXIMUM_DISTINCT_FAILURE_TRACEBACKS,
                )
                self._failure_summary_logged = True
            return
        self._failure_signatures.add(signature)
        context = self._failure_context(error, metrics)
        try:
            raise error
        except Exception:
            logger.exception("Frame processing failed: %s", context)

    def _failure_context(
        self, error: Exception, metrics: FrameSchedulerMetrics
    ) -> dict[str, object]:
        bridge = getattr(self, "bridge", None)
        runtime = getattr(bridge, "runtime", None)
        pipeline = getattr(runtime, "pipeline", None)
        processor = getattr(pipeline, "processor", None)
        task_engine = getattr(processor, "_task_engine", None)
        result = getattr(processor, "last_mediapipe_result", None)
        selection = getattr(result, "selection", None)
        return {
            "connection": getattr(self, "channel_name", "unknown"),
            "frame_attempt": metrics.processed_frames,
            "received_frames": metrics.received_frames,
            "exception_type": type(error).__name__,
            "bridge_stage": getattr(getattr(error, "stage", None), "value", None),
            "bridge_code": getattr(getattr(error, "code", None), "value", None),
            "recognition_engine": (
                type(task_engine).__name__ if task_engine else "legacy_hands"
            ),
            "selected_task_hand": bool(getattr(selection, "primary_hand", None)),
            "annotation_transport_enabled": bool(
                getattr(bridge, "is_annotation_enabled", False)
            ),
            "landmark_overlay_renderer_active": bool(task_engine),
        }

    async def _receive_control(self, text_data: str) -> None:
        try:
            payload = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            await self._send_error(
                WebSocketProtocolErrorCode.INVALID_JSON,
                "Control message must contain valid JSON.",
            )
            return
        if not isinstance(payload, dict):
            await self._send_error(
                WebSocketProtocolErrorCode.INVALID_MESSAGE,
                "Control message must be a JSON object.",
            )
            return

        request_id = payload.get("request_id")
        if request_id is not None and (
            not isinstance(request_id, str)
            or not request_id
            or len(request_id) > MAXIMUM_REQUEST_ID_LENGTH
        ):
            await self._send_error(
                WebSocketProtocolErrorCode.INVALID_MESSAGE,
                "request_id must be a non-empty string of at most 128 characters.",
            )
            return
        version = payload.get("protocol_version")
        if isinstance(version, bool) or version != PROTOCOL_VERSION:
            await self._send_error(
                WebSocketProtocolErrorCode.UNSUPPORTED_MESSAGE,
                "Unsupported protocol version.",
                request_id=request_id,
            )
            return

        message_type = payload.get("type")
        if message_type == WebSocketProtocolMessageType.ANNOTATED_FRAME_SET.value:
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                await self._send_error(
                    WebSocketProtocolErrorCode.INVALID_ANNOTATION_CONTROL,
                    "enabled must be a boolean.",
                    request_id=request_id,
                )
                return
            try:
                await sync_to_async(
                    self.bridge.set_annotation_enabled, thread_sensitive=True
                )(enabled)
            except WebSocketRuntimeBridgeError as error:
                await self._send_error(
                    error.code, error.public_message, request_id=request_id
                )
                return
            response: dict[str, object] = {
                "protocol_version": PROTOCOL_VERSION,
                "type": WebSocketProtocolMessageType.ANNOTATED_FRAME_SET_ACK.value,
                "enabled": enabled,
            }
            if request_id is not None:
                response["request_id"] = request_id
            await self._send_json_ordered(response)
            return
        if message_type == WebSocketProtocolMessageType.PING.value:
            response: dict[str, object] = {
                "protocol_version": PROTOCOL_VERSION,
                "type": WebSocketProtocolMessageType.PONG.value,
            }
            if request_id is not None:
                response["request_id"] = request_id
            await self._send_json_ordered(response)
            return
        if message_type == WebSocketProtocolMessageType.RUNTIME_RESET.value:
            try:
                await sync_to_async(self.bridge.reset, thread_sensitive=True)()
            except WebSocketRuntimeBridgeError as error:
                await self._send_error(
                    error.code,
                    error.public_message,
                    request_id=request_id,
                )
                return
            except Exception:
                await self._send_error(
                    WebSocketProtocolErrorCode.RESET_FAILURE,
                    "Gesture runtime could not be reset.",
                    request_id=request_id,
                )
                return
            response = {
                "protocol_version": PROTOCOL_VERSION,
                "type": WebSocketProtocolMessageType.RUNTIME_RESET_ACK.value,
            }
            if request_id is not None:
                response["request_id"] = request_id
            await self._send_json_ordered(response)
            return

        await self._send_error(
            WebSocketProtocolErrorCode.UNSUPPORTED_MESSAGE,
            "Unsupported control message type; frames must be sent as binary data.",
            request_id=request_id,
        )

    async def _send_error(
        self,
        code: WebSocketProtocolErrorCode,
        message: str,
        *,
        request_id: str | None = None,
    ) -> None:
        response: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "type": WebSocketProtocolMessageType.ERROR.value,
            "error": {"code": code.value, "message": message},
        }
        if request_id is not None:
            response["request_id"] = request_id
        await self._send_json_ordered(response)

    async def _send_json_ordered(self, payload: dict[str, Any]) -> None:
        async with self._outbound_lock:
            if not self._connection_closed:
                await self.send_json(payload)

    async def _close_bridge(self) -> None:
        if self._bridge_closed:
            return
        self._bridge_closed = True
        bridge = getattr(self, "bridge", None)
        if bridge is not None:
            try:
                await sync_to_async(bridge.close, thread_sensitive=True)()
            except Exception:
                pass

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(payload, separators=(",", ":")))
