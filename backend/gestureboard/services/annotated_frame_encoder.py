"""JPEG encoding and GBF1 envelopes for optional annotated-frame feedback."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

import cv2
import numpy as np

ANNOTATED_FRAME_MAGIC = b"GBF1"
ANNOTATED_FRAME_ENVELOPE_VERSION = 1
_HEADER = struct.Struct(">4sBBH I H H I")


class AnnotatedFrameMessageKind(IntEnum):
    ANNOTATED_JPEG = 1


class AnnotatedFrameEncoderError(ValueError):
    """Raised when an annotated frame cannot be safely encoded."""


class AnnotatedFrameEnvelopeError(ValueError):
    """Raised when GBF1 envelope values are out of range."""


@dataclass(frozen=True, slots=True)
class AnnotatedFrameEncoderConfig:
    jpeg_quality: int = 80
    maximum_width: int = 640
    maximum_height: int = 480
    maximum_payload_size: int = 512 * 1024

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("jpeg_quality", self.jpeg_quality, 100),
            ("maximum_width", self.maximum_width, 65535),
            ("maximum_height", self.maximum_height, 65535),
            ("maximum_payload_size", self.maximum_payload_size, 2**32 - 1),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= maximum
            ):
                raise AnnotatedFrameEncoderError(f"{name} is out of range.")


@dataclass(frozen=True, slots=True)
class AnnotatedFrameEncodingResult:
    jpeg_bytes: bytes
    width: int
    height: int
    payload_size: int
    mime_type: str = "image/jpeg"


@dataclass(frozen=True, slots=True)
class AnnotatedFrameBinaryEnvelope:
    sequence: int
    width: int
    height: int
    jpeg_bytes: bytes

    def to_bytes(self) -> bytes:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or not 0 <= self.sequence <= 2**32 - 1
        ):
            raise AnnotatedFrameEnvelopeError("sequence is out of range.")
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 65535
            for value in (self.width, self.height)
        ):
            raise AnnotatedFrameEnvelopeError("dimensions are out of range.")
        if not self.jpeg_bytes or len(self.jpeg_bytes) > 2**32 - 1:
            raise AnnotatedFrameEnvelopeError("JPEG payload is out of range.")
        return (
            _HEADER.pack(
                ANNOTATED_FRAME_MAGIC,
                ANNOTATED_FRAME_ENVELOPE_VERSION,
                AnnotatedFrameMessageKind.ANNOTATED_JPEG,
                0,
                self.sequence,
                self.width,
                self.height,
                len(self.jpeg_bytes),
            )
            + self.jpeg_bytes
        )


class AnnotatedFrameEncoder:
    def __init__(self, config: AnnotatedFrameEncoderConfig | None = None) -> None:
        self.config = config or AnnotatedFrameEncoderConfig()

    def encode(self, frame: np.ndarray) -> AnnotatedFrameEncodingResult:
        if (
            not isinstance(frame, np.ndarray)
            or frame.ndim != 3
            or frame.shape[2] != 3
            or not frame.size
        ):
            raise AnnotatedFrameEncoderError(
                "Annotated frame must be a non-empty three-channel BGR image."
            )
        height, width = frame.shape[:2]
        scale = min(
            1.0, self.config.maximum_width / width, self.config.maximum_height / height
        )
        resized = (
            frame
            if scale == 1
            else cv2.resize(
                frame,
                (round(width * scale), round(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        )
        try:
            success, encoded = cv2.imencode(
                ".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, self.config.jpeg_quality]
            )
        except Exception as error:
            raise AnnotatedFrameEncoderError(
                "Annotated JPEG encoding failed."
            ) from error
        if not success or encoded is None or not encoded.size:
            raise AnnotatedFrameEncoderError("Annotated JPEG encoding failed.")
        payload = encoded.tobytes()
        if len(payload) > self.config.maximum_payload_size:
            raise AnnotatedFrameEncoderError(
                "Annotated JPEG exceeds the configured payload limit."
            )
        return AnnotatedFrameEncodingResult(
            payload, resized.shape[1], resized.shape[0], len(payload)
        )
