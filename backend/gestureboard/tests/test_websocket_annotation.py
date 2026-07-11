"""Focused Channels regressions for optional annotated-frame feedback."""

import struct
from unittest.mock import patch

from channels.testing import WebsocketCommunicator
from config.asgi import application
from django.test import SimpleTestCase

from gestureboard.services.websocket_runtime_bridge import WebSocketFrameResponse
from gestureboard.websocket.consumers import GestureConsumer


def metadata(
    sequence: int = 0, *, enabled: bool = False, available: bool = False
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "type": "gesture.result",
        "sequence": sequence,
        "timestamp": 1.0,
        "detected_hand_count": 0,
        "selection": {"decision": "NO_HANDS", "identity": None},
        "hand": None,
        "gesture": {"label": None, "engine_decision": "NO_HAND"},
        "action_executed": False,
        "dispatch": None,
        "annotation": {
            "enabled": enabled,
            "available": available,
            **(
                {"sequence": sequence, "width": 2, "height": 1, "byte_length": 2}
                if available
                else {}
            ),
        },
    }


class ResponseBridge:
    def __init__(self) -> None:
        self.enabled = False
        self.frames = 0
        self.close_count = 0
        self.reset_count = 0

    def set_annotation_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def process_frame_response(self, payload: bytes) -> WebSocketFrameResponse:
        self.frames += 1
        sequence = self.frames - 1
        if self.enabled:
            binary = (
                b"GBF1"
                + bytes([1, 1, 0, 0])
                + struct.pack(">IHHI", sequence, 2, 1, 2)
                + b"ok"
            )
            return WebSocketFrameResponse(
                metadata(sequence, enabled=True, available=True), sequence, binary
            )
        return WebSocketFrameResponse(metadata(sequence), sequence)

    def reset(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        self.close_count += 1


async def connect(bridge: ResponseBridge):
    patcher = patch.object(
        GestureConsumer, "bridge_factory", staticmethod(lambda: bridge)
    )
    patcher.start()
    communicator = WebsocketCommunicator(application, "/ws/")
    assert (await communicator.connect())[0]
    return communicator, patcher, await communicator.receive_json_from()


class AnnotationConsumerTests(SimpleTestCase):
    async def test_connection_ready_advertises_annotation_capability(self) -> None:
        c, p, ready = await connect(ResponseBridge())
        try:
            self.assertEqual(ready["capabilities"], ["annotated_frame.jpeg.v1"])
            self.assertTrue(await c.receive_nothing(timeout=0.01))
            await c.disconnect()
        finally:
            p.stop()

    async def test_annotation_can_be_enabled_and_acknowledged(self) -> None:
        b = ResponseBridge()
        c, p, _ = await connect(b)
        try:
            await c.send_json_to(
                {
                    "protocol_version": 1,
                    "type": "annotated_frame.set",
                    "enabled": True,
                    "request_id": "a",
                }
            )
            r = await c.receive_json_from()
            self.assertEqual(
                r,
                {
                    "protocol_version": 1,
                    "type": "annotated_frame.set.ack",
                    "enabled": True,
                    "request_id": "a",
                },
            )
            self.assertTrue(b.enabled)
            await c.disconnect()
        finally:
            p.stop()

    async def test_annotation_can_be_disabled_and_acknowledged(self) -> None:
        b = ResponseBridge()
        c, p, _ = await connect(b)
        try:
            for enabled in (True, False, False):
                await c.send_json_to(
                    {
                        "protocol_version": 1,
                        "type": "annotated_frame.set",
                        "enabled": enabled,
                    }
                )
                self.assertEqual((await c.receive_json_from())["enabled"], enabled)
            self.assertFalse(b.enabled)
            await c.disconnect()
        finally:
            p.stop()

    async def test_annotation_control_rejects_null(self) -> None:
        await self._invalid(None)

    async def test_annotation_control_rejects_integer_zero(self) -> None:
        await self._invalid(0)

    async def test_annotation_control_rejects_integer_one(self) -> None:
        await self._invalid(1)

    async def test_annotation_control_rejects_string(self) -> None:
        await self._invalid("true")

    async def test_annotation_control_rejects_object(self) -> None:
        await self._invalid({})

    async def test_annotation_control_rejects_array(self) -> None:
        await self._invalid([])

    async def test_annotation_control_rejects_unsupported_protocol_version(
        self,
    ) -> None:
        b = ResponseBridge()
        c, p, _ = await connect(b)
        try:
            await c.send_json_to(
                {"protocol_version": 2, "type": "annotated_frame.set", "enabled": True}
            )
            self.assertEqual((await c.receive_json_from())["type"], "error")
            self.assertFalse(b.enabled)
            await c.disconnect()
        finally:
            p.stop()

    async def _invalid(self, enabled: object) -> None:
        b = ResponseBridge()
        c, p, _ = await connect(b)
        try:
            await c.send_json_to(
                {
                    "protocol_version": 1,
                    "type": "annotated_frame.set",
                    "enabled": enabled,
                }
            )
            self.assertEqual((await c.receive_json_from())["type"], "error")
            self.assertFalse(b.enabled)
            await c.disconnect()
        finally:
            p.stop()

    async def test_disabled_annotation_sends_json_only(self) -> None:
        b = ResponseBridge()
        c, p, _ = await connect(b)
        try:
            await c.send_to(bytes_data=b"x")
            self.assertFalse((await c.receive_json_from())["annotation"]["enabled"])
            self.assertTrue(await c.receive_nothing(timeout=0.01))
            await c.disconnect()
        finally:
            p.stop()

    async def test_enabled_annotation_sends_json_before_gbf1(self) -> None:
        b = ResponseBridge()
        c, p, _ = await connect(b)
        try:
            await c.send_json_to(
                {"protocol_version": 1, "type": "annotated_frame.set", "enabled": True}
            )
            await c.receive_json_from()
            await c.send_to(bytes_data=b"x")
            self.assertTrue((await c.receive_json_from())["annotation"]["available"])
            self.assertEqual(
                (await c.receive_from()),
                b"GBF1"
                + bytes([1, 1, 0, 0])
                + struct.pack(">IHHI", 0, 2, 1, 2)
                + b"ok",
            )
            await c.disconnect()
        finally:
            p.stop()

    async def test_annotation_sequences_match(self) -> None:
        await self.test_enabled_annotation_sends_json_before_gbf1()

    async def test_reset_preserves_annotation_enablement(self) -> None:
        b = ResponseBridge()
        c, p, _ = await connect(b)
        try:
            await c.send_json_to(
                {"protocol_version": 1, "type": "annotated_frame.set", "enabled": True}
            )
            await c.receive_json_from()
            await c.send_json_to({"protocol_version": 1, "type": "runtime.reset"})
            self.assertEqual((await c.receive_json_from())["type"], "runtime.reset.ack")
            self.assertTrue(b.enabled)
            await c.disconnect()
        finally:
            p.stop()

    async def test_disconnect_closes_bridge_once(self) -> None:
        b = ResponseBridge()
        c, p, _ = await connect(b)
        try:
            await c.disconnect()
            self.assertEqual(b.close_count, 1)
        finally:
            p.stop()
