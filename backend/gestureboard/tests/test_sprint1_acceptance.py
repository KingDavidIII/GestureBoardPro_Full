"""Sprint 1 integration acceptance across real internal components."""

import json
from unittest.mock import MagicMock, patch

import numpy as np
from django.test import SimpleTestCase

from gestureboard.services.action_dispatcher import ActionDispatcher
from gestureboard.services.gesture_classifier import GestureClassifier, GestureLabel
from gestureboard.services.gesture_engine import GestureEngine, GestureEngineConfig
from gestureboard.services.gesture_pipeline import GesturePipeline
from gestureboard.services.gesture_runtime import GestureRuntime
from gestureboard.services.keyboard_controller import KeyboardAction, KeyboardController
from gestureboard.services.landmark_normalizer import (
    LandmarkNormalizer,
    NormalizedLandmark,
)
from gestureboard.services.landmark_processor import HandData
from gestureboard.services.websocket_runtime_bridge import (
    WebSocketRuntimeBridge,
    WebSocketRuntimeBridgeError,
)


def synthetic_hand(*, extended: set[str]) -> list[NormalizedLandmark]:
    coordinates = [(0.0, 0.0, 0.0)] * 21
    fingers = {
        "thumb": ((-0.3, 0.2), (-0.5, 0.4), (-0.9, 0.4), (-1.3, 0.4)),
        "index": ((-0.4, 0.8), (-0.4, 1.1), (-0.4, 1.6), (-0.4, 2.1)),
        "middle": ((0.0, 1.0), (0.0, 1.3), (0.0, 1.8), (0.0, 2.3)),
        "ring": ((0.4, 0.9), (0.4, 1.2), (0.4, 1.7), (0.4, 2.2)),
        "little": ((0.7, 0.7), (0.7, 1.0), (0.7, 1.4), (0.7, 1.8)),
    }
    folded_tips = {
        "thumb": (-0.25, 0.2),
        "index": (-0.4, 0.75),
        "middle": (0.0, 0.8),
        "ring": (0.4, 0.75),
        "little": (0.7, 0.65),
    }
    starts = {"thumb": 1, "index": 5, "middle": 9, "ring": 13, "little": 17}
    for name, finger in fingers.items():
        chosen = list(finger)
        if name not in extended:
            chosen[-1] = folded_tips[name]
        for offset, (x, y) in enumerate(chosen):
            coordinates[starts[name] + offset] = (x, y, 0.0)
    return [NormalizedLandmark(*point) for point in coordinates]


class FakeKeyboardBackend:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def press(self, key: str) -> None:
        self.events.append(("press", key))

    def release(self, key: str) -> None:
        self.events.append(("release", key))

    def type(self, text: str) -> None:
        self.events.append(("type", text))


class FakeFrameDecoder:
    def __init__(self) -> None:
        self.close_count = 0

    def decode(self, payload: bytes) -> np.ndarray:
        marker = {b"none": 0, b"point": 1, b"open": 2}[payload]
        return np.full((2, 2, 3), marker, dtype=np.uint8)

    def close(self) -> None:
        self.close_count += 1


class FakeLandmarkProcessor:
    def __init__(self) -> None:
        self.point = synthetic_hand(extended={"index"})
        self.open_palm = synthetic_hand(
            extended={"thumb", "index", "middle", "ring", "little"}
        )

    def process(self, frame: np.ndarray):
        marker = int(frame[0, 0, 0])
        if marker == 0:
            return frame.copy(), []
        landmarks = self.point if marker == 1 else self.open_palm
        return frame.copy(), [HandData(landmarks, "Right", 0.98)]


class SprintOneAcceptanceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.keyboard = FakeKeyboardBackend()
        controller = KeyboardController(self.keyboard)
        dispatcher = ActionDispatcher(
            {GestureLabel.POINT: KeyboardAction.tap("a")},
            controller,
        )
        engine = GestureEngine(
            dispatcher,
            GestureEngineConfig(
                activation_frames=2,
                release_frames=2,
                cooldown_seconds=0,
            ),
        )
        pipeline = GesturePipeline(
            FakeLandmarkProcessor(),
            LandmarkNormalizer(),
            GestureClassifier(),
        )
        self.runtime = GestureRuntime(pipeline, engine)
        self.decoder = FakeFrameDecoder()
        self.bridge = WebSocketRuntimeBridge(self.runtime, self.decoder)
        self.timestamp = 0.0

    def frame(self, payload: bytes):
        result = self.bridge.process_frame(payload, timestamp=self.timestamp)
        self.timestamp += 1.0
        return result

    def test_activation_hold_release_rearm_and_transport_safety(self) -> None:
        accumulating = self.frame(b"point")
        activated = self.frame(b"point")
        held = self.frame(b"point")

        self.assertFalse(accumulating["action_executed"])
        self.assertTrue(activated["action_executed"])
        self.assertFalse(held["action_executed"])
        self.assertEqual(
            self.keyboard.events,
            [("press", "a"), ("release", "a")],
        )

        incomplete_release = self.frame(b"none")
        returned = self.frame(b"point")
        self.assertEqual(
            incomplete_release["gesture"]["engine_decision"],
            "RELEASE_ACCUMULATING",
        )
        self.assertFalse(returned["action_executed"])

        self.frame(b"none")
        released = self.frame(b"none")
        reaccumulating = self.frame(b"point")
        reactivated = self.frame(b"point")
        self.assertEqual(released["gesture"]["engine_decision"], "RELEASED")
        self.assertFalse(reaccumulating["action_executed"])
        self.assertTrue(reactivated["action_executed"])
        self.assertEqual(len(self.keyboard.events), 4)

        encoded = json.dumps(reactivated)
        for forbidden in ("annotated_frame", "landmarks", "FakeKeyboardBackend"):
            self.assertNotIn(forbidden, encoded)

    def test_unmapped_gesture_is_safe_and_serialisable(self) -> None:
        self.frame(b"open")
        result = self.frame(b"open")

        self.assertFalse(result["action_executed"])
        self.assertEqual(result["gesture"]["label"], GestureLabel.OPEN_PALM.value)
        self.assertEqual(result["gesture"]["engine_decision"], "UNMAPPED")
        self.assertEqual(self.keyboard.events, [])
        json.dumps(result)

    def test_injected_boundaries_remain_caller_owned_after_bridge_close(self) -> None:
        self.bridge.close()
        self.bridge.close()

        self.assertEqual(self.decoder.close_count, 0)
        with self.assertRaises(WebSocketRuntimeBridgeError):
            self.bridge.process_frame(b"point")

    def test_outer_bridge_closes_internally_owned_dependencies_once(self) -> None:
        owned_runtime = MagicMock()
        owned_decoder = MagicMock()
        with (
            patch(
                "gestureboard.services.websocket_runtime_bridge.GestureRuntime",
                return_value=owned_runtime,
            ),
            patch(
                "gestureboard.services.websocket_runtime_bridge.OpenCVFrameDecoder",
                return_value=owned_decoder,
            ),
        ):
            bridge = WebSocketRuntimeBridge()
        bridge.close()
        bridge.close()

        owned_runtime.close.assert_called_once_with()
        owned_decoder.close.assert_called_once_with()
