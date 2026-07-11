"""Channels consumer for the versioned gesture runtime protocol."""

from __future__ import annotations

import json
from typing import Any

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from gestureboard.services.websocket_manager import websocket_manager
from gestureboard.services.websocket_runtime_bridge import (
    WebSocketProtocolErrorCode,
    WebSocketProtocolMessageType,
    WebSocketRuntimeBridge,
    WebSocketRuntimeBridgeError,
)

PROTOCOL_VERSION = 1
MAXIMUM_REQUEST_ID_LENGTH = 128


class GestureConsumer(AsyncWebsocketConsumer):
    """Maintain one ordered, synchronous runtime bridge per connection."""

    bridge_factory = staticmethod(WebSocketRuntimeBridge)

    async def connect(self) -> None:
        self._bridge_closed = False
        try:
            self.bridge = await sync_to_async(
                self.bridge_factory,
                thread_sensitive=True,
            )()
        except Exception:
            await self.close(code=1011)
            return
        await self.accept()
        await websocket_manager.register(self)
        await self.send_json(
            {
                "protocol_version": PROTOCOL_VERSION,
                "type": WebSocketProtocolMessageType.CONNECTION_READY.value,
                "capabilities": ["annotated_frame.jpeg.v1"],
            }
        )

    async def disconnect(self, close_code: int) -> None:
        await websocket_manager.unregister(self)
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
            process_response = getattr(self.bridge, "process_frame_response", None)
            if callable(process_response):
                response = await sync_to_async(process_response, thread_sensitive=True)(
                    payload
                )
            else:
                response = await sync_to_async(
                    self.bridge.process_frame, thread_sensitive=True
                )(payload)
        except WebSocketRuntimeBridgeError as error:
            await self._send_error(error.code, error.public_message)
        except Exception:
            await self._send_error(
                WebSocketProtocolErrorCode.INTERNAL_ERROR,
                "An internal error occurred while processing the frame.",
            )
        else:
            if isinstance(response, dict):
                await self.send_json(response)
            else:
                await self.send_json(dict(response.metadata))
                if response.annotated_envelope is not None:
                    await self.send(bytes_data=response.annotated_envelope)

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
            await self.send_json(response)
            return
        if message_type == WebSocketProtocolMessageType.PING.value:
            response: dict[str, object] = {
                "protocol_version": PROTOCOL_VERSION,
                "type": WebSocketProtocolMessageType.PONG.value,
            }
            if request_id is not None:
                response["request_id"] = request_id
            await self.send_json(response)
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
            await self.send_json(response)
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
        await self.send_json(response)

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
