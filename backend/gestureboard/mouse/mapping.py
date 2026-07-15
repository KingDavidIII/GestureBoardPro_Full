"""Pure virtual-surface mapping and locked deterministic cursor smoothing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import hypot, isfinite
from threading import RLock

from .models import CursorTarget, MouseValidationError


def _finite_unit(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise MouseValidationError(
            f"{name} must be a finite real number in [0.0, 1.0]."
        )
    return float(value)


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MouseValidationError(f"{name} must be a positive integer.")
    return value


@dataclass(frozen=True, slots=True)
class VirtualSurface:
    """A virtual pixel surface; this never queries a physical monitor."""

    width_px: int
    height_px: int

    def __post_init__(self) -> None:
        _positive_int(self.width_px, "width_px")
        _positive_int(self.height_px, "height_px")


@dataclass(frozen=True, slots=True)
class ActiveCameraRegion:
    left: float = 0.0
    top: float = 0.0
    right: float = 1.0
    bottom: float = 1.0

    def __post_init__(self) -> None:
        for name in ("left", "top", "right", "bottom"):
            object.__setattr__(self, name, _finite_unit(getattr(self, name), name))
        if self.left >= self.right:
            raise MouseValidationError("left must be less than right.")
        if self.top >= self.bottom:
            raise MouseValidationError("top must be less than bottom.")


@dataclass(frozen=True, slots=True)
class VirtualCursorMappingPolicy:
    active_region: ActiveCameraRegion = field(default_factory=ActiveCameraRegion)
    mirror_x: bool = False
    mirror_y: bool = False
    smoothing_alpha: float = 1.0
    dead_zone_radius: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.active_region, ActiveCameraRegion):
            raise MouseValidationError("active_region must be an ActiveCameraRegion.")
        if not isinstance(self.mirror_x, bool) or not isinstance(self.mirror_y, bool):
            raise MouseValidationError("mirror values must be bool.")
        alpha = self.smoothing_alpha
        if (
            isinstance(alpha, bool)
            or not isinstance(alpha, (int, float))
            or not isfinite(alpha)
            or not 0.0 < alpha <= 1.0
        ):
            raise MouseValidationError(
                "smoothing_alpha must be finite and in (0.0, 1.0]."
            )
        radius = self.dead_zone_radius
        if (
            isinstance(radius, bool)
            or not isinstance(radius, (int, float))
            or not isfinite(radius)
            or not 0.0 <= radius <= 1.0
        ):
            raise MouseValidationError(
                "dead_zone_radius must be finite and in [0.0, 1.0]."
            )
        object.__setattr__(self, "smoothing_alpha", float(alpha))
        object.__setattr__(self, "dead_zone_radius", float(radius))


@dataclass(frozen=True, slots=True)
class VirtualCursorTarget:
    x_normalised: float
    y_normalised: float
    x_px: int
    y_px: int
    timestamp_ms: int
    source_index: int

    def __post_init__(self) -> None:
        for name in ("x_normalised", "y_normalised"):
            object.__setattr__(self, name, _finite_unit(getattr(self, name), name))
        if (
            isinstance(self.x_px, bool)
            or not isinstance(self.x_px, int)
            or self.x_px < 0
        ):
            raise MouseValidationError("x_px must be a non-negative integer.")
        if (
            isinstance(self.y_px, bool)
            or not isinstance(self.y_px, int)
            or self.y_px < 0
        ):
            raise MouseValidationError("y_px must be a non-negative integer.")
        if (
            isinstance(self.timestamp_ms, bool)
            or not isinstance(self.timestamp_ms, int)
            or self.timestamp_ms < 0
        ):
            raise MouseValidationError("timestamp_ms must be a non-negative integer.")
        if (
            isinstance(self.source_index, bool)
            or not isinstance(self.source_index, int)
            or self.source_index < 0
        ):
            raise MouseValidationError("source_index must be a non-negative integer.")


class VirtualCursorReason(StrEnum):
    EMITTED = "emitted"
    DEAD_ZONE_SUPPRESSED = "dead_zone_suppressed"
    INVALID_SELECTED_HAND = "invalid_selected_hand"
    STALE_TIMESTAMP = "stale_timestamp"
    RESET = "reset"


@dataclass(frozen=True, slots=True)
class VirtualCursorMappingResult:
    accepted: bool
    emitted: bool
    virtual_target: VirtualCursorTarget | None
    retained_target: VirtualCursorTarget | None
    reason: VirtualCursorReason
    source_reset: bool = False


@dataclass(frozen=True, slots=True)
class VirtualCursorSnapshot:
    current_target: VirtualCursorTarget | None
    smoothed_normalised: tuple[float, float] | None
    source_index: int | None
    last_timestamp_ms: int | None
    surface: VirtualSurface
    policy: VirtualCursorMappingPolicy
    reset_generation: int


class VirtualCursorMapper:
    """Map one already-valid camera target at a time, with no output side effects."""

    def __init__(
        self,
        surface: VirtualSurface,
        policy: VirtualCursorMappingPolicy | None = None,
    ) -> None:
        if not isinstance(surface, VirtualSurface):
            raise MouseValidationError("surface must be a VirtualSurface.")
        self._surface = surface
        self._policy = policy or VirtualCursorMappingPolicy()
        self._current_target: VirtualCursorTarget | None = None
        self._smoothed: tuple[float, float] | None = None
        self._source_index: int | None = None
        self._last_timestamp_ms: int | None = None
        self._reset_generation = 0
        self._lock = RLock()

    def map(self, target: CursorTarget | None) -> VirtualCursorMappingResult:
        with self._lock:
            if not isinstance(target, CursorTarget):
                return VirtualCursorMappingResult(
                    False,
                    False,
                    None,
                    self._current_target,
                    VirtualCursorReason.INVALID_SELECTED_HAND,
                )
            source_reset = (
                target.source_index != self._source_index
                and self._source_index is not None
            )
            if source_reset:
                self._clear_history()
            if (
                self._last_timestamp_ms is not None
                and target.timestamp_ms < self._last_timestamp_ms
            ):
                return VirtualCursorMappingResult(
                    False,
                    False,
                    None,
                    self._current_target,
                    VirtualCursorReason.STALE_TIMESTAMP,
                    source_reset,
                )
            mapped = _map_camera_target(target, self._surface, self._policy)
            x, y = mapped
            if self._smoothed is not None:
                alpha = self._policy.smoothing_alpha
                x = alpha * x + (1.0 - alpha) * self._smoothed[0]
                y = alpha * y + (1.0 - alpha) * self._smoothed[1]
            self._smoothed = (x, y)
            self._source_index = target.source_index
            self._last_timestamp_ms = target.timestamp_ms
            candidate = _virtual_target(x, y, target, self._surface)
            if (
                self._current_target is not None
                and hypot(
                    x - self._current_target.x_normalised,
                    y - self._current_target.y_normalised,
                )
                <= self._policy.dead_zone_radius
            ):
                return VirtualCursorMappingResult(
                    True,
                    False,
                    None,
                    self._current_target,
                    VirtualCursorReason.DEAD_ZONE_SUPPRESSED,
                    source_reset,
                )
            self._current_target = candidate
            return VirtualCursorMappingResult(
                True,
                True,
                candidate,
                candidate,
                VirtualCursorReason.EMITTED,
                source_reset,
            )

    def reset(self) -> VirtualCursorMappingResult:
        with self._lock:
            self._clear_history()
            self._reset_generation += 1
            return VirtualCursorMappingResult(
                False, False, None, None, VirtualCursorReason.RESET
            )

    def snapshot(self) -> VirtualCursorSnapshot:
        with self._lock:
            return VirtualCursorSnapshot(
                self._current_target,
                self._smoothed,
                self._source_index,
                self._last_timestamp_ms,
                self._surface,
                self._policy,
                self._reset_generation,
            )

    def _clear_history(self) -> None:
        self._current_target = None
        self._smoothed = None
        self._source_index = None
        self._last_timestamp_ms = None


def _map_camera_target(
    target: CursorTarget,
    surface: VirtualSurface,
    policy: VirtualCursorMappingPolicy,
) -> tuple[float, float]:
    del surface
    region = policy.active_region
    x = min(1.0, max(0.0, (target.x - region.left) / (region.right - region.left)))
    y = min(1.0, max(0.0, (target.y - region.top) / (region.bottom - region.top)))
    return (1.0 - x if policy.mirror_x else x, 1.0 - y if policy.mirror_y else y)


def _virtual_target(
    x: float, y: float, target: CursorTarget, surface: VirtualSurface
) -> VirtualCursorTarget:
    x_px = min(surface.width_px - 1, max(0, round(x * (surface.width_px - 1))))
    y_px = min(surface.height_px - 1, max(0, round(y * (surface.height_px - 1))))
    return VirtualCursorTarget(
        x, y, x_px, y_px, target.timestamp_ms, target.source_index
    )
