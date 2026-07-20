"""Explicit, dependency-injectable construction for optional mouse outputs."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from .button_output import (
    MouseButtonOutputPort,
    NullMouseButtonOutput,
    WindowsMouseButtonApi,
    WindowsMouseButtonOutput,
    create_windows_mouse_button_api,
)
from .config import (
    GestureMouseOutputMode,
    GestureMouseRuntimeConfig,
    MouseButtonOutputMode,
)
from .mapping import VirtualCursorMapper
from .models import MouseOutputError
from .output import (
    NullVirtualCursorOutput,
    VirtualCursorOutputPort,
    WindowsCursorApi,
    WindowsCursorOutput,
    WindowsDesktopBounds,
    create_windows_cursor_api,
)
from .ownership import WindowsCursorOwnershipLease, WindowsNamedMutex
from .runtime import GestureMouseRuntimeCoordinator


@dataclass(frozen=True, slots=True)
class MouseRuntimeDependencies:
    coordinator: GestureMouseRuntimeCoordinator
    cursor_output: VirtualCursorOutputPort
    button_output: MouseButtonOutputPort
    ownership_lease: WindowsCursorOwnershipLease | None


def build_mouse_runtime_dependencies(
    config: GestureMouseRuntimeConfig,
    *,
    owner_id: str,
    cursor_api: WindowsCursorApi | None = None,
    button_api: WindowsMouseButtonApi | None = None,
    ownership_lease: WindowsCursorOwnershipLease | None = None,
    mutex_factory: Callable[[], WindowsNamedMutex] = WindowsNamedMutex,
    platform_name: str | None = None,
    cursor_output_factory: Callable[..., VirtualCursorOutputPort] = WindowsCursorOutput,
    button_output_factory: Callable[
        ..., MouseButtonOutputPort
    ] = WindowsMouseButtonOutput,
    coordinator_factory: Callable[
        ..., GestureMouseRuntimeCoordinator
    ] = GestureMouseRuntimeCoordinator,
) -> MouseRuntimeDependencies:
    """Build one coordinator and its owned outputs, cleaning partial work safely."""

    platform = os.name if platform_name is None else platform_name
    requests_windows_output = (
        config.output_mode is GestureMouseOutputMode.WINDOWS
        or config.button_output_mode is MouseButtonOutputMode.WINDOWS
    )
    active_native_output = (
        config.enabled and config.output_mode is GestureMouseOutputMode.WINDOWS
    ) or (
        config.enabled
        and config.button_policy.buttons_enabled
        and config.button_output_mode is MouseButtonOutputMode.WINDOWS
    )
    if requests_windows_output and platform != "nt":
        raise MouseOutputError("Windows mouse output is unavailable on this platform.")
    if active_native_output and ownership_lease is None:
        raise MouseOutputError("Native mouse output requires a shared ownership lease.")
    mapper = VirtualCursorMapper(config.virtual_surface(), config.mapping_policy())
    cursor_output: VirtualCursorOutputPort = NullVirtualCursorOutput()
    button_output: MouseButtonOutputPort = NullMouseButtonOutput()
    lease = ownership_lease if active_native_output else None
    if lease is not None:
        lease.enable_cross_process(mutex_factory)
    try:
        if config.enabled and config.output_mode is GestureMouseOutputMode.WINDOWS:
            api = cursor_api if cursor_api is not None else create_windows_cursor_api()
            cursor_output = cursor_output_factory(
                WindowsDesktopBounds.from_windows_api(api), api, platform_name=platform
            )
        if (
            config.enabled
            and config.button_policy.buttons_enabled
            and config.button_output_mode is MouseButtonOutputMode.WINDOWS
        ):
            api = (
                button_api
                if button_api is not None
                else create_windows_mouse_button_api()
            )
            button_output = button_output_factory(api, platform_name=platform)
        coordinator = coordinator_factory(
            owner_id,
            enabled=config.enabled,
            mapper=mapper,
            output=cursor_output,
            max_output_hz=config.max_output_hz,
            windows_lease=lease,
            button_policy=config.button_policy,
            button_output=button_output,
        )
    except Exception:
        for output in (button_output, cursor_output):
            try:
                output.close()
            except Exception:
                pass
        raise
    return MouseRuntimeDependencies(coordinator, cursor_output, button_output, lease)
