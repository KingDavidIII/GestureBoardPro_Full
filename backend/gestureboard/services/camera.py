"""
GestureBoard Pro
Camera Service

Provides a clean interface for accessing the system camera.
"""

from __future__ import annotations

import time

import cv2
import numpy as np


class CameraError(Exception):
    """Raised when the camera cannot be accessed."""


class CameraService:
    """
    Handles camera operations.

    Responsibilities:
        • Open camera
        • Read frames
        • Release resources
        • FPS calculation
        • Camera status
    """

    def __init__(
        self,
        camera_index: int = 0,
        width: int = 1280,
        height: int = 720,
    ) -> None:

        self.camera_index = camera_index
        self.width = width
        self.height = height

        self._capture: cv2.VideoCapture | None = None

        self._frame_counter = 0
        self._start_time = time.perf_counter()

    @property
    def is_open(self) -> bool:
        """Returns True if camera is active."""

        return self._capture is not None and self._capture.isOpened()

    def start(self) -> None:
        """Open the camera."""

        if self.is_open:
            return

        self._capture = cv2.VideoCapture(self.camera_index)

        if not self._capture.isOpened():
            raise CameraError(f"Unable to open camera {self.camera_index}.")

        self._capture.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            self.width,
        )

        self._capture.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            self.height,
        )

    def read(self) -> np.ndarray:
        """
        Read one frame.

        Returns:
            BGR OpenCV image.
        """

        if not self.is_open:
            raise CameraError("Camera has not been started.")

        success, frame = self._capture.read()

        if not success or frame is None:
            raise CameraError("Failed to capture frame.")

        self._frame_counter += 1

        return frame

    def fps(self) -> float:
        """
        Returns the average FPS.
        """

        elapsed = time.perf_counter() - self._start_time

        if elapsed <= 0:
            return 0.0

        return round(
            self._frame_counter / elapsed,
            2,
        )

    def release(self) -> None:
        """Release camera resources."""

        if self._capture is not None:
            self._capture.release()

        self._capture = None

    def __enter__(self) -> CameraService:
        self.start()
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ) -> None:
        self.release()
