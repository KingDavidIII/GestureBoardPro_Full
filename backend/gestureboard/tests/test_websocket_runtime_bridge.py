"""Tests for encoded-frame validation, decoding, and protocol serialization."""

import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from django.test import SimpleTestCase

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
    GestureRuntimeError,
    GestureRuntimeResult,
    HandSelectionDecision,
    SelectedHandIdentity,
)
from gestureboard.services.websocket_runtime_bridge import (
    OpenCVFrameDecoder,
    WebSocketProtocolErrorCode,
    WebSocketRuntimeBridge,
    WebSocketRuntimeBridgeConfig,
    WebSocketRuntimeBridgeError,
    WebSocketRuntimeBridgeStage,
)


def prediction() -> GesturePrediction:
    state = FingerState(False, True, -1.0, 0.5)
    return GesturePrediction(
        GestureLabel.POINT,
        GestureFeatures(state, state, state, state, state, 1.0, (0.5,) * 5),
    )


def runtime_result(*, with_hand: bool = True) -> GestureRuntimeResult:
    annotated = np.zeros((3, 4, 3), dtype=np.uint8)
    if with_hand:
        hand = HandGestureResult(0, "Right", 0.93, (), prediction())
        pipeline = GesturePipelineResult(annotated, (hand,))
        item = GestureObservation(hand.prediction, hand.confidence)
        identity = SelectedHandIdentity(0, "right")
        decision = HandSelectionDecision.FIRST_DETECTED
        engine_decision = GestureEngineDecision.ACCUMULATING
    else:
        hand = None
        pipeline = GesturePipelineResult(annotated, ())
        item = NeutralGestureObservation()
        identity = None
        decision = HandSelectionDecision.NO_HANDS
        engine_decision = GestureEngineDecision.NO_HAND
    engine = GestureEngineResult(
        item,
        engine_decision,
        None,
        0,
        None,
        0,
        12.5,
    )
    return GestureRuntimeResult(pipeline, hand, identity, decision, item, engine)


class FakeDecoder:
    def __init__(self, frame: np.ndarray | None = None) -> None:
        self.frame = frame if frame is not None else np.zeros((2, 3, 3), np.uint8)
        self.payloads: list[bytes] = []
        self.error: Exception | None = None
        self.close_count = 0

    def decode(self, payload: bytes) -> np.ndarray:
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.frame

    def close(self) -> None:
        self.close_count += 1


class BridgeConfigTests(SimpleTestCase):
    def test_default_configuration_is_valid_and_immutable(self) -> None:
        config = WebSocketRuntimeBridgeConfig()
        self.assertEqual(config.protocol_version, 1)
        with self.assertRaises(FrozenInstanceError):
            config.protocol_version = 2

    def test_invalid_configuration_is_rejected(self) -> None:
        invalid = (
            {"protocol_version": 0},
            {"protocol_version": 2},
            {"protocol_version": True},
            {"maximum_encoded_frame_size": 0},
            {"maximum_encoded_frame_size": True},
            {"maximum_decoded_width": 0},
            {"maximum_decoded_height": True},
            {"expose_diagnostic_errors": 1},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(WebSocketRuntimeBridgeError):
                    WebSocketRuntimeBridgeConfig(**values)


class BridgeProcessingTests(SimpleTestCase):
    def setUp(self) -> None:
        self.runtime = MagicMock()
        self.runtime.process.return_value = runtime_result()
        self.decoder = FakeDecoder()
        self.bridge = WebSocketRuntimeBridge(self.runtime, self.decoder)

    def test_annotation_is_opt_in_and_response_uses_the_same_sequence(self) -> None:
        encoder = MagicMock()
        encoder.encode.return_value = AnnotatedFrameEncodingResult(b"jpeg", 4, 3, 4)
        bridge = WebSocketRuntimeBridge(
            self.runtime, self.decoder, annotated_frame_encoder=encoder
        )
        disabled = bridge.process_frame_response(b"one")
        self.assertFalse(disabled.metadata["annotation"]["enabled"])
        encoder.encode.assert_not_called()
        bridge.set_annotation_enabled(True)
        enabled = bridge.process_frame_response(b"two")
        self.assertTrue(bridge.is_annotation_enabled)
        self.assertEqual(enabled.metadata["sequence"], enabled.sequence)
        self.assertEqual(enabled.metadata["annotation"]["sequence"], enabled.sequence)
        self.assertIsNotNone(enabled.annotated_envelope)
        encoder.encode.assert_called_once_with(
            self.runtime.process.return_value.annotated_frame
        )

    def test_annotation_encoding_failure_preserves_metadata(self) -> None:
        encoder = MagicMock()
        encoder.encode.side_effect = RuntimeError("private failure")
        bridge = WebSocketRuntimeBridge(
            self.runtime, self.decoder, annotated_frame_encoder=encoder
        )
        bridge.set_annotation_enabled(True)
        response = bridge.process_frame_response(b"frame")
        self.assertEqual(response.metadata["type"], "gesture.result")
        self.assertEqual(
            response.metadata["annotation"],
            {
                "enabled": True,
                "available": False,
                "error_code": "annotation_encoding_failed",
            },
        )
        self.assertIsNone(response.annotated_envelope)
        with self.assertRaises(WebSocketRuntimeBridgeError):
            bridge.set_annotation_enabled(1)

    def test_valid_bytes_decode_and_process_exactly_once(self) -> None:
        payload = b"encoded-image"
        response = self.bridge.process_frame(payload, timestamp=9.5)

        self.assertEqual(self.decoder.payloads, [payload])
        self.runtime.process.assert_called_once_with(self.decoder.frame, timestamp=9.5)
        self.assertEqual(response["sequence"], 0)
        self.assertEqual(response["type"], "gesture.result")

    def test_invalid_payloads_do_not_decode_or_run(self) -> None:
        values = (bytearray(b"x"), "x", b"")
        for payload in values:
            with self.subTest(payload=payload):
                with self.assertRaises(WebSocketRuntimeBridgeError):
                    self.bridge.process_frame(payload)
        self.assertEqual(self.decoder.payloads, [])
        self.runtime.process.assert_not_called()

    def test_oversized_payload_has_stable_code(self) -> None:
        bridge = WebSocketRuntimeBridge(
            self.runtime,
            self.decoder,
            WebSocketRuntimeBridgeConfig(maximum_encoded_frame_size=2),
        )
        with self.assertRaises(WebSocketRuntimeBridgeError) as caught:
            bridge.process_frame(b"abc")
        self.assertEqual(
            caught.exception.code, WebSocketProtocolErrorCode.FRAME_TOO_LARGE
        )
        self.runtime.process.assert_not_called()

    def test_decoder_failure_is_wrapped_and_runtime_not_called(self) -> None:
        original = ValueError("bad image")
        self.decoder.error = original
        with self.assertRaises(WebSocketRuntimeBridgeError) as caught:
            self.bridge.process_frame(b"bad")
        self.assertEqual(
            caught.exception.stage, WebSocketRuntimeBridgeStage.FRAME_DECODING
        )
        self.assertIs(caught.exception.__cause__, original)
        self.runtime.process.assert_not_called()

    def test_malformed_decoded_shape_and_limits_are_rejected(self) -> None:
        self.decoder.frame = np.zeros((2, 3), np.uint8)
        with self.assertRaises(WebSocketRuntimeBridgeError):
            self.bridge.process_frame(b"gray")
        limited = WebSocketRuntimeBridge(
            self.runtime,
            FakeDecoder(np.zeros((5, 6, 3), np.uint8)),
            WebSocketRuntimeBridgeConfig(
                maximum_decoded_width=5,
                maximum_decoded_height=4,
            ),
        )
        with self.assertRaises(WebSocketRuntimeBridgeError):
            limited.process_frame(b"large")
        self.runtime.process.assert_not_called()

    def test_runtime_failure_is_wrapped_with_cause(self) -> None:
        original = GestureRuntimeError(MagicMock(), "failed")
        self.runtime.process.side_effect = original
        with self.assertRaises(WebSocketRuntimeBridgeError) as caught:
            self.bridge.process_frame(b"valid")
        self.assertEqual(
            caught.exception.code, WebSocketProtocolErrorCode.RUNTIME_FAILURE
        )
        self.assertIs(caught.exception.__cause__, original)

    def test_selected_hand_serializes_only_safe_metadata(self) -> None:
        response = self.bridge.process_frame(b"valid")
        json.dumps(response)

        self.assertEqual(response["hand"]["handedness"], "Right")
        self.assertEqual(response["gesture"]["label"], GestureLabel.POINT.value)
        self.assertEqual(response["selection"]["identity"]["handedness"], "right")
        self.assertNotIn("annotated_frame", response)
        self.assertNotIn("landmarks", json.dumps(response))

    def test_no_hand_serializes_null_metadata(self) -> None:
        self.runtime.process.return_value = runtime_result(with_hand=False)
        response = self.bridge.process_frame(b"valid")
        json.dumps(response)

        self.assertIsNone(response["hand"])
        self.assertIsNone(response["selection"]["identity"])
        self.assertIsNone(response["gesture"]["label"])
        self.assertFalse(response["action_executed"])

    def test_sequence_advances_only_after_success_and_reset_clears_it(self) -> None:
        self.assertEqual(self.bridge.process_frame(b"a")["sequence"], 0)
        self.decoder.error = ValueError("bad")
        with self.assertRaises(WebSocketRuntimeBridgeError):
            self.bridge.process_frame(b"b")
        self.decoder.error = None
        self.assertEqual(self.bridge.process_frame(b"c")["sequence"], 1)
        self.bridge.reset()
        self.runtime.reset.assert_called_once_with()
        self.assertEqual(self.bridge.process_frame(b"d")["sequence"], 0)

    def test_explicit_sequence_validation(self) -> None:
        self.assertEqual(self.bridge.process_frame(b"a", sequence=7)["sequence"], 7)
        for value in (-1, True, 1.5, 6):
            with self.subTest(value=value):
                with self.assertRaises(WebSocketRuntimeBridgeError):
                    self.bridge.process_frame(b"b", sequence=value)
        self.assertEqual(self.runtime.process.call_count, 1)

    def test_recognition_uses_the_existing_pipeline_mediapipe_result(self) -> None:
        landmarks = [SimpleNamespace(x=0.0, y=0.0, z=0.0) for _ in range(21)]
        for (mcp, pip, tip), x in zip(
            ((1, 3, 4), (5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20)),
            (-2, -1, 0, 1, 2),
            strict=True,
        ):
            landmarks[mcp] = SimpleNamespace(x=x, y=0.0, z=0.0)
            landmarks[pip] = SimpleNamespace(x=x, y=1.0, z=0.0)
            landmarks[tip] = SimpleNamespace(x=x, y=3.0, z=0.0)
        media_result = SimpleNamespace(
            multi_hand_landmarks=[SimpleNamespace(landmark=landmarks)],
            multi_handedness=[
                SimpleNamespace(
                    classification=[SimpleNamespace(label="Right", score=0.9)]
                )
            ],
        )
        original = runtime_result()
        pipeline = GesturePipelineResult(
            original.annotated_frame, original.pipeline_result.hands, media_result
        )
        self.runtime.process.return_value = GestureRuntimeResult(
            pipeline,
            original.selected_hand,
            original.selected_identity,
            original.selection_decision,
            original.observation,
            original.engine_result,
        )
        response = self.bridge.process_frame(b"valid")
        self.runtime.process.assert_called_once_with(self.decoder.frame, timestamp=None)
        self.assertEqual(
            response["recognition"]["candidate"]["gesture_id"], "open_palm"
        )
        self.assertEqual(response["recognition"]["hand_count"], 1)

    def test_recognition_failure_is_nullable_and_does_not_poison_runtime_result(
        self,
    ) -> None:
        recognition = MagicMock()
        recognition.process.side_effect = ValueError("recognition fixture failure")
        self.bridge = WebSocketRuntimeBridge(
            self.runtime, self.decoder, recognition_service=recognition
        )
        original = runtime_result()
        media_result = SimpleNamespace(multi_hand_landmarks=[], multi_handedness=[])
        pipeline = GesturePipelineResult(
            original.annotated_frame, original.pipeline_result.hands, media_result
        )
        self.runtime.process.return_value = GestureRuntimeResult(
            pipeline,
            original.selected_hand,
            original.selected_identity,
            original.selection_decision,
            original.observation,
            original.engine_result,
        )
        with self.assertLogs(
            "gestureboard.services.websocket_runtime_bridge", level="ERROR"
        ):
            response = self.bridge.process_frame(b"valid")
        self.assertIsNone(response["recognition"])
        self.assertEqual(response["type"], "gesture.result")


class BridgeLifecycleTests(SimpleTestCase):
    def test_injected_dependencies_are_not_closed_and_use_is_rejected(self) -> None:
        runtime = MagicMock()
        decoder = FakeDecoder()
        bridge = WebSocketRuntimeBridge(runtime, decoder)
        bridge.close()
        bridge.close()
        runtime.close.assert_not_called()
        self.assertEqual(decoder.close_count, 0)
        with self.assertRaises(WebSocketRuntimeBridgeError):
            bridge.process_frame(b"x")
        with self.assertRaises(WebSocketRuntimeBridgeError):
            bridge.reset()

    def test_context_manager_closes_owned_dependencies_once(self) -> None:
        runtime = MagicMock()
        decoder = FakeDecoder()
        with (
            patch(
                "gestureboard.services.websocket_runtime_bridge.GestureRuntime",
                return_value=runtime,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.OpenCVFrameDecoder",
                return_value=decoder,
            ),
        ):
            with WebSocketRuntimeBridge() as bridge:
                pass
            bridge.close()
        runtime.close.assert_called_once_with()
        self.assertEqual(decoder.close_count, 1)


class OpenCVDecoderIntegrationTests(SimpleTestCase):
    def test_real_png_decoding_reaches_fake_runtime_as_bgr(self) -> None:
        source = np.zeros((3, 4, 3), np.uint8)
        source[:, :, 2] = 255
        encoded_ok, encoded = cv2.imencode(".png", source)
        self.assertTrue(encoded_ok)
        runtime = MagicMock()
        runtime.process.return_value = runtime_result(with_hand=False)
        bridge = WebSocketRuntimeBridge(runtime, OpenCVFrameDecoder())

        bridge.process_frame(encoded.tobytes())

        frame = runtime.process.call_args.args[0]
        self.assertEqual(frame.shape, (3, 4, 3))
        self.assertTrue(np.array_equal(frame, source))

    def test_real_decoder_rejects_undecodable_data(self) -> None:
        with self.assertRaises(WebSocketRuntimeBridgeError):
            OpenCVFrameDecoder().decode(b"not an image")
