from __future__ import annotations

import struct
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from gestureboard.services.annotated_frame_encoder import (
    ANNOTATED_FRAME_MAGIC,
    AnnotatedFrameBinaryEnvelope,
    AnnotatedFrameEncoder,
    AnnotatedFrameEncoderConfig,
    AnnotatedFrameEncoderError,
    AnnotatedFrameEnvelopeError,
)


class AnnotatedFrameEncoderTests(SimpleTestCase):
    def test_configuration_rejects_bool_and_out_of_range_values(self) -> None:
        for kwargs in (
            {"jpeg_quality": True},
            {"jpeg_quality": 101},
            {"maximum_width": 0},
            {"maximum_height": -1},
            {"maximum_payload_size": 0},
        ):
            with self.assertRaises(AnnotatedFrameEncoderError):
                AnnotatedFrameEncoderConfig(**kwargs)

    def test_encodes_bgr_frame_without_mutating_and_result_is_immutable(self) -> None:
        frame = np.full((20, 40, 3), 127, dtype=np.uint8)
        original = frame.copy()
        result = AnnotatedFrameEncoder().encode(frame)

        self.assertTrue(result.jpeg_bytes.startswith(b"\xff\xd8"))
        self.assertEqual((result.width, result.height), (40, 20))
        self.assertEqual(frame.tolist(), original.tolist())
        with self.assertRaises(FrozenInstanceError):
            result.width = 1  # type: ignore[misc]

    def test_resizes_aspect_ratio_and_forwards_quality(self) -> None:
        frame = np.zeros((100, 400, 3), dtype=np.uint8)
        with patch(
            "gestureboard.services.annotated_frame_encoder.cv2.imencode",
            return_value=(True, np.array([1], dtype=np.uint8)),
        ) as encode:
            result = AnnotatedFrameEncoder(
                AnnotatedFrameEncoderConfig(
                    jpeg_quality=63, maximum_width=200, maximum_height=200
                )
            ).encode(frame)
        self.assertEqual((result.width, result.height), (200, 50))
        self.assertEqual(encode.call_args.args[0], ".jpg")
        self.assertEqual(encode.call_args.args[2][1], 63)

    def test_rejects_invalid_frames_encoding_failure_and_payload_limit(self) -> None:
        encoder = AnnotatedFrameEncoder()
        for frame in (
            np.empty((0, 1, 3), dtype=np.uint8),
            np.zeros((2, 2), dtype=np.uint8),
            np.zeros((2, 2, 4), dtype=np.uint8),
        ):
            with self.assertRaises(AnnotatedFrameEncoderError):
                encoder.encode(frame)
        with patch(
            "gestureboard.services.annotated_frame_encoder.cv2.imencode",
            side_effect=RuntimeError("failure"),
        ):
            with self.assertRaises(AnnotatedFrameEncoderError) as error:
                encoder.encode(np.zeros((2, 2, 3), dtype=np.uint8))
        self.assertIsInstance(error.exception.__cause__, RuntimeError)
        with self.assertRaises(AnnotatedFrameEncoderError):
            AnnotatedFrameEncoder(
                AnnotatedFrameEncoderConfig(maximum_payload_size=1)
            ).encode(np.zeros((10, 10, 3), dtype=np.uint8))
        with patch(
            "gestureboard.services.annotated_frame_encoder.cv2.imencode",
            return_value=(False, np.array([], dtype=np.uint8)),
        ):
            with self.assertRaises(AnnotatedFrameEncoderError):
                encoder.encode(np.zeros((2, 2, 3), dtype=np.uint8))

    def test_gbf1_envelope_has_network_order_fields_and_validates_ranges(self) -> None:
        data = AnnotatedFrameBinaryEnvelope(7, 320, 240, b"jpeg").to_bytes()
        self.assertEqual(data[:4], ANNOTATED_FRAME_MAGIC)
        self.assertEqual(
            struct.unpack(">BBH I H H I", data[4:20]), (1, 1, 0, 7, 320, 240, 4)
        )
        self.assertEqual(len(data), 24)
        self.assertEqual(
            AnnotatedFrameBinaryEnvelope(2**32 - 1, 1, 1, b"x").to_bytes()[8:12],
            b"\xff\xff\xff\xff",
        )
        for envelope in (
            AnnotatedFrameBinaryEnvelope(-1, 1, 1, b"x"),
            AnnotatedFrameBinaryEnvelope(2**32, 1, 1, b"x"),
            AnnotatedFrameBinaryEnvelope(1, 0, 1, b"x"),
            AnnotatedFrameBinaryEnvelope(1, 65536, 1, b"x"),
            AnnotatedFrameBinaryEnvelope(1, 1, 1, b""),
        ):
            with self.assertRaises(AnnotatedFrameEnvelopeError):
                envelope.to_bytes()
