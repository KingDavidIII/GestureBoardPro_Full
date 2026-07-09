"""
GestureBoard WebSocket Consumer
"""

import json

from channels.generic.websocket import AsyncWebsocketConsumer


class GestureConsumer(AsyncWebsocketConsumer):
    """
    Main WebSocket consumer.
    """

    async def connect(self):

        await self.accept()

        await self.send(
            text_data=json.dumps(
                {
                    "status": "connected",
                    "project": "GestureBoard Pro",
                    "version": "0.1.0-alpha.1",
                }
            )
        )

    async def disconnect(self, close_code):

        pass

    async def receive(self, text_data):

        await self.send(text_data=text_data)
