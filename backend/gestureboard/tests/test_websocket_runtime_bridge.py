"""Tests for encoded-frame validation, decoding, and protocol serialization."""

import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from django.test import SimpleTestCase

from gestureboard.mouse.config import GestureMouseOutputMode, GestureMouseRuntimeConfig
from gestureboard.mouse.mapping import VirtualCursorMapper, VirtualSurface
from gestureboard.mouse.ownership import WindowsCursorOwnershipLease
from gestureboard.mouse.runtime import GestureMouseRuntimeCoordinator
from gestureboard.recognition.models import GestureCandidate, GestureId
from gestureboard.recognition.observations import (
    Handedness,
    HandObservation,
    Landmark3D,
)
from gestureboard.recognition.service import RecognitionFrameResult
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


class RecordingCursorOutput:
    def __init__(self, *, fails: bool = False) -> None:
        self.targets: list[object] = []
        self.closed = 0
        self.fails = fails

    def move(self, target: object) -> None:
        if self.fails:
            from gestureboard.mouse.models import MouseOutputError

            raise MouseOutputError("fixture output failed")
        self.targets.append(target)

    def close(self) -> None:
        self.closed += 1


class FakeWindowsCursorApi:
    def __init__(self) -> None:
        self.metric_calls: list[int] = []
        self.moves: list[tuple[int, int]] = []

    def get_system_metrics(self, metric_id: int) -> int:
        self.metric_calls.append(metric_id)
        return (10, 20, 100, 50)[len(self.metric_calls) - 1]

    def set_cursor_pos(self, x: int, y: int) -> bool:
        self.moves.append((x, y))
        return True


def recognition_hand(*, source_index: int = 2) -> HandObservation:
    landmarks = [Landmark3D(0.0, 0.0, 0.0) for _ in range(21)]
    landmarks[8] = Landmark3D(0.25, 0.75, 0.0)
    return HandObservation(
        tuple(landmarks), source_index, Handedness.RIGHT, 0.9, None, 1.0, 1.0
    )


def recognition_result(
    hand: HandObservation | None,
    stable: GestureCandidate | None = None,
) -> RecognitionFrameResult:
    return RecognitionFrameResult(
        1, 0, 0 if hand is None else 1, hand, None, stable, None, None, 0, 123.0
    )


def stable(gesture_id: GestureId) -> GestureCandidate:
    return GestureCandidate(gesture_id, 0.9, "fixture", threshold_satisfied=True)


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
        original = runtime_result()
        pipeline = GesturePipelineResult(
            original.annotated_frame, original.pipeline_result.hands, object()
        )
        self.runtime.process.return_value = GestureRuntimeResult(
            pipeline,
            original.selected_hand,
            original.selected_identity,
            original.selection_decision,
            original.observation,
            original.engine_result,
        )
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
    def test_bridge_rolls_back_runtime_when_decoder_construction_fails(self) -> None:
        runtime = MagicMock()
        with (
            patch(
                "gestureboard.services.websocket_runtime_bridge.GestureRuntime",
                return_value=runtime,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.OpenCVFrameDecoder",
                side_effect=RuntimeError("decoder"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "decoder"):
                WebSocketRuntimeBridge()
        runtime.close.assert_called_once_with()

    def test_bridge_rolls_back_runtime_and_decoder_when_encoder_construction_fails(
        self,
    ) -> None:
        runtime, decoder = MagicMock(), FakeDecoder()
        with (
            patch(
                "gestureboard.services.websocket_runtime_bridge.GestureRuntime",
                return_value=runtime,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.OpenCVFrameDecoder",
                return_value=decoder,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.AnnotatedFrameEncoder",
                side_effect=RuntimeError("encoder construction failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "encoder construction failure"),
        ):
            WebSocketRuntimeBridge()
        runtime.close.assert_called_once_with()
        self.assertEqual(decoder.close_count, 1)

    def test_bridge_rolls_back_owned_dependencies_when_recognition_construction_fails(
        self,
    ) -> None:
        runtime, decoder, encoder = MagicMock(), FakeDecoder(), MagicMock()
        with (
            patch(
                "gestureboard.services.websocket_runtime_bridge.GestureRuntime",
                return_value=runtime,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.OpenCVFrameDecoder",
                return_value=decoder,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.AnnotatedFrameEncoder",
                return_value=encoder,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.RecognitionService",
                side_effect=RuntimeError("recognition construction failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "recognition construction failure"),
        ):
            WebSocketRuntimeBridge()
        runtime.close.assert_called_once_with()
        encoder.close.assert_called_once_with()
        self.assertEqual(decoder.close_count, 1)

    def test_bridge_rolls_back_owned_dependencies_when_mouse_config_loading_fails(
        self,
    ) -> None:
        runtime, decoder, encoder, recognition = (
            MagicMock(),
            FakeDecoder(),
            MagicMock(),
            MagicMock(),
        )
        with (
            patch(
                "gestureboard.services.websocket_runtime_bridge.GestureRuntime",
                return_value=runtime,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.OpenCVFrameDecoder",
                return_value=decoder,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.AnnotatedFrameEncoder",
                return_value=encoder,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.RecognitionService",
                return_value=recognition,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.load_gesture_mouse_config",
                side_effect=RuntimeError("mouse config failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "mouse config failure"),
        ):
            WebSocketRuntimeBridge()
        runtime.close.assert_called_once_with()
        encoder.close.assert_called_once_with()
        recognition.reset.assert_called_once_with()
        self.assertEqual(decoder.close_count, 1)

    def test_bridge_rolls_back_owned_dependencies_when_mouse_composition_fails(
        self,
    ) -> None:
        runtime, decoder, encoder, recognition = (
            MagicMock(),
            FakeDecoder(),
            MagicMock(),
            MagicMock(),
        )
        with (
            patch(
                "gestureboard.services.websocket_runtime_bridge.GestureRuntime",
                return_value=runtime,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.OpenCVFrameDecoder",
                return_value=decoder,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.AnnotatedFrameEncoder",
                return_value=encoder,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.RecognitionService",
                return_value=recognition,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.build_mouse_runtime_dependencies",
                side_effect=RuntimeError("mouse composition failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "mouse composition failure"),
        ):
            WebSocketRuntimeBridge()
        runtime.close.assert_called_once_with()
        encoder.close.assert_called_once_with()
        recognition.reset.assert_called_once_with()
        self.assertEqual(decoder.close_count, 1)

    def test_bridge_rollback_cleanup_failure_preserves_original_error(self) -> None:
        runtime, decoder, encoder, recognition = (
            MagicMock(),
            FakeDecoder(),
            MagicMock(),
            MagicMock(),
        )
        encoder.close.side_effect = RuntimeError("cleanup")
        with (
            patch(
                "gestureboard.services.websocket_runtime_bridge.GestureRuntime",
                return_value=runtime,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.OpenCVFrameDecoder",
                return_value=decoder,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.AnnotatedFrameEncoder",
                return_value=encoder,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.RecognitionService",
                return_value=recognition,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.build_mouse_runtime_dependencies",
                side_effect=RuntimeError("mouse composition failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "mouse composition failure"),
        ):
            WebSocketRuntimeBridge()
        runtime.close.assert_called_once_with()
        recognition.reset.assert_called_once_with()
        self.assertEqual(decoder.close_count, 1)

    def test_falsy_bridge_dependencies_are_retained_and_external(self) -> None:
        class FalsyRuntime:
            def __init__(self):
                self.close_calls = 0

            def __bool__(self):
                return False

            def close(self):
                self.close_calls += 1

        class FalsyDecoder:
            def __init__(self):
                self.close_calls = 0

            def __bool__(self):
                return False

            def close(self):
                self.close_calls += 1

        class FalsyEncoder:
            def __init__(self):
                self.close_calls = 0

            def __bool__(self):
                return False

            def close(self):
                self.close_calls += 1

        class FalsyRecognitionService:
            def __init__(self):
                self.reset_calls = 0

            def __bool__(self):
                return False

            def reset(self):
                self.reset_calls += 1

        class FalsyMouseCoordinator:
            def __init__(self):
                self.close_calls = 0

            def __bool__(self):
                return False

            def close(self):
                self.close_calls += 1

            def tracking_lost(self):
                return None

        runtime, decoder, encoder, recognition, coordinator = (
            FalsyRuntime(),
            FalsyDecoder(),
            FalsyEncoder(),
            FalsyRecognitionService(),
            FalsyMouseCoordinator(),
        )
        config = GestureMouseRuntimeConfig()
        with (
            patch(
                "gestureboard.services.websocket_runtime_bridge.GestureRuntime"
            ) as runtime_default,
            patch(
                "gestureboard.services.websocket_runtime_bridge.OpenCVFrameDecoder"
            ) as decoder_default,
            patch(
                "gestureboard.services.websocket_runtime_bridge.AnnotatedFrameEncoder"
            ) as encoder_default,
            patch(
                "gestureboard.services.websocket_runtime_bridge.RecognitionService"
            ) as recognition_default,
            patch(
                "gestureboard.services.websocket_runtime_bridge.load_gesture_mouse_config"
            ) as config_default,
            patch(
                "gestureboard.services.websocket_runtime_bridge.build_mouse_runtime_dependencies"
            ) as composition_default,
        ):
            bridge = WebSocketRuntimeBridge(
                runtime,
                decoder,
                annotated_frame_encoder=encoder,
                recognition_service=recognition,
                mouse_config=config,
                mouse_coordinator=coordinator,
            )
        self.assertIs(bridge.runtime, runtime)
        self.assertIs(bridge.decoder, decoder)
        self.assertIs(bridge.annotated_frame_encoder, encoder)
        self.assertIs(bridge.recognition_service, recognition)
        self.assertIs(bridge.mouse_coordinator, coordinator)
        bridge.close()
        bridge.close()
        self.assertEqual(
            (
                runtime.close_calls,
                decoder.close_calls,
                encoder.close_calls,
                recognition.reset_calls,
                coordinator.close_calls,
            ),
            (0, 0, 0, 0, 0),
        )
        for default in (
            runtime_default,
            decoder_default,
            encoder_default,
            recognition_default,
            config_default,
            composition_default,
        ):
            default.assert_not_called()

    def test_protocol_response_recursively_excludes_mouse_internal_keys(self) -> None:
        forbidden = {
            "button_decision",
            "button_action",
            "mouse_button",
            "drag_action",
            "primary_down",
            "primary_up",
            "secondary_click",
            "ownership",
            "owner_id",
            "lease",
            "lease_owner",
            "windows_lease",
        }

        def walk(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden & set(value))
                for nested in value.values():
                    walk(nested)
            elif isinstance(value, list):
                for nested in value:
                    walk(nested)

        runtime = MagicMock()
        runtime.process.return_value = runtime_result()
        bridge = WebSocketRuntimeBridge(runtime, FakeDecoder())
        walk(bridge.process_frame(b"frame"))

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
                self.assertFalse(bridge._closed)
            bridge.close()
        runtime.close.assert_called_once_with()
        self.assertEqual(decoder.close_count, 1)

    def test_injected_mouse_coordinator_is_not_closed(self) -> None:
        runtime = MagicMock()
        decoder = FakeDecoder()
        coordinator = MagicMock()
        bridge = WebSocketRuntimeBridge(runtime, decoder, mouse_coordinator=coordinator)

        bridge.close()
        bridge.close()

        coordinator.close.assert_not_called()

    def test_owned_mouse_coordinator_is_closed_once(self) -> None:
        runtime = MagicMock()
        decoder = FakeDecoder()
        coordinator = MagicMock()
        with patch(
            "gestureboard.services.websocket_runtime_bridge.build_mouse_runtime_dependencies"
        ) as factory:
            factory.return_value = SimpleNamespace(coordinator=coordinator)
            bridge = WebSocketRuntimeBridge(runtime, decoder)
        bridge.close()
        bridge.close()
        coordinator.close.assert_called_once_with()


class GestureMouseBridgeIntegrationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.runtime = MagicMock()
        original = runtime_result()
        pipeline = GesturePipelineResult(
            original.annotated_frame, original.pipeline_result.hands, object()
        )
        self.runtime.process.return_value = GestureRuntimeResult(
            pipeline,
            original.selected_hand,
            original.selected_identity,
            original.selection_decision,
            original.observation,
            original.engine_result,
        )
        self.decoder = FakeDecoder()
        self.recognition = MagicMock()
        self.hand = recognition_hand()
        self.recognition.process.return_value = recognition_result(
            self.hand, stable(GestureId.POINT)
        )

    def _bridge(
        self,
        coordinator: GestureMouseRuntimeCoordinator,
        config: GestureMouseRuntimeConfig | None = None,
    ) -> WebSocketRuntimeBridge:
        return WebSocketRuntimeBridge(
            self.runtime,
            self.decoder,
            recognition_service=self.recognition,
            mouse_config=config or GestureMouseRuntimeConfig(enabled=True),
            mouse_coordinator=coordinator,
        )

    def test_default_disabled_does_not_construct_windows_output_or_move(self) -> None:
        output = RecordingCursorOutput()
        coordinator = GestureMouseRuntimeCoordinator("disabled", output=output)
        with patch(
            "gestureboard.mouse.composition.create_windows_cursor_api"
        ) as api_factory:
            response = self._bridge(
                coordinator, GestureMouseRuntimeConfig()
            ).process_frame(b"frame")
        self.assertEqual(response["type"], "gesture.result")
        self.assertEqual(output.targets, [])
        api_factory.assert_not_called()

    def test_virtual_mode_reuses_cached_primary_hand_once(self) -> None:
        output = RecordingCursorOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "virtual",
            enabled=True,
            mapper=VirtualCursorMapper(VirtualSurface(100, 100)),
            output=output,
        )
        bridge = self._bridge(coordinator)

        response = bridge.process_frame(b"frame")

        self.assertEqual(response["protocol_version"], 1)
        self.assertEqual(len(output.targets), 1)
        self.assertEqual(output.targets[0].source_index, self.hand.source_index)
        self.assertEqual(output.targets[0].timestamp_ms, 123)
        self.recognition.process.assert_called_once_with(
            self.runtime.process.return_value.pipeline_result.mediapipe_result,
            frame_sequence=0,
        )
        self.runtime.process.assert_called_once_with(self.decoder.frame, timestamp=None)

    def test_no_hand_and_processing_failure_reset_mouse_without_movement(self) -> None:
        output = RecordingCursorOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "virtual", enabled=True, output=output
        )
        bridge = self._bridge(coordinator)
        self.recognition.process.return_value = recognition_result(None)
        bridge.process_frame(b"no-hand")
        self.assertEqual(output.targets, [])
        self.runtime.process.side_effect = ValueError("runtime failure")
        with self.assertRaises(WebSocketRuntimeBridgeError):
            bridge.process_frame(b"bad")
        self.assertEqual(output.targets, [])

    def test_only_stable_point_allows_motion_and_reacquires_fresh_mapping(self) -> None:
        output = RecordingCursorOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "virtual",
            enabled=True,
            mapper=VirtualCursorMapper(VirtualSurface(100, 100)),
            output=output,
            max_output_hz=10,
        )
        bridge = self._bridge(coordinator)
        bridge.process_frame(b"point")
        self.assertEqual(len(output.targets), 1)
        for gesture_id in (
            GestureId.OPEN_PALM,
            GestureId.CLOSED_FIST,
            GestureId.PINCH,
            GestureId.UNKNOWN,
        ):
            with self.subTest(gesture_id=gesture_id):
                self.recognition.process.return_value = recognition_result(
                    self.hand, stable(gesture_id)
                )
                bridge.process_frame(b"non-point")
                self.assertEqual(len(output.targets), 1)
        self.recognition.process.return_value = recognition_result(self.hand)
        bridge.process_frame(b"pending")
        self.assertEqual(len(output.targets), 1)
        self.recognition.process.return_value = recognition_result(
            self.hand, stable(GestureId.POINT)
        )
        bridge.process_frame(b"point-again")
        self.assertEqual(len(output.targets), 2)
        self.assertEqual(output.targets[-1].timestamp_ms, 123)

    def test_output_failure_isolated_from_gesture_result_and_releases_lease(
        self,
    ) -> None:
        lease = WindowsCursorOwnershipLease()
        coordinator = GestureMouseRuntimeCoordinator(
            "windows",
            enabled=True,
            output=RecordingCursorOutput(fails=True),
            windows_lease=lease,
        )
        response = self._bridge(coordinator).process_frame(b"frame")
        self.assertEqual(response["type"], "gesture.result")
        self.assertFalse(coordinator.enabled)
        self.assertIsNone(lease.owner_id)

    def test_windows_mode_uses_fake_api_and_requires_the_shared_lease(self) -> None:
        api = FakeWindowsCursorApi()
        lease = WindowsCursorOwnershipLease()
        bridge = WebSocketRuntimeBridge(
            self.runtime,
            self.decoder,
            recognition_service=self.recognition,
            mouse_config=GestureMouseRuntimeConfig(
                enabled=True, output_mode=GestureMouseOutputMode.WINDOWS
            ),
            mouse_windows_api=api,
            mouse_ownership_lease=lease,
        )
        response = bridge.process_frame(b"frame")
        self.assertEqual(response["type"], "gesture.result")
        self.assertEqual(api.moves, [(109, 69)])
        self.assertIsNotNone(lease.owner_id)
        bridge.close()
        self.assertIsNone(lease.owner_id)

    def test_close_releases_output_and_bridge_does_not_add_protocol_data(self) -> None:
        output = RecordingCursorOutput()
        coordinator = GestureMouseRuntimeCoordinator(
            "virtual", enabled=True, output=output
        )
        bridge = self._bridge(coordinator)
        response = bridge.process_frame(b"frame")
        bridge.close()
        bridge.close()
        self.assertEqual(output.closed, 0)
        self.assertNotIn("mouse", response)

    def test_bridge_forwards_cached_hand_stable_id_and_timestamp_once(self) -> None:
        coordinator = MagicMock()
        bridge = self._bridge(coordinator)

        response = bridge.process_frame(b"forwarded")

        self.assertEqual(response["type"], "gesture.result")
        coordinator.process.assert_called_once_with(
            self.hand, timestamp_ms=123, stable_gesture=GestureId.POINT
        )
        self.assertEqual(self.decoder.payloads, [b"forwarded"])
        self.runtime.process.assert_called_once_with(self.decoder.frame, timestamp=None)
        self.recognition.process.assert_called_once_with(
            self.runtime.process.return_value.pipeline_result.mediapipe_result,
            frame_sequence=0,
        )

    def test_response_does_not_serialize_button_actions(self) -> None:
        coordinator = MagicMock()
        response = self._bridge(coordinator).process_frame(b"no-button-transport")

        serialized = json.dumps(response)
        for field in (
            "button_decision",
            "mouse_button",
            "button_action",
            "drag_action",
        ):
            with self.subTest(field=field):
                self.assertNotIn(field, serialized)

    def test_close_aggregates_owned_cleanup_and_preserves_first_cause(self) -> None:
        runtime = MagicMock()
        runtime.close.side_effect = RuntimeError("runtime close failure")
        decoder = FakeDecoder()
        decoder.close = MagicMock(side_effect=RuntimeError("decoder close failure"))
        recognition = MagicMock()
        recognition.reset.side_effect = RuntimeError("recognition reset failure")
        coordinator = MagicMock()
        coordinator.close.side_effect = RuntimeError("coordinator close failure")
        encoder = MagicMock()
        encoder.close.side_effect = RuntimeError("encoder close failure")
        bridge = WebSocketRuntimeBridge(
            runtime,
            decoder,
            recognition_service=recognition,
            mouse_coordinator=coordinator,
            annotated_frame_encoder=encoder,
        )
        bridge._owns_runtime = True
        bridge._owns_decoder = True
        bridge._owns_annotated_frame_encoder = True

        with self.assertRaises(WebSocketRuntimeBridgeError) as raised:
            bridge.close()
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertEqual(str(raised.exception.__cause__), "runtime close failure")
        coordinator.close.assert_not_called()
        runtime.close.assert_called_once_with()
        decoder.close.assert_called_once_with()
        encoder.close.assert_called_once_with()
        bridge.close()
        coordinator.close.assert_not_called()


class OpenCVDecoderIntegrationTests(SimpleTestCase):
    def test_bridge_response_schema_has_no_button_action_fields(self) -> None:
        response = {
            "protocol_version": 1,
            "type": "gesture.result",
            "gesture": {"label": "unknown"},
        }
        self.assertFalse(
            {"button_action", "button_decision", "mouse_button"} & response.keys()
        )

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
