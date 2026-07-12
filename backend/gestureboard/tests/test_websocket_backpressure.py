"""Channels integration tests for latest-frame backpressure."""

import asyncio
import struct
import threading
from unittest.mock import patch

from channels.testing import WebsocketCommunicator
from config.asgi import application
from django.test import SimpleTestCase

from gestureboard.services.websocket_runtime_bridge import WebSocketFrameResponse
from gestureboard.websocket.consumers import GestureConsumer


def result(sequence: int) -> dict[str, object]:
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
        "annotation": {"enabled": False, "available": False},
    }


class BlockingBridge:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.frames: list[bytes] = []
        self.active = 0
        self.maximum_active = 0
        self.enabled = False
        self.fail_next = False

    def process_frame_response(self, payload: bytes) -> WebSocketFrameResponse:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.frames.append(payload)
        self.entered.set()
        self.release.wait(timeout=5)
        try:
            if self.fail_next:
                self.fail_next = False
                raise ValueError("private failure")
            sequence = len(self.frames) - 1
            metadata = result(sequence)
            envelope = None
            if self.enabled:
                metadata["annotation"] = {
                    "enabled": True,
                    "available": True,
                    "sequence": sequence,
                    "width": 1,
                    "height": 1,
                    "byte_length": 1,
                }
                envelope = (
                    b"GBF1"
                    + bytes([1, 1, 0, 0])
                    + struct.pack(">IHHI", sequence, 1, 1, 1)
                    + b"x"
                )
            return WebSocketFrameResponse(metadata, sequence, envelope)
        finally:
            self.active -= 1

    def set_annotation_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def reset(self) -> None:
        return None

    def close(self) -> None:
        self.release.set()


async def connected(bridge: BlockingBridge):
    patcher = patch.object(
        GestureConsumer, "bridge_factory", staticmethod(lambda: bridge)
    )
    patcher.start()
    communicator = WebsocketCommunicator(application, "/ws/")
    assert (await communicator.connect())[0]
    await communicator.receive_json_from()
    return communicator, patcher


class WebSocketBackpressureTests(SimpleTestCase):
    async def test_legacy_process_frame_bridge_remains_compatible(self) -> None:
        class LegacyBridge:
            def __init__(self) -> None:
                self.frames = []
                self.close_count = 0

            def process_frame(self, payload: bytes) -> dict[str, object]:
                self.frames.append(payload)
                return result(len(self.frames) - 1)

            def reset(self) -> None:
                return None

            def close(self) -> None:
                self.close_count += 1

        bridge = LegacyBridge()
        with patch.object(
            GestureConsumer, "bridge_factory", staticmethod(lambda: bridge)
        ):
            communicator = WebsocketCommunicator(application, "/ws/")
            self.assertTrue((await communicator.connect())[0])
            await communicator.receive_json_from()
            await communicator.send_to(bytes_data=b"legacy")
            response = await communicator.receive_json_from()
            self.assertEqual(bridge.frames, [b"legacy"])
            self.assertIn("scheduler", response)
            self.assertTrue(await communicator.receive_nothing(timeout=0.01))
            await communicator.disconnect()
        self.assertEqual(bridge.close_count, 1)

    async def test_binary_submission_returns_before_blocked_processing_completes(
        self,
    ) -> None:
        bridge = BlockingBridge()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"one")
            await asyncio.to_thread(bridge.entered.wait, 1)
            await communicator.send_to(bytes_data=b"two")
            self.assertEqual(bridge.frames, [b"one"])
            self.assertTrue(await communicator.receive_nothing(timeout=0.01))
            bridge.release.set()
            await communicator.receive_json_from()
            await communicator.receive_json_from()
            self.assertEqual(bridge.frames, [b"one", b"two"])
            self.assertEqual(bridge.maximum_active, 1)
            await communicator.disconnect()
        finally:
            bridge.release.set()
            patcher.stop()

    async def test_annotation_state_is_isolated_between_connections(self) -> None:
        first_bridge = BlockingBridge()
        second_bridge = BlockingBridge()
        first_bridge.release.set()
        second_bridge.release.set()
        bridges = iter((first_bridge, second_bridge))
        with patch.object(
            GestureConsumer, "bridge_factory", staticmethod(lambda: next(bridges))
        ):
            first = WebsocketCommunicator(application, "/ws/")
            second = WebsocketCommunicator(application, "/ws/")
            self.assertTrue((await first.connect())[0])
            await first.receive_json_from()
            self.assertTrue((await second.connect())[0])
            await second.receive_json_from()
            await first.send_json_to(
                {"protocol_version": 1, "type": "annotated_frame.set", "enabled": True}
            )
            await first.receive_json_from()
            await first.send_to(bytes_data=b"a")
            self.assertTrue((await first.receive_json_from())["annotation"]["enabled"])
            self.assertTrue((await first.receive_from()).startswith(b"GBF1"))
            await second.send_to(bytes_data=b"b")
            self.assertFalse(
                (await second.receive_json_from())["annotation"]["enabled"]
            )
            self.assertTrue(await second.receive_nothing(timeout=0.01))
            await first.disconnect()
            await second.send_to(bytes_data=b"b2")
            self.assertEqual(
                (await second.receive_json_from())["type"], "gesture.result"
            )
            await second.disconnect()

    async def test_disconnect_discards_retained_pending_frame(self) -> None:
        bridge = BlockingBridge()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"one")
            await asyncio.to_thread(bridge.entered.wait, 1)
            await communicator.send_to(bytes_data=b"two")
            await communicator.disconnect()
            bridge.release.set()
            self.assertEqual(bridge.frames, [b"one"])
        finally:
            bridge.release.set()
            patcher.stop()

    async def test_blocked_connection_does_not_block_another_connection(self) -> None:
        slow = BlockingBridge()
        fast = BlockingBridge()
        fast.release.set()
        bridges = iter((slow, fast))
        with patch.object(
            GestureConsumer, "bridge_factory", staticmethod(lambda: next(bridges))
        ):
            first = WebsocketCommunicator(application, "/ws/")
            second = WebsocketCommunicator(application, "/ws/")
            self.assertTrue((await first.connect())[0])
            await first.receive_json_from()
            self.assertTrue((await second.connect())[0])
            await second.receive_json_from()
            await first.send_to(bytes_data=b"slow")
            await asyncio.to_thread(slow.entered.wait, 1)
            await second.send_json_to({"protocol_version": 1, "type": "ping"})
            self.assertEqual((await second.receive_json_from())["type"], "pong")
            await second.send_to(bytes_data=b"fast")
            self.assertEqual(
                (await second.receive_json_from())["type"], "gesture.result"
            )
            self.assertEqual(fast.frames, [b"fast"])
            slow.release.set()
            await first.receive_json_from()
            await first.disconnect()
            await second.disconnect()

    async def test_failure_does_not_stop_later_valid_processing(self) -> None:
        bridge = BlockingBridge()
        bridge.release.set()
        bridge.fail_next = True
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"bad")
            error = await communicator.receive_json_from()
            self.assertEqual(error["type"], "error")
            await communicator.send_to(bytes_data=b"good")
            response = await communicator.receive_json_from()
            self.assertEqual(response["type"], "gesture.result")
            self.assertEqual(response["scheduler"]["processed_frames"], 2)
            self.assertEqual(response["scheduler"]["processing_failures"], 1)
            await communicator.disconnect()
        finally:
            bridge.release.set()
            patcher.stop()

    async def test_annotation_disabled_sends_json_only(self) -> None:
        bridge = BlockingBridge()
        bridge.release.set()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"frame")
            response = await communicator.receive_json_from()
            self.assertEqual(
                response["annotation"], {"enabled": False, "available": False}
            )
            self.assertTrue(await communicator.receive_nothing(timeout=0.01))
            await communicator.disconnect()
        finally:
            patcher.stop()

    async def test_successful_result_has_complete_scheduler_metadata(self) -> None:
        bridge = BlockingBridge()
        bridge.release.set()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"frame")
            scheduler = (await communicator.receive_json_from())["scheduler"]
            self.assertEqual(
                set(scheduler),
                {
                    "received_frames",
                    "processed_frames",
                    "dropped_frames",
                    "processing_failures",
                    "pending_frames",
                    "queue_delay_ms",
                    "processing_time_ms",
                },
            )
            await communicator.disconnect()
        finally:
            patcher.stop()

    async def test_fresh_connection_starts_with_fresh_scheduler_counters(self) -> None:
        first_bridge = BlockingBridge()
        first_bridge.release.set()
        first, first_patcher = await connected(first_bridge)
        try:
            await first.send_to(bytes_data=b"one")
            await first.receive_json_from()
            await first.send_to(bytes_data=b"two")
            await first.receive_json_from()
            await first.disconnect()
        finally:
            first_patcher.stop()
        second_bridge = BlockingBridge()
        second_bridge.release.set()
        second, second_patcher = await connected(second_bridge)
        try:
            await second.send_to(bytes_data=b"one")
            scheduler = (await second.receive_json_from())["scheduler"]
            self.assertEqual(scheduler["received_frames"], 1)
            self.assertEqual(scheduler["processed_frames"], 1)
            self.assertEqual(scheduler["dropped_frames"], 0)
            self.assertEqual(scheduler["processing_failures"], 0)
            self.assertEqual(scheduler["pending_frames"], 0)
            self.assertGreaterEqual(scheduler["queue_delay_ms"], 0)
            self.assertGreaterEqual(scheduler["processing_time_ms"], 0)
            await second.disconnect()
        finally:
            second_patcher.stop()

    async def test_annotation_json_and_gbf1_sequences_are_correlated(self) -> None:
        bridge = BlockingBridge()
        bridge.release.set()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_json_to(
                {"protocol_version": 1, "type": "annotated_frame.set", "enabled": True}
            )
            await communicator.receive_json_from()
            await communicator.send_to(bytes_data=b"frame")
            metadata = await communicator.receive_json_from()
            envelope = await communicator.receive_from()
            sequence, width, height, payload_length = struct.unpack(
                ">IHHI", envelope[8:20]
            )
            self.assertEqual(metadata["sequence"], sequence)
            self.assertEqual(metadata["annotation"]["sequence"], sequence)
            self.assertEqual(metadata["annotation"]["width"], width)
            self.assertEqual(metadata["annotation"]["height"], height)
            self.assertEqual(metadata["annotation"]["byte_length"], payload_length)
            self.assertEqual(len(envelope), 20 + payload_length)
            await communicator.disconnect()
        finally:
            patcher.stop()

    async def test_control_is_responsive_while_processing_is_blocked(self) -> None:
        bridge = BlockingBridge()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"one")
            await asyncio.to_thread(bridge.entered.wait, 1)
            self.assertTrue(await communicator.receive_nothing(timeout=0.02))
            await communicator.send_json_to(
                {
                    "protocol_version": 1,
                    "type": "annotated_frame.set",
                    "enabled": True,
                }
            )
            self.assertEqual(
                (await communicator.receive_json_from())["type"],
                "annotated_frame.set.ack",
            )
            bridge.release.set()
            await communicator.receive_json_from()
            await communicator.receive_from()
            await communicator.disconnect()
        finally:
            bridge.release.set()
            patcher.stop()

    async def test_rapid_frames_retain_only_newest_pending(self) -> None:
        bridge = BlockingBridge()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"one")
            await asyncio.to_thread(bridge.entered.wait, 1)
            for payload in (b"two", b"three", b"four"):
                await communicator.send_to(bytes_data=payload)
            await communicator.send_json_to({"protocol_version": 1, "type": "ping"})
            self.assertEqual((await communicator.receive_json_from())["type"], "pong")
            bridge.release.set()
            first = await communicator.receive_json_from()
            second = await communicator.receive_json_from()
            self.assertEqual(bridge.frames, [b"one", b"four"])
            self.assertEqual(second["scheduler"]["dropped_frames"], 2)
            self.assertEqual(first["scheduler"]["received_frames"], 4)
            await communicator.disconnect()
        finally:
            bridge.release.set()
            patcher.stop()

    async def test_bridge_calls_never_overlap(self) -> None:
        bridge = BlockingBridge()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"one")
            await asyncio.to_thread(bridge.entered.wait, 1)
            await communicator.send_to(bytes_data=b"two")
            bridge.release.set()
            await communicator.receive_json_from()
            await communicator.receive_json_from()
            self.assertEqual(bridge.maximum_active, 1)
            await communicator.disconnect()
        finally:
            bridge.release.set()
            patcher.stop()

    async def test_json_and_gbf1_are_adjacent(self) -> None:
        bridge = BlockingBridge()
        bridge.release.set()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_json_to(
                {"protocol_version": 1, "type": "annotated_frame.set", "enabled": True}
            )
            await communicator.receive_json_from()
            await communicator.send_to(bytes_data=b"one")
            metadata = await communicator.receive_json_from()
            envelope = await communicator.receive_from()
            self.assertEqual(metadata["type"], "gesture.result")
            self.assertTrue(envelope.startswith(b"GBF1"))
            await communicator.disconnect()
        finally:
            patcher.stop()

    async def test_disconnect_prevents_late_result(self) -> None:
        bridge = BlockingBridge()
        communicator, patcher = await connected(bridge)
        try:
            await communicator.send_to(bytes_data=b"one")
            await asyncio.to_thread(bridge.entered.wait, 1)
            await communicator.disconnect()
            bridge.release.set()
            self.assertEqual(bridge.frames, [b"one"])
        finally:
            bridge.release.set()
            patcher.stop()

    async def test_metrics_are_connection_local(self) -> None:
        first_bridge = BlockingBridge()
        first_bridge.release.set()
        first, first_patch = await connected(first_bridge)
        try:
            await first.send_to(bytes_data=b"one")
            self.assertEqual(
                (await first.receive_json_from())["scheduler"]["received_frames"], 1
            )
            await first.disconnect()
        finally:
            first_patch.stop()
        second_bridge = BlockingBridge()
        second_bridge.release.set()
        second, second_patch = await connected(second_bridge)
        try:
            await second.send_to(bytes_data=b"one")
            self.assertEqual(
                (await second.receive_json_from())["scheduler"]["received_frames"], 1
            )
            await second.disconnect()
        finally:
            second_patch.stop()
