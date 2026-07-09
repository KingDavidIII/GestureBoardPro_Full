"""
GestureBoard Pro
Root WebSocket Routing
"""

from django.urls import include, path

websocket_urlpatterns = [
    path(
        "ws/",
        include("gestureboard.websocket.routing"),
    ),
]
