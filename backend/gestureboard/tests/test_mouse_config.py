from __future__ import annotations

from unittest import TestCase

from gestureboard.mouse import (
    GestureMouseConfigurationError,
    GestureMouseOutputMode,
    GestureMouseRuntimeConfig,
    load_gesture_mouse_config,
)


class GestureMouseConfigTests(TestCase):
    def test_defaults_are_disabled_virtual_and_side_effect_free(self) -> None:
        config = load_gesture_mouse_config({})
        self.assertEqual(config, GestureMouseRuntimeConfig())
        self.assertFalse(config.enabled)
        self.assertEqual(config.output_mode, GestureMouseOutputMode.VIRTUAL)
        self.assertEqual(
            (config.virtual_width_px, config.virtual_height_px), (1920, 1080)
        )

    def test_all_environment_overrides_are_parsed(self) -> None:
        config = load_gesture_mouse_config(
            {
                "GESTURE_MOUSE_ENABLED": "yes",
                "GESTURE_MOUSE_OUTPUT_MODE": "windows",
                "GESTURE_MOUSE_VIRTUAL_WIDTH_PX": "800",
                "GESTURE_MOUSE_VIRTUAL_HEIGHT_PX": "600",
                "GESTURE_MOUSE_MIRROR_X": "true",
                "GESTURE_MOUSE_MIRROR_Y": "1",
                "GESTURE_MOUSE_SMOOTHING_ALPHA": "0.5",
                "GESTURE_MOUSE_DEAD_ZONE_RADIUS": "0.1",
                "GESTURE_MOUSE_ACTIVE_LEFT": "0.1",
                "GESTURE_MOUSE_ACTIVE_TOP": "0.2",
                "GESTURE_MOUSE_ACTIVE_RIGHT": "0.9",
                "GESTURE_MOUSE_ACTIVE_BOTTOM": "0.8",
                "GESTURE_MOUSE_MAX_OUTPUT_HZ": "120",
            }
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.output_mode, GestureMouseOutputMode.WINDOWS)
        self.assertEqual(config.virtual_surface().width_px, 800)
        self.assertEqual(config.mapping_policy().smoothing_alpha, 0.5)
        self.assertEqual(config.max_output_hz, 120.0)

    def test_blank_values_use_defaults(self) -> None:
        self.assertEqual(
            load_gesture_mouse_config({"GESTURE_MOUSE_ENABLED": "   "}),
            GestureMouseRuntimeConfig(),
        )

    def test_invalid_environment_values_are_rejected(self) -> None:
        invalid = (
            {"GESTURE_MOUSE_ENABLED": "maybe"},
            {"GESTURE_MOUSE_OUTPUT_MODE": "real"},
            {"GESTURE_MOUSE_VIRTUAL_WIDTH_PX": "0"},
            {"GESTURE_MOUSE_VIRTUAL_HEIGHT_PX": "-1"},
            {"GESTURE_MOUSE_SMOOTHING_ALPHA": "NaN"},
            {"GESTURE_MOUSE_DEAD_ZONE_RADIUS": "Infinity"},
            {"GESTURE_MOUSE_ACTIVE_LEFT": "1", "GESTURE_MOUSE_ACTIVE_RIGHT": "1"},
            {"GESTURE_MOUSE_MAX_OUTPUT_HZ": "0"},
            {"GESTURE_MOUSE_MAX_OUTPUT_HZ": "241"},
        )
        for values in invalid:
            with (
                self.subTest(values=values),
                self.assertRaises(GestureMouseConfigurationError),
            ):
                load_gesture_mouse_config(values)

    def test_model_rejects_numeric_booleans_and_boundary_violations(self) -> None:
        for kwargs in (
            {"virtual_width_px": True},
            {"smoothing_alpha": 0.0},
            {"dead_zone_radius": 1.1},
            {"max_output_hz": True},
        ):
            with (
                self.subTest(kwargs=kwargs),
                self.assertRaises(GestureMouseConfigurationError),
            ):
                GestureMouseRuntimeConfig(**kwargs)
