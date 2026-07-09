"""
GestureBoard Pro
Root WebSocket Routing
"""

from gestureboard.websocket.routing import (
    websocket_urlpatterns as gestureboard_websocket_urlpatterns,
)

websocket_urlpatterns = [
    *gestureboard_websocket_urlpatterns,
]
