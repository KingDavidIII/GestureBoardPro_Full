"""GestureBoard WebSocket endpoint routing."""

from django.urls import path

from .consumers import GestureConsumer

websocket_urlpatterns = [
    path("ws/", GestureConsumer.as_asgi()),
]
