"""Immutable, environment-backed configuration for the optional mouse layer."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

from .buttons import MouseButtonPolicy
from .mapping import ActiveCameraRegion, VirtualCursorMappingPolicy, VirtualSurface
from .models import MouseValidationError


class GestureMouseConfigurationError(MouseValidationError):
    """Raised when gesture-mouse environment configuration is invalid."""


class GestureMouseOutputMode(StrEnum):
    VIRTUAL = "virtual"
    WINDOWS = "windows"


class MouseButtonOutputMode(StrEnum):
    NULL = "null"
    WINDOWS = "windows"


@dataclass(frozen=True, slots=True)
class GestureMouseRuntimeConfig:
    enabled: bool = False
    output_mode: GestureMouseOutputMode = GestureMouseOutputMode.VIRTUAL
    virtual_width_px: int = 1920
    virtual_height_px: int = 1080
    active_region: ActiveCameraRegion = field(default_factory=ActiveCameraRegion)
    mirror_x: bool = False
    mirror_y: bool = False
    smoothing_alpha: float = 1.0
    dead_zone_radius: float = 0.0
    max_output_hz: float = 60.0
    button_policy: MouseButtonPolicy = field(default_factory=MouseButtonPolicy)
    button_output_mode: MouseButtonOutputMode = MouseButtonOutputMode.NULL

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise GestureMouseConfigurationError("enabled must be bool.")
        if not isinstance(self.output_mode, GestureMouseOutputMode):
            raise GestureMouseConfigurationError("output_mode must be supported.")
        if not isinstance(self.button_policy, MouseButtonPolicy):
            raise GestureMouseConfigurationError(
                "button_policy must be a MouseButtonPolicy."
            )
        if not isinstance(self.button_output_mode, MouseButtonOutputMode):
            raise GestureMouseConfigurationError(
                "button_output_mode must be supported."
            )
        try:
            VirtualSurface(self.virtual_width_px, self.virtual_height_px)
            VirtualCursorMappingPolicy(
                self.active_region,
                self.mirror_x,
                self.mirror_y,
                self.smoothing_alpha,
                self.dead_zone_radius,
            )
        except MouseValidationError as exc:
            raise GestureMouseConfigurationError(str(exc)) from exc
        if (
            isinstance(self.max_output_hz, bool)
            or not isinstance(self.max_output_hz, (int, float))
            or not isfinite(self.max_output_hz)
            or not 0.0 < self.max_output_hz <= 240.0
        ):
            raise GestureMouseConfigurationError(
                "max_output_hz must be finite and in (0.0, 240.0]."
            )
        object.__setattr__(self, "max_output_hz", float(self.max_output_hz))

    def mapping_policy(self) -> VirtualCursorMappingPolicy:
        return VirtualCursorMappingPolicy(
            self.active_region,
            self.mirror_x,
            self.mirror_y,
            self.smoothing_alpha,
            self.dead_zone_radius,
        )

    def virtual_surface(self) -> VirtualSurface:
        return VirtualSurface(self.virtual_width_px, self.virtual_height_px)


def load_gesture_mouse_config(
    environ: Mapping[str, str] | None = None,
) -> GestureMouseRuntimeConfig:
    values = os.environ if environ is None else environ
    defaults = GestureMouseRuntimeConfig()
    try:
        return GestureMouseRuntimeConfig(
            enabled=_boolean(values, "GESTURE_MOUSE_ENABLED", defaults.enabled),
            output_mode=_mode(values, defaults.output_mode),
            virtual_width_px=_integer(
                values, "GESTURE_MOUSE_VIRTUAL_WIDTH_PX", defaults.virtual_width_px
            ),
            virtual_height_px=_integer(
                values, "GESTURE_MOUSE_VIRTUAL_HEIGHT_PX", defaults.virtual_height_px
            ),
            active_region=ActiveCameraRegion(
                _real(values, "GESTURE_MOUSE_ACTIVE_LEFT", defaults.active_region.left),
                _real(values, "GESTURE_MOUSE_ACTIVE_TOP", defaults.active_region.top),
                _real(
                    values, "GESTURE_MOUSE_ACTIVE_RIGHT", defaults.active_region.right
                ),
                _real(
                    values, "GESTURE_MOUSE_ACTIVE_BOTTOM", defaults.active_region.bottom
                ),
            ),
            mirror_x=_boolean(values, "GESTURE_MOUSE_MIRROR_X", defaults.mirror_x),
            mirror_y=_boolean(values, "GESTURE_MOUSE_MIRROR_Y", defaults.mirror_y),
            smoothing_alpha=_real(
                values, "GESTURE_MOUSE_SMOOTHING_ALPHA", defaults.smoothing_alpha
            ),
            dead_zone_radius=_real(
                values, "GESTURE_MOUSE_DEAD_ZONE_RADIUS", defaults.dead_zone_radius
            ),
            max_output_hz=_real(
                values, "GESTURE_MOUSE_MAX_OUTPUT_HZ", defaults.max_output_hz
            ),
            button_policy=MouseButtonPolicy(
                buttons_enabled=_boolean(
                    values,
                    "GESTURE_MOUSE_BUTTONS_ENABLED",
                    defaults.button_policy.buttons_enabled,
                ),
                drag_enabled=_boolean(
                    values,
                    "GESTURE_MOUSE_DRAG_ENABLED",
                    defaults.button_policy.drag_enabled,
                ),
                intent_activation_ms=_integer(
                    values,
                    "GESTURE_MOUSE_BUTTON_INTENT_ACTIVATION_MS",
                    defaults.button_policy.intent_activation_ms,
                ),
                intent_release_ms=_integer(
                    values,
                    "GESTURE_MOUSE_BUTTON_INTENT_RELEASE_MS",
                    defaults.button_policy.intent_release_ms,
                ),
                click_cooldown_ms=_integer(
                    values,
                    "GESTURE_MOUSE_CLICK_COOLDOWN_MS",
                    defaults.button_policy.click_cooldown_ms,
                ),
                drag_hold_ms=_integer(
                    values,
                    "GESTURE_MOUSE_DRAG_HOLD_MS",
                    defaults.button_policy.drag_hold_ms,
                ),
                contact_activation_threshold=_real(
                    values,
                    "GESTURE_MOUSE_CONTACT_ACTIVATION_THRESHOLD",
                    defaults.button_policy.contact_activation_threshold,
                ),
                contact_release_threshold=_real(
                    values,
                    "GESTURE_MOUSE_CONTACT_RELEASE_THRESHOLD",
                    defaults.button_policy.contact_release_threshold,
                ),
                contact_isolation_ratio=_real(
                    values,
                    "GESTURE_MOUSE_CONTACT_ISOLATION_RATIO",
                    defaults.button_policy.contact_isolation_ratio,
                ),
            ),
            button_output_mode=_button_output_mode(values, defaults.button_output_mode),
        )
    except MouseValidationError as exc:
        raise GestureMouseConfigurationError(str(exc)) from exc


def _value(values: Mapping[str, str], name: str) -> str | None:
    value = values.get(name)
    if value is None or not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _boolean(values: Mapping[str, str], name: str, default: bool) -> bool:
    value = _value(values, name)
    if value is None:
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise GestureMouseConfigurationError(f"{name} must be a boolean.")


def _mode(
    values: Mapping[str, str], default: GestureMouseOutputMode
) -> GestureMouseOutputMode:
    value = _value(values, "GESTURE_MOUSE_OUTPUT_MODE")
    if value is None:
        return default
    try:
        return GestureMouseOutputMode(value.lower())
    except ValueError as exc:
        raise GestureMouseConfigurationError(
            "GESTURE_MOUSE_OUTPUT_MODE must be virtual or windows."
        ) from exc


def _button_output_mode(
    values: Mapping[str, str], default: MouseButtonOutputMode
) -> MouseButtonOutputMode:
    value = _value(values, "GESTURE_MOUSE_BUTTON_OUTPUT_MODE")
    if value is None:
        return default
    try:
        return MouseButtonOutputMode(value.lower())
    except ValueError as exc:
        raise GestureMouseConfigurationError(
            "GESTURE_MOUSE_BUTTON_OUTPUT_MODE must be null or windows."
        ) from exc


def _integer(values: Mapping[str, str], name: str, default: int) -> int:
    value = _value(values, name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise GestureMouseConfigurationError(f"{name} must be an integer.") from exc


def _real(values: Mapping[str, str], name: str, default: float) -> float:
    value = _value(values, name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise GestureMouseConfigurationError(f"{name} must be numeric.") from exc
    if not isfinite(parsed):
        raise GestureMouseConfigurationError(f"{name} must be finite.")
    return parsed
