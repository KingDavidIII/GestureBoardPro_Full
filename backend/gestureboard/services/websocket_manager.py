"""
GestureBoard Pro
WebSocket Manager

Central manager for active WebSocket connections.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from channels.generic.websocket import AsyncWebsocketConsumer


class WebSocketManager:
    """
    Maintains all active websocket connections.

    This class is responsible for:

    • registering clients
    • removing disconnected clients
    • broadcasting messages
    • sending messages to individual clients
    """

    def __init__(self) -> None:
        self._connections: set[AsyncWebsocketConsumer] = set()
        self._lock = asyncio.Lock()

    @property
    def total_connections(self) -> int:
        """
        Returns the number of connected clients.
        """
        return len(self._connections)

    async def register(
        self,
        consumer: AsyncWebsocketConsumer,
    ) -> None:
        """
        Register a new websocket connection.
        """

        async with self._lock:
            self._connections.add(consumer)

    async def unregister(
        self,
        consumer: AsyncWebsocketConsumer,
    ) -> None:
        """
        Remove a websocket connection.
        """

        async with self._lock:
            self._connections.discard(consumer)

    async def send(
        self,
        consumer: AsyncWebsocketConsumer,
        payload: dict,
    ) -> None:
        """
        Send a JSON payload to one client.
        """

        await consumer.send_json(payload)

    async def broadcast(
        self,
        payload: dict,
    ) -> None:
        """
        Broadcast a JSON payload to every client.
        """

        if not self._connections:
            return

        await asyncio.gather(
            *(connection.send_json(payload) for connection in self._connections),
            return_exceptions=True,
        )

    async def disconnect_all(self) -> None:
        """
        Close every active websocket.
        """

        if not self._connections:
            return

        await asyncio.gather(
            *(connection.close() for connection in self._connections),
            return_exceptions=True,
        )

        self._connections.clear()

    def get_connections(
        self,
    ) -> Iterable[AsyncWebsocketConsumer]:
        """
        Returns a read-only iterable of connections.
        """

        return tuple(self._connections)


websocket_manager = WebSocketManager()
