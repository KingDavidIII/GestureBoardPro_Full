"""
GestureBoard Pro
Camera Service

Provides a robust, production-ready interface for interacting with the
system camera.

Responsibilities
----------------
- Camera initialization
- Resolution negotiation
- Frame acquisition
- Rolling FPS calculation
- Timestamp generation
- Resource management
"""

from __future__ import annotations

import logging
import platform
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Final

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_CAMERA_INDEX: Final[int] = 0
DEFAULT_WIDTH: Final[int] = 1280
DEFAULT_HEIGHT: Final[int] = 720

FPS_UPDATE_INTERVAL: Final[float] = 1.0


class CameraError(RuntimeError):
    """Raised whenever the camera cannot be initialized or read."""


@dataclass(slots=True, frozen=True)
class CameraConfig:
    """
    Immutable camera configuration.
    """

    camera_index: int = DEFAULT_CAMERA_INDEX
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    mirror: bool = True


@dataclass(slots=True, frozen=True)
class FramePacket:
    """
    Represents one captured frame together with metadata.
    """

    frame: np.ndarray
    timestamp: float
    monotonic_timestamp: float
    fps: float
    frame_number: int


class CameraService:
    """
    High-level interface for camera operations.

    Example
    -------
    >>> with CameraService() as camera:
    ...     packet = camera.read()
    ...     print(packet.fps)
    """

    def __init__(
        self,
        config: CameraConfig | None = None,
    ) -> None:

        self.config = config or CameraConfig()

        self._capture: cv2.VideoCapture | None = None

        self._frame_counter = 0
        self._fps = 0.0
        self._fps_counter = 0
        self._fps_timer = time.perf_counter()
        self._capture_start = 0.0

    @property
    def is_open(self) -> bool:
        """
        Returns True if the camera has been opened.
        """

        return self._capture is not None and self._capture.isOpened()

    @staticmethod
    def _backend_flag() -> int:
        """
        Select the preferred OpenCV backend.

        Windows:
            DirectShow

        Others:
            Default backend.
        """

        if platform.system() == "Windows":
            return cv2.CAP_DSHOW

        return cv2.CAP_ANY

    def start(self) -> None:
        """
        Opens the configured camera.
        """

        if self.is_open:
            return

        self._capture = cv2.VideoCapture(
            self.config.camera_index,
            self._backend_flag(),
        )

        if not self._capture.isOpened():
            raise CameraError(f"Unable to open camera {self.config.camera_index}.")

        self._capture.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            self.config.width,
        )

        self._capture.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            self.config.height,
        )

        self.reset_statistics()

        actual_width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))

        actual_height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if actual_width != self.config.width or actual_height != self.config.height:
            logger.warning(
                "Requested resolution %sx%s, using %sx%s.",
                self.config.width,
                self.config.height,
                actual_width,
                actual_height,
            )

    def _update_fps(self) -> None:
        """
        Updates the rolling FPS calculation once every second.
        """

        self._fps_counter += 1

        elapsed = time.perf_counter() - self._fps_timer

        if elapsed >= FPS_UPDATE_INTERVAL:
            self._fps = round(
                self._fps_counter / elapsed,
                2,
            )

            self._fps_counter = 0
            self._fps_timer = time.perf_counter()

    def read(self) -> FramePacket:
        """
        Capture one frame from the camera.

        Returns
        -------
        FramePacket
            Contains the captured frame together with timing
            and performance metadata.
        """

        if not self.is_open:
            raise CameraError("Camera has not been started.")

        success, frame = self._capture.read()

        if not success or frame is None:
            raise CameraError("Failed to capture frame.")

        if self.config.mirror:
            frame = cv2.flip(frame, 1)

        self._frame_counter += 1

        self._update_fps()

        return FramePacket(
            frame=frame,
            timestamp=time.time(),
            monotonic_timestamp=time.perf_counter(),
            fps=self._fps,
            frame_number=self._frame_counter,
        )

    @property
    def fps(self) -> float:
        """
        Returns the most recent rolling FPS.
        """

        return self._fps

    @property
    def elapsed_time(self) -> float:
        """
        Returns the elapsed running time in seconds.
        """

        return round(
            time.perf_counter() - self._capture_start,
            2,
        )

    @property
    def frame_count(self) -> int:
        """
        Returns the total number of frames captured.
        """

        return self._frame_counter

    @property
    def resolution(self) -> tuple[int, int]:
        """
        Returns the active camera resolution.
        """

        if not self.is_open:
            return (
                self.config.width,
                self.config.height,
            )

        return (
            int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )

    def reset_statistics(self) -> None:
        """
        Reset all runtime statistics without restarting the camera.

        Useful for benchmarking or beginning a new gesture session.
        """

        self._frame_counter = 0
        self._fps_counter = 0
        self._fps = 0.0

        now = time.perf_counter()

        self._fps_timer = now
        self._capture_start = now

    def release(self) -> None:
        """
        Release all camera resources.

        Safe to call multiple times.
        """

        if self._capture is not None:
            if self._capture.isOpened():
                self._capture.release()

            self._capture = None

        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass

    def restart(self) -> None:
        """
        Restart the camera using the current configuration.
        """

        self.release()
        self.start()

    def __enter__(self) -> CameraService:
        """
        Context manager entry.
        """

        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """
        Ensure camera resources are released.
        """

        self.release()

    def __repr__(self) -> str:
        """
        Developer-friendly representation.
        """

        width, height = self.resolution

        return (
            f"{self.__class__.__name__}("
            f"camera_index={self.config.camera_index}, "
            f"resolution={width}x{height}, "
            f"mirror={self.config.mirror}, "
            f"is_open={self.is_open}, "
            f"fps={self.fps}"
            f")"
        )
