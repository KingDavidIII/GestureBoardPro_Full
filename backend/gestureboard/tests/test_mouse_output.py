from __future__ import annotations

from unittest import TestCase

from gestureboard.mouse import (
    MouseLifecycleError,
    MouseOutputError,
    MouseValidationError,
    VirtualCursorTarget,
    WindowsCursorOutput,
    WindowsDesktopBounds,
)


class FakeWindowsApi:
    def __init__(
        self, metrics: tuple[int, int, int, int], succeeds: bool = True
    ) -> None:
        self.metrics = metrics
        self.succeeds = succeeds
        self.metric_calls: list[int] = []
        self.moves: list[tuple[int, int]] = []
        self.position = (0, 0)

    def get_system_metrics(self, metric_id: int) -> int:
        self.metric_calls.append(metric_id)
        return self.metrics[len(self.metric_calls) - 1]

    def set_cursor_pos(self, x: int, y: int) -> bool:
        self.moves.append((x, y))
        if self.succeeds:
            self.position = (x, y)
        return self.succeeds

    def get_cursor_pos(self) -> tuple[int, int]:
        return self.position


def target(x: int, y: int) -> VirtualCursorTarget:
    return VirtualCursorTarget(0.5, 0.5, x, y, 1, 0)


class WindowsCursorOutputTests(TestCase):
    def test_bounds_factory_preserves_negative_origin_and_reads_each_metric_once(
        self,
    ) -> None:
        api = FakeWindowsApi((-100, -50, 200, 100))
        bounds = WindowsDesktopBounds.from_windows_api(api)
        self.assertEqual(bounds, WindowsDesktopBounds(-100, -50, 200, 100))
        self.assertEqual(len(api.metric_calls), 4)

    def test_invalid_bounds_reject_before_any_cursor_movement(self) -> None:
        api = FakeWindowsApi((0, 0, 0, 1))
        with self.assertRaises(MouseValidationError):
            WindowsDesktopBounds.from_windows_api(api)
        self.assertEqual(api.moves, [])

    def test_translation_clamping_and_exactly_one_call(self) -> None:
        api = FakeWindowsApi((-100, -50, 200, 100))
        output = WindowsCursorOutput(
            WindowsDesktopBounds.from_windows_api(api), api, platform_name="nt"
        )
        output.move(target(0, 0))
        output.move(target(199, 99))
        output.move(target(999, 0))
        self.assertEqual(api.moves, [(-100, -50), (99, 49), (99, -50)])
        self.assertEqual(api.get_cursor_pos(), (99, -50))

    def test_output_failure_platform_and_lifecycle_are_safe(self) -> None:
        api = FakeWindowsApi((0, 0, 10, 10), succeeds=False)
        with self.assertRaises(MouseOutputError):
            WindowsCursorOutput(
                WindowsDesktopBounds(0, 0, 1, 1), api, platform_name="posix"
            )
        output = WindowsCursorOutput(
            WindowsDesktopBounds(0, 0, 10, 10), api, platform_name="nt"
        )
        with self.assertRaises(MouseOutputError):
            output.move(target(1, 1))
        output.close()
        output.close()
        with self.assertRaises(MouseLifecycleError):
            output.move(target(1, 1))
        self.assertFalse(hasattr(output, "click"))
        self.assertFalse(hasattr(output, "scroll"))
