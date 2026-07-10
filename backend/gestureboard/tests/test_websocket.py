"""Channels integration tests for the versioned gesture protocol."""

from unittest.mock import patch

from channels.testing import WebsocketCommunicator
from config.asgi import application
from django.test import SimpleTestCase

from gestureboard.services.websocket_runtime_bridge import (
    WebSocketProtocolErrorCode,
    WebSocketRuntimeBridgeError,
    WebSocketRuntimeBridgeStage,
)
from gestureboard.websocket.consumers import GestureConsumer


class FakeBridge:
    def __init__(self, name: str = "bridge") -> None:
        self.name = name
        self.frames: list[bytes] = []
        self.events: list[str] = []
        self.close_count = 0
        self.reset_count = 0
        self.error: Exception | None = None

    def process_frame(self, payload: bytes):
        self.frames.append(payload)
        self.events.append(f"frame:{payload.decode(errors='ignore')}")
        if self.error:
            raise self.error
        return {
            "protocol_version": 1,
            "type": "gesture.result",
            "sequence": len(self.frames) - 1,
            "timestamp": 1.0,
            "detected_hand_count": 0,
            "selection": {"decision": "NO_HANDS", "identity": None},
            "hand": None,
            "gesture": {"label": None, "engine_decision": "NO_HAND"},
            "action_executed": False,
            "dispatch": None,
        }

    def reset(self) -> None:
        self.reset_count += 1
        self.events.append("reset")

    def close(self) -> None:
        self.close_count += 1


async def connected(bridge: FakeBridge):
    factory = patch.object(
        GestureConsumer,
        "bridge_factory",
        staticmethod(lambda: bridge),
    )
    factory.start()
    communicator = WebsocketCommunicator(application, "/ws/")
    is_connected, _ = await communicator.connect()
    if not is_connected:
        factory.stop()
        raise AssertionError("WebSocket route did not connect")
    ready = await communicator.receive_json_from()
    if ready["type"] != "connection.ready":
        factory.stop()
        raise AssertionError("Consumer did not send connection.ready")
    return communicator, factory


class GestureConsumerTests(SimpleTestCase):
    async def test_route_binary_result_and_disconnect_cleanup(self) -> None:
        bridge = FakeBridge()
        communicator, factory = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"frame")
            response = await communicator.receive_json_from()
            self.assertEqual(response["type"], "gesture.result")
            self.assertEqual(bridge.frames, [b"frame"])
            self.assertTrue(await communicator.receive_nothing(timeout=0.05))
            await communicator.disconnect()
            self.assertEqual(bridge.close_count, 1)
        finally:
            factory.stop()

    async def test_each_connection_receives_a_distinct_bridge(self) -> None:
        bridges: list[FakeBridge] = []

        def factory():
            bridge = FakeBridge(str(len(bridges)))
            bridges.append(bridge)
            return bridge

        with patch.object(
            GestureConsumer,
            "bridge_factory",
            staticmethod(factory),
        ):
            first = WebsocketCommunicator(application, "/ws/")
            second = WebsocketCommunicator(application, "/ws/")
            self.assertTrue((await first.connect())[0])
            self.assertTrue((await second.connect())[0])
            await first.receive_json_from()
            await second.receive_json_from()
            await first.send_to(bytes_data=b"first")
            await second.send_to(bytes_data=b"second")
            await first.receive_json_from()
            await second.receive_json_from()
            await first.disconnect()
            await second.disconnect()

        self.assertEqual(len(bridges), 2)
        self.assertIsNot(bridges[0], bridges[1])
        self.assertEqual(bridges[0].frames, [b"first"])
        self.assertEqual(bridges[1].frames, [b"second"])

    async def test_bridge_construction_failure_rejects_connection(self) -> None:
        def fail():
            raise RuntimeError("construction failed")

        with patch.object(
            GestureConsumer,
            "bridge_factory",
            staticmethod(fail),
        ):
            communicator = WebsocketCommunicator(application, "/ws/")
            is_connected, close_code = await communicator.connect()
        self.assertFalse(is_connected)
        self.assertEqual(close_code, 1011)

    async def test_bridge_error_returns_typed_error_without_disconnect(self) -> None:
        bridge = FakeBridge()
        bridge.error = WebSocketRuntimeBridgeError(
            WebSocketRuntimeBridgeStage.FRAME_DECODING,
            WebSocketProtocolErrorCode.INVALID_FRAME,
            "Encoded frame could not be decoded.",
        )
        communicator, factory = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"bad")
            error = await communicator.receive_json_from()
            self.assertEqual(error["error"]["code"], "invalid_frame")
            await communicator.send_json_to({"protocol_version": 1, "type": "ping"})
            self.assertEqual((await communicator.receive_json_from())["type"], "pong")
            await communicator.disconnect()
        finally:
            factory.stop()

    async def test_ping_preserves_request_id_without_using_bridge(self) -> None:
        bridge = FakeBridge()
        communicator, factory = await connected(bridge)
        try:
            await communicator.send_json_to(
                {"protocol_version": 1, "type": "ping", "request_id": "client-1"}
            )
            response = await communicator.receive_json_from()
            self.assertEqual(response["type"], "pong")
            self.assertEqual(response["request_id"], "client-1")
            self.assertEqual(bridge.frames, [])
            await communicator.disconnect()
        finally:
            factory.stop()

    async def test_reset_acknowledges_and_calls_bridge_once(self) -> None:
        bridge = FakeBridge()
        communicator, factory = await connected(bridge)
        try:
            await communicator.send_json_to(
                {
                    "protocol_version": 1,
                    "type": "runtime.reset",
                    "request_id": "reset-1",
                }
            )
            response = await communicator.receive_json_from()
            self.assertEqual(response["type"], "runtime.reset.ack")
            self.assertEqual(response["request_id"], "reset-1")
            self.assertEqual(bridge.reset_count, 1)
            await communicator.disconnect()
        finally:
            factory.stop()

    async def test_malformed_and_unsupported_controls_return_typed_errors(self) -> None:
        bridge = FakeBridge()
        communicator, factory = await connected(bridge)
        try:
            await communicator.send_to(text_data="{")
            malformed = await communicator.receive_json_from()
            self.assertEqual(malformed["error"]["code"], "invalid_json")
            commands = (
                (
                    {"protocol_version": 1, "type": "frame", "data": "base64"},
                    "unsupported_message",
                ),
                ({"protocol_version": 2, "type": "ping"}, "unsupported_message"),
                (
                    {"protocol_version": 1, "type": "ping", "request_id": ""},
                    "invalid_message",
                ),
                (
                    {"protocol_version": 1, "type": "ping", "request_id": "x" * 129},
                    "invalid_message",
                ),
            )
            for payload, expected in commands:
                await communicator.send_json_to(payload)
                response = await communicator.receive_json_from()
                self.assertEqual(response["error"]["code"], expected)
            await communicator.disconnect()
        finally:
            factory.stop()

    async def test_frames_and_reset_are_processed_in_receive_order(self) -> None:
        bridge = FakeBridge()
        communicator, factory = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"one")
            await communicator.receive_json_from()
            await communicator.send_json_to(
                {"protocol_version": 1, "type": "runtime.reset"}
            )
            await communicator.receive_json_from()
            await communicator.send_to(bytes_data=b"two")
            await communicator.receive_json_from()
            self.assertEqual(bridge.events, ["frame:one", "reset", "frame:two"])
            await communicator.disconnect()
        finally:
            factory.stop()
