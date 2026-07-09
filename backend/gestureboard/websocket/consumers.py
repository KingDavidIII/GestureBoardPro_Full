"""
GestureBoard Pro
WebSocket Consumer
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from channels.generic.websocket import AsyncWebsocketConsumer


class GestureConsumer(AsyncWebsocketConsumer):
    """
    Handles real-time communication between the frontend
    and the GestureBoard backend.
    """

    async def connect(self) -> None:
        await self.accept()

        await self.send_json(
            {
                "type": "connection",
                "status": "connected",
                "project": "GestureBoard Pro",
                "version": "0.1.0-alpha.2",
                "timestamp": self.timestamp(),
            }
        )

    async def disconnect(self, close_code: int) -> None:
        """
        Cleanup when the client disconnects.
        """
        return

    async def receive(
        self,
        text_data: str | None = None,
        bytes_data: bytes | None = None,
    ) -> None:
        """
        Handle incoming websocket messages.
        """

        if not text_data:
            return

        try:
            payload = json.loads(text_data)

        except json.JSONDecodeError:
            await self.send_json(
                {
                    "type": "error",
                    "message": "Invalid JSON payload.",
                    "timestamp": self.timestamp(),
                }
            )
            return

        message_type = payload.get("type")

        if message_type == "ping":
            await self.send_json(
                {
                    "type": "pong",
                    "timestamp": self.timestamp(),
                }
            )
            return

        await self.send_json(
            {
                "type": "echo",
                "payload": payload,
                "timestamp": self.timestamp(),
            }
        )

    async def send_json(self, payload: dict[str, Any]) -> None:
        """
        Send a JSON message to the client.
        """

        await self.send(
            text_data=json.dumps(
                payload,
                separators=(",", ":"),
            )
        )

    @staticmethod
    def timestamp() -> str:
        """
        Returns the current UTC timestamp.
        """

        return datetime.now(UTC).isoformat()
