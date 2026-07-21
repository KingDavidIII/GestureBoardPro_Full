"""Cross-stack version-1 WebSocket JSON contract fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from channels.testing import WebsocketCommunicator
from config.asgi import application
from django.test import SimpleTestCase

from gestureboard.recognition.models import (
    GestureCandidate,
    GestureId,
    GestureTransition,
    TransitionKind,
)
from gestureboard.recognition.observations import (
    Handedness,
    HandObservation,
    Landmark3D,
)
from gestureboard.recognition.service import RecognitionFrameResult
from gestureboard.services.action_dispatcher import DispatchResult
from gestureboard.services.annotated_frame_encoder import AnnotatedFrameEncodingResult
from gestureboard.services.gesture_classifier import (
    FingerState,
    GestureFeatures,
    GestureLabel,
    GesturePrediction,
)
from gestureboard.services.gesture_engine import (
    GestureEngineDecision,
    GestureEngineResult,
    GestureObservation,
    NeutralGestureObservation,
)
from gestureboard.services.gesture_pipeline import (
    GesturePipelineResult,
    HandGestureResult,
)
from gestureboard.services.gesture_runtime import (
    GestureRuntimeResult,
    HandSelectionDecision,
    SelectedHandIdentity,
)
from gestureboard.services.keyboard_controller import (
    KeyboardAction,
    KeyboardExecutionResult,
)
from gestureboard.services.websocket_runtime_bridge import (
    WebSocketProtocolErrorCode,
    WebSocketProtocolMessageType,
    WebSocketRuntimeBridge,
)
from gestureboard.websocket.consumers import GestureConsumer


def load_fixtures(filename: str) -> list[dict[str, object]]:
    path = (
        Path(__file__).resolve().parents[3]
        / "contracts"
        / "gesture-protocol"
        / "v1"
        / "fixtures"
        / filename
    )
    return json.loads(path.read_text(encoding="utf-8"))


class ContractBridge:
    def __init__(self) -> None:
        self.annotation_enabled = False
        self.reset_count = 0

    def process_frame(self, payload: bytes) -> dict[str, object]:
        raise AssertionError("Control contract tests must not submit binary frames.")

    def set_annotation_enabled(self, enabled: bool) -> None:
        self.annotation_enabled = enabled

    def reset(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        pass


class StaticDecoder:
    def decode(self, payload: bytes) -> np.ndarray:
        return np.zeros((480, 640, 3), dtype=np.uint8)


class StaticRuntime:
    def __init__(self, result: GestureRuntimeResult) -> None:
        self.result = result

    def process(
        self, frame: np.ndarray, *, timestamp: float | None = None
    ) -> GestureRuntimeResult:
        return self.result


class StaticRecognitionService:
    def __init__(self, result: RecognitionFrameResult) -> None:
        self.result = result

    def process(self, result: object, *, frame_sequence: int) -> RecognitionFrameResult:
        return self.result

    def reset(self) -> None:
        pass


class StaticAnnotationEncoder:
    def encode(self, frame: np.ndarray) -> AnnotatedFrameEncodingResult:
        return AnnotatedFrameEncodingResult(b"fixture-jpeg", 640, 480, 12)


class NullMouseCoordinator:
    def process(
        self,
        selected: object,
        *,
        timestamp_ms: int,
        stable_gesture: GestureId | None,
    ) -> None:
        pass

    def tracking_lost(self) -> None:
        pass


def production_legacy_runtime_result() -> GestureRuntimeResult:
    pipeline = GesturePipelineResult(np.zeros((480, 640, 3), dtype=np.uint8), ())
    observation = NeutralGestureObservation()
    engine = GestureEngineResult(
        observation,
        GestureEngineDecision.NO_HAND,
        None,
        0,
        None,
        0,
        1000.25,
    )
    return GestureRuntimeResult(
        pipeline,
        None,
        None,
        HandSelectionDecision.NO_HANDS,
        observation,
        engine,
    )


def production_bridge(
    runtime_result: GestureRuntimeResult | None = None,
) -> WebSocketRuntimeBridge:
    finger = FingerState(True, False, 1.0, 1.0)
    prediction = GesturePrediction(
        GestureLabel.PINCH,
        GestureFeatures(finger, finger, finger, finger, finger, 0.1, (1, 1, 1, 1, 1)),
    )
    observation = GestureObservation(prediction, 0.98)
    selected = HandGestureResult(0, "Right", 0.98, (), prediction)
    pipeline = GesturePipelineResult(
        np.zeros((480, 640, 3), dtype=np.uint8), (selected,), object()
    )
    dispatch = DispatchResult(
        GestureLabel.PINCH,
        KeyboardAction.tap("a"),
        KeyboardExecutionResult(KeyboardAction.tap("a")),
    )
    engine = GestureEngineResult(
        observation,
        GestureEngineDecision.ACTIVATED,
        GestureLabel.PINCH,
        3,
        GestureLabel.PINCH,
        0,
        1002.75,
        dispatch,
    )
    rich_runtime_result = GestureRuntimeResult(
        pipeline,
        selected,
        SelectedHandIdentity(0, "Right"),
        HandSelectionDecision.HIGHEST_CONFIDENCE,
        observation,
        engine,
    )
    landmarks = tuple(Landmark3D(0, 0, 0) for _ in range(21))
    primary = HandObservation(landmarks, 0, Handedness.RIGHT, 0.98, 0.98, 1.0, 1.0)
    candidate = GestureCandidate(GestureId.PINCH, 0.85, "isolated_thumb_index_contact")
    recognition = RecognitionFrameResult(
        1,
        9,
        1,
        primary,
        candidate,
        candidate,
        GestureTransition(
            4, TransitionKind.ACTIVATED, None, GestureId.PINCH, 0.85, 1000
        ),
        880,
        3,
        1000,
    )
    return WebSocketRuntimeBridge(
        runtime=StaticRuntime(runtime_result or rich_runtime_result),
        decoder=StaticDecoder(),
        annotated_frame_encoder=StaticAnnotationEncoder(),
        recognition_service=StaticRecognitionService(recognition),
        mouse_coordinator=NullMouseCoordinator(),
    )


class ProtocolContractTests(SimpleTestCase):
    def test_shared_server_fixtures_define_the_version_1_contract(self) -> None:
        fixtures = load_fixtures("server-messages.json")
        error_fixtures = load_fixtures("server-error-messages.json")
        messages = [fixture["message"] for fixture in fixtures]
        message_types = {message["type"] for message in messages}  # type: ignore[index]
        self.assertEqual(
            message_types | {"error"},
            {
                WebSocketProtocolMessageType.CONNECTION_READY.value,
                WebSocketProtocolMessageType.GESTURE_RESULT.value,
                WebSocketProtocolMessageType.PONG.value,
                WebSocketProtocolMessageType.RUNTIME_RESET_ACK.value,
                WebSocketProtocolMessageType.ANNOTATED_FRAME_SET_ACK.value,
                WebSocketProtocolMessageType.ERROR.value,
            },
        )
        ready = next(
            message for message in messages if message["type"] == "connection.ready"
        )  # type: ignore[index]
        self.assertEqual(
            ready["capabilities"],  # type: ignore[index]
            ["annotated_frame.jpeg.v1", "gesture.recognition.v1"],
        )
        for message in messages:
            self.assertEqual(message["protocol_version"], 1)  # type: ignore[index]
        results = [
            message for message in messages if message["type"] == "gesture.result"
        ]  # type: ignore[index]
        required = {
            "protocol_version",
            "type",
            "sequence",
            "timestamp",
            "detected_hand_count",
            "selection",
            "hand",
            "gesture",
            "action_executed",
            "dispatch",
        }
        for result in results:
            self.assertTrue(required.issubset(result))
        scheduler = next(
            result["scheduler"] for result in results if "scheduler" in result
        )
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
        annotation = next(
            result["annotation"] for result in results if "annotation" in result
        )
        self.assertEqual(
            set(annotation),
            {
                "enabled",
                "available",
                "format",
                "envelope_version",
                "sequence",
                "width",
                "height",
                "byte_length",
            },
        )
        recognition = next(
            result["recognition"] for result in results if "recognition" in result
        )
        self.assertEqual(recognition["schema_version"], 1)
        self.assertEqual(
            {fixture["error"]["code"] for fixture in error_fixtures},
            {code.value for code in WebSocketProtocolErrorCode},
        )

    async def test_real_bridge_and_consumer_construct_fixture_contract_fields(
        self,
    ) -> None:
        bridge = production_bridge()
        fixtures = load_fixtures("server-messages.json")
        legacy = next(
            item for item in fixtures if item["name"] == "gesture-result-legacy"
        )["message"]
        fixture = next(
            item for item in fixtures if item["name"] == "gesture-result-recognition"
        )["message"]
        annotation_fixture = next(
            item for item in fixtures if item["name"] == "gesture-result-annotation"
        )["message"]
        legacy_bridge = production_bridge(production_legacy_runtime_result())
        legacy_message = legacy_bridge._serialize(legacy_bridge.runtime.result, 7)
        self.assertEqual(set(legacy_message), set(legacy))
        self.assertEqual(legacy_message, legacy)
        bridge.set_annotation_enabled(True)
        with patch.object(
            GestureConsumer,
            "bridge_factory",
            staticmethod(lambda: bridge),
        ):
            communicator = WebsocketCommunicator(application, "/ws/")
            self.assertTrue((await communicator.connect())[0])
            await communicator.receive_json_from()
            await communicator.send_to(bytes_data=b"fixture-frame")
            response = await communicator.receive_json_from()
            await communicator.receive_from()
            await communicator.disconnect()
        for field in (
            "protocol_version",
            "type",
            "selection",
            "hand",
            "gesture",
            "dispatch",
            "recognition",
        ):
            self.assertEqual(response[field], fixture[field])
        self.assertEqual(
            set(response),
            set(legacy) | {"scheduler", "recognition", "annotation"},
        )
        self.assertEqual(
            set(response["scheduler"]),
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
        self.assertEqual(
            set(response["annotation"]), set(annotation_fixture["annotation"])
        )
        for field in (
            "enabled",
            "available",
            "format",
            "envelope_version",
            "width",
            "height",
        ):
            self.assertEqual(
                response["annotation"][field], annotation_fixture["annotation"][field]
            )
        self.assertIsInstance(response["annotation"]["sequence"], int)
        self.assertGreaterEqual(response["annotation"]["sequence"], 0)
        self.assertIsInstance(response["annotation"]["byte_length"], int)
        self.assertGreater(response["annotation"]["byte_length"], 0)
        self.assertEqual(response["recognition"]["schema_version"], 1)
        self.assertEqual(set(response["recognition"]), set(fixture["recognition"]))

    async def test_shared_client_fixtures_are_accepted_with_correlated_acknowledgements(
        self,
    ) -> None:
        bridge = ContractBridge()
        with patch.object(
            GestureConsumer,
            "bridge_factory",
            staticmethod(lambda: bridge),
        ):
            communicator = WebsocketCommunicator(application, "/ws/")
            self.assertTrue((await communicator.connect())[0])
            ready = await communicator.receive_json_from()
            self.assertEqual(
                ready["capabilities"],
                ["annotated_frame.jpeg.v1", "gesture.recognition.v1"],
            )
            fixtures = load_fixtures("client-messages.json")
            self.assertEqual(
                {fixture["message"]["type"] for fixture in fixtures},
                {
                    WebSocketProtocolMessageType.PING.value,
                    WebSocketProtocolMessageType.RUNTIME_RESET.value,
                    WebSocketProtocolMessageType.ANNOTATED_FRAME_SET.value,
                },
            )
            for fixture in fixtures:
                message = fixture["message"]
                await communicator.send_json_to(message)
                response = await communicator.receive_json_from()
                self.assertEqual(response["protocol_version"], 1)
                self.assertEqual(response["request_id"], message["request_id"])
                if message["type"] == "ping":
                    self.assertEqual(response["type"], "pong")
                elif message["type"] == "runtime.reset":
                    self.assertEqual(response["type"], "runtime.reset.ack")
                else:
                    self.assertEqual(response["type"], "annotated_frame.set.ack")
                    self.assertEqual(response["enabled"], message["enabled"])
            self.assertEqual(bridge.reset_count, 1)
            self.assertFalse(bridge.annotation_enabled)
            await communicator.disconnect()
