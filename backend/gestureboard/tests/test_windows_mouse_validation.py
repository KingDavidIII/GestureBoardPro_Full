"""Deterministic tests for guarded Windows mouse validation; never native input."""

from __future__ import annotations

import ctypes
import json
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from gestureboard.management.commands.validate_windows_mouse import TkValidationWindow
from gestureboard.mouse.buttons import MouseButton
from gestureboard.mouse.ownership import _CtypesNamedMutexApi
from gestureboard.mouse.windows_validation import (
    ACKNOWLEDGEMENT,
    NativeOutputs,
    RecordedMouseEvent,
    ScenarioResult,
    ScreenPoint,
    ValidationRegion,
    WindowsMouseValidationRunner,
    WindowsNamedMutex,
    build_report,
    validate_countdown,
    validate_drag_points,
    write_report_atomic,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakeWindow:
    def __init__(self) -> None:
        self.recorded: list[RecordedMouseEvent] = []
        self.has_focus = True
        self.is_closed = False
        self.close_count = 0
        self.statuses: list[str] = []
        self.held = False

    def safe_region(self):
        return ValidationRegion(100, 100, 600, 400)

    def click_target(self):
        return ScreenPoint(300, 180)

    def drag_points(self):
        return (ScreenPoint(180, 320), ScreenPoint(280, 320), ScreenPoint(420, 320))

    def drag_region(self):
        return ValidationRegion(180, 290, 450, 350)

    def focused(self):
        return self.has_focus

    def closed(self):
        return self.is_closed

    def events(self):
        return tuple(self.recorded)

    def clear_events(self):
        self.recorded.clear()

    def update_status(self, text):
        self.statuses.append(text)

    def pump(self):
        return None

    def close(self):
        self.close_count += 1


class FakeCursor:
    def __init__(self, window: FakeWindow, *, fail=False, close_fail=False) -> None:
        self.window = window
        self.fail = fail
        self.close_fail = close_fail
        self.moves = []
        self.close_count = 0
        self.position = ScreenPoint(50, 60)
        self.update_position = True

    def move(self, target):
        if self.fail:
            raise RuntimeError("operational cursor failure")
        point = ScreenPoint(target.x_px, target.y_px)
        self.moves.append(point)
        if self.update_position:
            self.position = point
        self.window.recorded.append(RecordedMouseEvent("move", point, self.window.held))

    def close(self):
        self.close_count += 1
        if self.close_fail:
            raise RuntimeError("cursor cleanup failure")


class FakeButtons:
    def __init__(
        self, window: FakeWindow, *, release_fail=False, close_fail=False
    ) -> None:
        self.window = window
        self.release_fail = release_fail
        self.close_fail = close_fail
        self.close_count = 0
        self.release_count = 0
        self.down_count = 0
        self.up_count = 0

    def button_down(self, button):
        assert button is MouseButton.PRIMARY
        self.down_count += 1
        self.window.held = True
        point = next(
            event.point
            for event in reversed(self.window.recorded)
            if event.kind == "move"
        )
        self.window.recorded.append(RecordedMouseEvent("down", point, True))

    def button_up(self, button):
        assert button is MouseButton.PRIMARY
        self.up_count += 1
        if self.window.held:
            self.window.held = False
            point = next(
                event.point
                for event in reversed(self.window.recorded)
                if event.kind == "move"
            )
            self.window.recorded.append(RecordedMouseEvent("up", point, False))

    def release_all(self):
        self.release_count += 1
        if self.release_fail:
            raise RuntimeError("release cleanup failure")
        self.button_up(MouseButton.PRIMARY)

    def close(self):
        self.close_count += 1
        if self.close_fail:
            raise RuntimeError("button cleanup failure")
        self.release_all()


class FakeMutexApi:
    def __init__(self, already_exists=False, release_error=None):
        self.already_exists = already_exists
        self.release_error = release_error
        self.releases = 0

    def create(self, name):
        return object(), self.already_exists

    def release(self, handle):
        self.releases += 1
        if self.release_error:
            raise self.release_error


def runner(
    *,
    cursor_fail=False,
    release_fail=False,
    cursor_close_fail=False,
    button_close_fail=False,
    emergency=lambda: False,
    timeout=20,
):
    window = FakeWindow()
    clock = FakeClock()
    cursor = FakeCursor(window, fail=cursor_fail, close_fail=cursor_close_fail)
    buttons = FakeButtons(
        window, release_fail=release_fail, close_fail=button_close_fail
    )
    outputs = NativeOutputs(cursor, buttons)
    restored = []
    service = WindowsMouseValidationRunner(
        window,
        outputs,
        clock=clock,
        sleep=clock.sleep,
        emergency_stop=emergency,
        cursor_position=lambda: cursor.position,
        restore_cursor=lambda point: restored.append(point) is None or True,
        timeout_seconds=timeout,
    )
    return service, window, cursor, buttons, restored


class WindowsMouseValidationTests(SimpleTestCase):
    def test_default_command_is_dry_run_and_never_acquires_native_output(self):
        with patch(
            "gestureboard.management.commands.validate_windows_mouse.acquire_native_outputs",
            side_effect=AssertionError("native boundary reached"),
        ):
            result = call_command("validate_windows_mouse")
        self.assertIsNone(result)

    def test_non_windows_live_refusal(self):
        with patch(
            "gestureboard.management.commands.validate_windows_mouse.os.name", "posix"
        ):
            with self.assertRaisesMessage(CommandError, "requires Windows"):
                call_command(
                    "validate_windows_mouse", live=True, acknowledge=ACKNOWLEDGEMENT
                )

    def test_non_interactive_live_refusal(self):
        with (
            patch(
                "gestureboard.management.commands.validate_windows_mouse.os.name", "nt"
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.sys.stdin.isatty",
                return_value=False,
            ),
        ):
            with self.assertRaisesMessage(CommandError, "interactive terminal"):
                call_command(
                    "validate_windows_mouse", live=True, acknowledge=ACKNOWLEDGEMENT
                )

    def test_acknowledgement_without_live_is_rejected(self):
        with self.assertRaisesMessage(CommandError, "only with --live"):
            call_command("validate_windows_mouse", acknowledge=ACKNOWLEDGEMENT)

    def test_incorrect_live_acknowledgement_is_rejected_before_acquisition(self):
        with (
            patch(
                "gestureboard.management.commands.validate_windows_mouse.os.name", "nt"
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.sys.stdin.isatty",
                return_value=True,
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.sys.stdout.isatty",
                return_value=True,
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.interactive_desktop_available",
                return_value=True,
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.acquire_native_outputs",
                side_effect=AssertionError("native boundary reached"),
            ),
        ):
            with self.assertRaisesMessage(CommandError, "requires --acknowledge"):
                call_command("validate_windows_mouse", live=True, acknowledge="wrong")

    def test_countdown_validation(self):
        for invalid in (True, 0, 2, 31, 1.5):
            with self.assertRaises(ValueError):
                validate_countdown(invalid)  # type: ignore[arg-type]

    def test_coordinate_bounding_rejects_external_target(self):
        service, window, *_ = runner()
        window.click_target = lambda: ScreenPoint(999, 999)
        report = service.run("click", 3)
        self.assertIn("outside", report.operational_error or "")

    def test_focus_loss_aborts(self):
        service, window, *_ = runner()
        window.has_focus = False
        report = service.run("cursor", 3)
        self.assertEqual(report.cancellation_reason, "validation window lost focus")

    def test_escape_aborts(self):
        service, *_ = runner(emergency=lambda: True)
        report = service.run("cursor", 3)
        self.assertEqual(report.cancellation_reason, "Escape emergency stop")

    def test_timeout_aborts(self):
        service, *_ = runner(timeout=-1)
        report = service.run("cursor", 3)
        self.assertEqual(report.cancellation_reason, "scenario timeout")

    def test_cursor_scenario_verifies_expected_location(self):
        report = runner()[0].run("cursor", 3)
        self.assertTrue(report.overall_pass)
        self.assertEqual(report.scenarios[0].observed_point, ScreenPoint(520, 250))

    def test_cursor_silent_native_noop_fails_without_later_scenarios(self):
        service, _, _, buttons, restored = runner()
        service.outputs.cursor.update_position = False

        report = service.run("all", 3)

        self.assertEqual(
            [(item.name, item.status) for item in report.scenarios],
            [("cursor", "failed")],
        )
        self.assertIn("native cursor did not reach", report.scenarios[0].detail or "")
        self.assertEqual(buttons.down_count, 0)
        self.assertEqual(restored, [ScreenPoint(50, 60)])

    def test_cursor_accepts_delayed_native_position_without_tk_motion(self):
        service, window, cursor, _, _ = runner()
        original_move = cursor.move
        polls = 0

        def native_position():
            nonlocal polls
            polls += 1
            return ScreenPoint(50, 60) if polls < 3 else cursor.position

        def no_tk_motion(target):
            original_move(target)
            window.recorded.clear()

        service.cursor_position = native_position
        cursor.move = no_tk_motion
        report = service.run("cursor", 3)

        self.assertTrue(report.overall_pass)
        self.assertEqual(report.scenarios[0].observed_start, ScreenPoint(180, 250))
        self.assertEqual(report.scenarios[0].observed_end, ScreenPoint(520, 250))
        self.assertEqual(report.scenarios[0].observed_events, ())
        self.assertEqual(report.scenarios[0].detail, "No Tk motion evidence")

    def test_cursor_uses_two_distinct_points_and_cancellable_visible_dwell(self):
        service, _, cursor, _, _ = runner()
        service.emergency_stop = lambda: service.clock() >= 3.1

        report = service.run("cursor", 3)

        self.assertEqual(report.cancellation_reason, "Escape emergency stop")
        self.assertGreaterEqual(len(cursor.moves), 2)
        self.assertNotEqual(cursor.moves[0], cursor.moves[1])

    def test_click_and_drag_never_press_before_native_position_verification(self):
        for scenario in ("click", "drag"):
            with self.subTest(scenario=scenario):
                service, _, cursor, buttons, _ = runner()
                cursor.update_position = False

                report = service.run(scenario, 3)

                self.assertIn(
                    "native cursor did not reach", report.operational_error or ""
                )
                self.assertEqual(buttons.down_count, 0)

    def test_click_requires_exact_press_release_order(self):
        report = runner()[0].run("click", 3)
        self.assertEqual(report.scenarios[0].observed_events, ("down", "up"))
        self.assertTrue(report.overall_pass)

    def test_click_rejects_button_event_away_from_target(self):
        service, window, cursor, buttons, _ = runner()
        original = buttons.button_down

        def misplaced(button):
            original(button)
            event = window.recorded[-1]
            window.recorded[-1] = RecordedMouseEvent(
                event.kind,
                ScreenPoint(event.point.x + 20, event.point.y),
                event.primary_held,
            )

        buttons.button_down = misplaced
        report = service.run("click", 3)
        self.assertEqual(report.scenarios[0].status, "failed")

    def test_drag_requires_down_held_motion_and_up(self):
        report = runner()[0].run("drag", 3)
        self.assertTrue(report.overall_pass)
        self.assertEqual(report.scenarios[0].observed_events[-1], "up")

    def test_drag_waits_for_delayed_down_before_native_waypoints(self):
        service, window, cursor, buttons, _ = runner()
        original_down = buttons.button_down

        def delayed_down(button):
            buttons.down_count += 1
            window.held = True

        def pump_delayed_down():
            if window.held and not any(
                event.kind == "down" for event in window.recorded
            ):
                original_down(MouseButton.PRIMARY)

        buttons.button_down = delayed_down
        window.pump = pump_delayed_down
        report = service.run("drag", 3)

        self.assertTrue(report.overall_pass)
        self.assertGreaterEqual(len(cursor.moves), 3)

    def test_drag_misplaced_down_prevents_waypoint_movement_and_cleans_up(self):
        service, window, cursor, buttons, restored = runner()
        original_down = buttons.button_down

        def misplaced_down(button):
            original_down(button)
            event = window.recorded[-1]
            window.recorded[-1] = RecordedMouseEvent(
                "down", ScreenPoint(event.point.x + 20, event.point.y), True
            )

        buttons.button_down = misplaced_down
        report = service.run("drag", 3)

        self.assertIn("misplaced", report.operational_error or "")
        self.assertEqual(len(cursor.moves), 1)
        self.assertEqual(restored, [ScreenPoint(50, 60)])
        self.assertGreaterEqual(buttons.release_count, 1)

    def test_drag_missing_or_duplicate_up_fails_safely(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                service, window, _, buttons, restored = runner()
                original_up = buttons.button_up

                def invalid_up(
                    button,
                    original_up=original_up,
                    duplicate=duplicate,
                    window=window,
                ):
                    original_up(button)
                    if not duplicate:
                        window.recorded.pop()
                    else:
                        window.recorded.append(
                            RecordedMouseEvent("up", ScreenPoint(420, 320), False)
                        )

                buttons.button_up = invalid_up
                report = service.run("drag", 3)

                self.assertFalse(report.overall_pass)
                self.assertTrue(report.operational_error)
                self.assertEqual(restored, [ScreenPoint(50, 60)])

    def test_drag_rejects_duplicate_up_after_valid_explicit_release(self):
        service, window, _, buttons, _ = runner()
        original_up = buttons.button_up
        pump_count = 0
        release_started = False

        def pump_delayed_duplicate_up():
            nonlocal pump_count
            pump_count += 1
            if release_started and pump_count == 2:
                window.recorded.append(
                    RecordedMouseEvent("up", ScreenPoint(420, 320), False)
                )

        def up_with_delayed_duplicate(button):
            nonlocal release_started, pump_count
            original_up(button)
            release_started = True
            pump_count = 0

        buttons.button_up = up_with_delayed_duplicate
        window.pump = pump_delayed_duplicate_up
        report = service.run("drag", 3)

        self.assertIn("duplicate button event", report.operational_error or "")

    def test_drag_lane_change_between_preflight_and_waypoint_fails_before_move(self):
        service, window, cursor, buttons, _ = runner()
        original_move = cursor.move

        def change_lane_after_start(target):
            original_move(target)
            if cursor.position == ScreenPoint(180, 320):
                window.drag_region = lambda: ValidationRegion(180, 319, 300, 321)

        cursor.move = change_lane_after_start
        report = service.run("drag", 3)

        self.assertIn("drag lane changed", report.operational_error or "")
        self.assertEqual(len(cursor.moves), 1)
        self.assertGreaterEqual(buttons.release_count, 1)

    def test_drag_waypoint_native_timeout_releases_and_prevents_interruption(self):
        service, window, cursor, buttons, restored = runner()
        original_events = window.events
        drag_down_verified = False

        def events_after_verified_drag_down():
            nonlocal drag_down_verified
            events = original_events()
            if buttons.down_count == 2 and any(
                event.kind == "down" for event in events
            ):
                if drag_down_verified:
                    cursor.update_position = False
                else:
                    drag_down_verified = True
            return events

        window.events = events_after_verified_drag_down
        report = service.run("all", 3)

        self.assertEqual(
            [(item.name, item.status) for item in report.scenarios],
            [("cursor", "passed"), ("click", "passed")],
        )
        self.assertIn("native cursor did not reach", report.operational_error or "")
        self.assertEqual(buttons.down_count, 2)
        self.assertGreaterEqual(buttons.release_count, 1)
        self.assertEqual(restored, [ScreenPoint(50, 60)])
        self.assertEqual(
            (buttons.close_count, cursor.close_count, window.close_count), (1, 1, 1)
        )

    def test_drag_button_wait_honours_escape_focus_loss_and_window_closure(self):
        for state in ("escape", "focus", "closed"):
            with self.subTest(state=state):
                service, window, _, buttons, _ = runner()
                buttons.button_down = lambda button, window=window: setattr(
                    window, "held", True
                )
                if state == "escape":
                    service.emergency_stop = lambda service=service: (
                        service.clock() >= 3.01
                    )
                elif state == "focus":
                    window.pump = lambda window=window: setattr(
                        window, "has_focus", False
                    )
                else:
                    window.pump = lambda window=window: setattr(
                        window, "is_closed", True
                    )

                report = service.run("drag", 3)

                self.assertIsNotNone(report.cancellation_reason)
                self.assertGreaterEqual(buttons.release_count, 1)

    def test_duplicate_release_is_rejected_by_click_verifier(self):
        service, window, _, buttons, _ = runner()
        original = buttons.button_up

        def duplicate(button):
            original(button)
            window.recorded.append(RecordedMouseEvent("up", ScreenPoint(0, 0), False))

        buttons.button_up = duplicate
        report = service.run("click", 3)
        self.assertFalse(report.scenarios[0].status == "passed")

    def test_duplicate_down_fails_drag_before_native_waypoint_movement(self):
        service, window, cursor, buttons, _ = runner()
        original = buttons.button_down

        def duplicate(button):
            original(button)
            window.recorded.append(RecordedMouseEvent("down", ScreenPoint(0, 0), True))

        buttons.button_down = duplicate
        report = service.run("drag", 3)
        self.assertIn("duplicate", report.operational_error or "")
        self.assertEqual(len(cursor.moves), 1)

    def test_interruption_releases_active_drag(self):
        report = runner()[0].run("interruption", 3)
        self.assertTrue(report.overall_pass)
        self.assertEqual(report.scenarios[0].observed_events, ("down", "up"))

    def test_all_stops_after_failed_cursor_before_later_native_scenarios(self):
        service, _, cursor, buttons, _ = runner()
        cursor.move = lambda target: None

        report = service.run("all", 3)

        self.assertEqual(
            [(item.name, item.status) for item in report.scenarios],
            [("cursor", "failed")],
        )
        self.assertEqual(buttons.down_count, 0)
        self.assertEqual(cursor.moves, [])
        self.assertIn("Failed scenario: cursor", service.window.statuses)

    def test_all_stops_after_failed_click_before_drag_and_interruption(self):
        service, window, cursor, buttons, _ = runner()
        original_down = buttons.button_down

        def unrecorded_down(button):
            buttons.down_count += 1
            window.held = True

        buttons.button_down = unrecorded_down
        report = service.run("all", 3)

        self.assertEqual(
            [(item.name, item.status) for item in report.scenarios],
            [
                ("cursor", "passed"),
                ("click", "failed"),
            ],
        )
        self.assertEqual(buttons.down_count, 1)
        self.assertEqual(len(cursor.moves), 3)
        self.assertIn("Failed scenario: click", window.statuses)
        buttons.button_down = original_down

    def test_all_stops_after_failed_drag_before_interruption(self):
        service, window, cursor, buttons, _ = runner()
        original_up = buttons.button_up

        def omit_drag_release(button):
            buttons.up_count += 1
            if buttons.down_count == 2:
                window.held = False
                return
            original_up(button)

        buttons.button_up = omit_drag_release
        report = service.run("all", 3)

        self.assertEqual(
            [(item.name, item.status) for item in report.scenarios],
            [("cursor", "passed"), ("click", "passed")],
        )
        self.assertIn("required Tk button event", report.operational_error or "")
        self.assertEqual(buttons.down_count, 2)
        self.assertEqual(len(cursor.moves), 6)
        self.assertIn("Final result: FAIL", window.statuses)

    def test_interruption_event_inspection_failure_still_completes_cleanup(self):
        service, window, cursor, buttons, restored = runner()
        lease = MagicMock()
        service.outputs.lease = lease
        original_events = window.events
        event_calls = 0

        def fail_final_inspection():
            nonlocal event_calls
            event_calls += 1
            if event_calls > 0:
                raise RuntimeError("inspection")
            return original_events()

        window.events = fail_final_inspection

        report = service.run("interruption", 3)

        self.assertEqual(report.scenarios[0].name, "interruption")
        self.assertEqual(report.scenarios[0].status, "failed")
        self.assertIn("event inspection failed", report.scenarios[0].detail or "")
        self.assertIsNone(report.operational_error)
        self.assertIn(
            "interruption event inspection: inspection", report.cleanup_errors
        )
        self.assertFalse(report.overall_pass)
        self.assertEqual(buttons.release_count, 2)
        self.assertEqual(restored, [ScreenPoint(50, 60)])
        self.assertEqual(
            (buttons.close_count, cursor.close_count, window.close_count), (1, 1, 1)
        )
        lease.release.assert_called_once_with(service.outputs.owner_id)

    def test_operational_error_survives_cleanup_failures(self):
        report = runner(cursor_fail=True, release_fail=True)[0].run("cursor", 3)
        self.assertIn("operational cursor failure", report.operational_error or "")
        self.assertTrue(report.cleanup_errors)

    def test_later_cleanup_continues_after_earlier_failure(self):
        service, window, cursor, buttons, restored = runner(
            release_fail=True, cursor_close_fail=True, button_close_fail=True
        )
        report = service.run("cursor", 3)
        self.assertEqual(cursor.close_count, 1)
        self.assertEqual(buttons.close_count, 1)
        self.assertEqual(window.close_count, 1)
        self.assertEqual(restored, [ScreenPoint(50, 60)])
        self.assertGreaterEqual(len(report.cleanup_errors), 3)

    def test_owned_dependencies_close_once(self):
        service, _, cursor, buttons, _ = runner()
        service.run("cursor", 3)
        service.outputs.close_once([])
        self.assertEqual((cursor.close_count, buttons.close_count), (1, 1))

    def test_external_dependencies_are_not_closed(self):
        service, _, cursor, buttons, _ = runner()
        service.outputs.owned = False
        service.run("cursor", 3)
        self.assertEqual((cursor.close_count, buttons.close_count), (0, 0))

    def test_original_cursor_restoration_is_attempted(self):
        service, _, _, _, restored = runner()
        report = service.run("cursor", 3)
        self.assertEqual(restored, [ScreenPoint(50, 60)])
        self.assertEqual(report.cursor_restoration_status, "restored")

    def test_atomic_json_report_has_only_approved_fields(self):
        report = build_report("all", False, 5, (ScenarioResult("cursor", "passed"),))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "report.json")
            write_report_atomic(path, report)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(payload),
                {
                    "schema_version",
                    "utc_timestamp",
                    "platform",
                    "python_version",
                    "command_scenario",
                    "live",
                    "countdown_seconds",
                    "scenarios",
                    "cancellation_reason",
                    "operational_error",
                    "cleanup_errors",
                    "final_release_status",
                    "cursor_restoration_status",
                    "overall_pass",
                },
            )
            self.assertFalse(
                any(item.suffix == ".tmp" for item in path.parent.iterdir())
            )

    def test_reports_and_scenario_results_are_immutable(self):
        item = ScenarioResult("cursor", "passed")
        with self.assertRaises(FrozenInstanceError):
            item.status = "failed"  # type: ignore[misc]

    def test_drag_distance_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            validate_drag_points((ScreenPoint(0, 0), ScreenPoint(1000, 0)))

    def test_cursor_capture_failure_still_cleans_every_owned_dependency(self):
        service, window, cursor, buttons, _ = runner()
        service.cursor_position = lambda: (_ for _ in ()).throw(RuntimeError("capture"))
        report = service.run("cursor", 3)
        self.assertIn("capture", report.operational_error or "")
        self.assertEqual(
            (
                buttons.release_count,
                buttons.close_count,
                cursor.close_count,
                window.close_count,
            ),
            (2, 1, 1, 1),
        )
        self.assertFalse(report.overall_pass)

    def test_countdown_status_failure_enters_cleanup(self):
        service, window, cursor, buttons, _ = runner()
        window.update_status = lambda text: (_ for _ in ()).throw(
            RuntimeError("status")
        )
        report = service.run("cursor", 3)
        self.assertIn("status", report.operational_error or "")
        self.assertEqual(
            (buttons.close_count, cursor.close_count, window.close_count), (1, 1, 1)
        )

    def test_report_construction_failure_occurs_after_cleanup(self):
        service, window, cursor, buttons, _ = runner()
        with patch(
            "gestureboard.mouse.windows_validation.build_report",
            side_effect=RuntimeError("report"),
        ):
            with self.assertRaisesMessage(RuntimeError, "report"):
                service.run("cursor", 3)
        self.assertEqual(
            (buttons.close_count, cursor.close_count, window.close_count), (1, 1, 1)
        )

    def test_invalid_scenario_after_dependencies_still_cleans(self):
        service, window, cursor, buttons, _ = runner()
        report = service.run("invalid", 3)
        self.assertIn("unsupported", report.operational_error or "")
        self.assertEqual(
            (buttons.close_count, cursor.close_count, window.close_count), (1, 1, 1)
        )

    def test_cleanup_error_forces_overall_failure(self):
        report = runner(cursor_close_fail=True)[0].run("cursor", 3)
        self.assertTrue(report.cleanup_errors)
        self.assertFalse(report.overall_pass)

    def test_restoration_failure_forces_overall_failure(self):
        service, *_ = runner()
        service.restore_cursor = lambda point: False
        report = service.run("cursor", 3)
        self.assertEqual(report.cursor_restoration_status, "restore failed")
        self.assertFalse(report.overall_pass)

    def test_window_close_failure_forces_overall_failure(self):
        service, window, *_ = runner()
        window.close = lambda: (_ for _ in ()).throw(RuntimeError("window close"))
        report = service.run("cursor", 3)
        self.assertIn("window close", report.cleanup_errors[0])
        self.assertFalse(report.overall_pass)

    def test_dry_run_explicitly_does_not_claim_pass(self):
        self.assertFalse(build_report("all", False, 5).overall_pass)

    def test_interruption_release_comes_from_final_cleanup(self):
        service, _, _, buttons, _ = runner()
        report = service.run("interruption", 3)
        self.assertEqual(buttons.release_count, 2)
        self.assertEqual(report.scenarios[0].observed_events, ("down", "up"))
        self.assertTrue(report.overall_pass)

    def test_observed_one_pixel_drag_fails(self):
        service, window, *_ = runner()
        window.drag_points = lambda: (ScreenPoint(180, 320), ScreenPoint(181, 320))
        report = service.run("drag", 3)
        self.assertEqual(report.scenarios[0].status, "failed")

    def test_native_drag_waypoint_outside_lane_fails(self):
        service, window, *_ = runner()
        window.drag_region = lambda: ValidationRegion(180, 319, 300, 321)
        report = service.run("drag", 3)
        self.assertIn("planned drag waypoint", report.operational_error or "")

    def test_drag_succeeds_without_tk_held_motion_evidence(self):
        service, window, cursor, *_ = runner()

        def nonheld(target):
            point = ScreenPoint(target.x_px, target.y_px)
            cursor.moves.append(point)
            cursor.position = point
            window.recorded.append(RecordedMouseEvent("move", point, False))

        cursor.move = nonheld
        report = service.run("drag", 3)
        self.assertTrue(report.overall_pass)
        self.assertEqual(report.scenarios[0].detail, "No Tk held-motion evidence")

    def test_drag_with_literal_down_up_only_uses_native_cursor_coordinates(self):
        service, window, cursor, buttons, _ = runner()

        def native_only_move(target):
            point = ScreenPoint(target.x_px, target.y_px)
            cursor.moves.append(point)
            cursor.position = point

        def native_down(button):
            buttons.down_count += 1
            window.held = True
            window.recorded.append(RecordedMouseEvent("down", cursor.position, True))

        def native_up(button):
            buttons.up_count += 1
            window.held = False
            window.recorded.append(RecordedMouseEvent("up", cursor.position, False))

        cursor.move = native_only_move
        buttons.button_down = native_down
        buttons.button_up = native_up
        report = service.run("drag", 3)

        self.assertTrue(report.overall_pass)
        self.assertEqual(report.scenarios[0].observed_events, ("down", "up"))
        self.assertEqual(report.scenarios[0].detail, "No Tk held-motion evidence")

    def test_unsafe_planned_drag_waypoint_is_rejected_before_cursor_or_button_action(
        self,
    ):
        service, window, cursor, buttons, _ = runner()
        window.drag_points = lambda: (
            ScreenPoint(180, 320),
            ScreenPoint(500, 380),
        )
        report = service.run("drag", 3)

        self.assertIn("planned drag waypoint", report.operational_error or "")
        self.assertEqual(cursor.moves, [])
        self.assertEqual(buttons.down_count, 0)

    def test_motion_after_release_is_supporting_evidence_only(self):
        service, window, _, buttons, _ = runner()
        original = buttons.button_up

        def move_after_release(button):
            original(button)
            window.recorded.append(
                RecordedMouseEvent("move", ScreenPoint(420, 320), True)
            )

        buttons.button_up = move_after_release
        report = service.run("drag", 3)
        self.assertTrue(report.overall_pass)

    def test_partial_offscreen_and_layout_change_fail_closed(self):
        service, *_ = runner()
        service.desktop_region = lambda: ValidationRegion(-100, -100, 500, 350)
        report = service.run("cursor", 3)
        self.assertIn("layout changed", report.operational_error or "")
        self.assertFalse(report.overall_pass)

    def test_negative_desktop_origin_is_supported_without_clamping(self):
        service, window, cursor, *_ = runner()
        service.outputs.desktop_origin = ScreenPoint(-500, -300)
        service.outputs.desktop_region = ValidationRegion(-500, -300, 800, 700)

        def translated(target):
            cursor.moves.append(ScreenPoint(target.x_px, target.y_px))
            cursor.position = ScreenPoint(target.x_px - 500, target.y_px - 300)
            window.recorded.append(
                RecordedMouseEvent(
                    "move",
                    ScreenPoint(target.x_px - 500, target.y_px - 300),
                    window.held,
                )
            )

        cursor.move = translated
        report = service.run("cursor", 3)
        self.assertTrue(report.overall_pass)
        self.assertEqual(cursor.moves[0], ScreenPoint(680, 550))

    def test_desktop_origin_change_fails_even_when_window_still_fits(self):
        service, *_ = runner()
        service.desktop_region = lambda: ValidationRegion(-20, 0, 1899, 1079)
        report = service.run("cursor", 3)
        self.assertIn("layout changed", report.operational_error or "")

    def test_desktop_size_change_fails_even_when_window_still_fits(self):
        service, *_ = runner()
        service.desktop_region = lambda: ValidationRegion(0, 0, 1599, 899)
        report = service.run("cursor", 3)
        self.assertIn("layout changed", report.operational_error or "")

    def test_stale_adapter_clamp_is_rejected_before_cursor_output(self):
        service, window, cursor, *_ = runner()
        service.outputs.desktop_region = ValidationRegion(0, 0, 500, 500)
        service.desktop_region = lambda: ValidationRegion(0, 0, 1919, 1079)
        window.click_target = lambda: ScreenPoint(550, 180)
        report = service.run("cursor", 3)
        self.assertFalse(cursor.moves)
        self.assertIn("layout changed", report.operational_error or "")

    def test_original_cursor_outside_changed_desktop_is_not_restored(self):
        service, _, _, _, restored = runner()
        calls = 0

        def current_desktop():
            nonlocal calls
            calls += 1
            return (
                ValidationRegion(0, 0, 1919, 1079)
                if calls == 1
                else ValidationRegion(100, 100, 1919, 1079)
            )

        service.desktop_region = current_desktop
        report = service.run("cursor", 3)
        self.assertEqual(restored, [])
        self.assertEqual(report.cursor_restoration_status, "restore failed")
        self.assertFalse(report.overall_pass)

    def test_small_desktop_is_rejected_before_tk_window_creation(self):
        with patch(
            "gestureboard.management.commands.validate_windows_mouse.tk.Tk",
            side_effect=AssertionError("Tk must not be created"),
        ):
            with self.assertRaisesMessage(RuntimeError, "too small"):
                TkValidationWindow(ValidationRegion(0, 0, 639, 479))

    def test_partial_tk_construction_destroys_root_once(self):
        root = MagicMock()
        with (
            patch(
                "gestureboard.management.commands.validate_windows_mouse.tk.Tk",
                return_value=root,
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.tk.StringVar"
            ),
            patch("gestureboard.management.commands.validate_windows_mouse.tk.Label"),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.tk.Canvas",
                side_effect=RuntimeError("canvas construction"),
            ),
        ):
            with self.assertRaisesMessage(RuntimeError, "canvas construction"):
                TkValidationWindow(ValidationRegion(0, 0, 1919, 1079))
        root.destroy.assert_called_once_with()

    def test_partial_tk_construction_preserves_error_when_destroy_fails(self):
        root = MagicMock()
        root.destroy.side_effect = RuntimeError("destroy failure")
        with (
            patch(
                "gestureboard.management.commands.validate_windows_mouse.tk.Tk",
                return_value=root,
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.tk.StringVar"
            ),
            patch("gestureboard.management.commands.validate_windows_mouse.tk.Label"),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.tk.Canvas",
                side_effect=RuntimeError("original canvas failure"),
            ),
        ):
            with self.assertRaisesMessage(RuntimeError, "original canvas failure"):
                TkValidationWindow(ValidationRegion(0, 0, 1919, 1079))
        root.destroy.assert_called_once_with()

    def test_window_construction_failure_cleans_acquired_outputs(self):
        _, _, cursor, buttons, _ = runner()
        outputs = NativeOutputs(cursor, buttons)
        with (
            patch(
                "gestureboard.management.commands.validate_windows_mouse.os.name", "nt"
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.sys.stdin.isatty",
                return_value=True,
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.sys.stdout.isatty",
                return_value=True,
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.interactive_desktop_available",
                return_value=True,
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.acquire_native_outputs",
                return_value=outputs,
            ),
            patch(
                "gestureboard.management.commands.validate_windows_mouse.TkValidationWindow",
                side_effect=RuntimeError("partial window construction"),
            ),
        ):
            with self.assertRaisesMessage(RuntimeError, "partial window construction"):
                call_command(
                    "validate_windows_mouse",
                    live=True,
                    acknowledge=ACKNOWLEDGEMENT,
                )
        self.assertEqual(buttons.release_count, 2)
        self.assertEqual((buttons.close_count, cursor.close_count), (1, 1))

    def test_named_mutex_first_acquisition_release_and_fresh_acquisition(self):
        first_api = FakeMutexApi()
        first = WindowsNamedMutex(first_api)
        first.release()
        first.release()
        self.assertEqual(first_api.releases, 1)
        second_api = FakeMutexApi()
        WindowsNamedMutex(second_api).release()
        self.assertEqual(second_api.releases, 1)

    def test_named_mutex_denies_second_process(self):
        api = FakeMutexApi(already_exists=True)
        with self.assertRaisesMessage(RuntimeError, "another Windows process"):
            WindowsNamedMutex(api)
        self.assertEqual(api.releases, 1)

    def test_named_mutex_preserves_denial_when_handle_close_fails(self):
        api = FakeMutexApi(
            already_exists=True, release_error=RuntimeError("CloseHandle failure")
        )
        with self.assertRaisesMessage(RuntimeError, "already owned") as caught:
            WindowsNamedMutex(api)
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertIn("CloseHandle failure", str(caught.exception.__cause__))

    def test_ctypes_mutex_uses_pointer_sized_handle_and_explicit_signatures(self):
        class Function:
            def __init__(self, result):
                self.result = result

            def __call__(self, *args):
                return self.result

        class Kernel:
            CreateMutexW = Function(2**40 + 7)
            CloseHandle = Function(True)

        kernel = Kernel()
        with (
            patch.object(ctypes, "WinDLL", return_value=kernel, create=True),
            patch.object(ctypes, "set_last_error", create=True),
            patch.object(ctypes, "get_last_error", return_value=0, create=True),
        ):
            api = _CtypesNamedMutexApi()
            handle, existed = api.create("mutex")
            api.release(handle)
        self.assertFalse(existed)
        self.assertEqual(handle, 2**40 + 7)
        self.assertEqual(
            ctypes.sizeof(kernel.CreateMutexW.restype), ctypes.sizeof(ctypes.c_void_p)
        )
        self.assertEqual(kernel.CreateMutexW.argtypes[2], ctypes.wintypes.LPCWSTR)
        self.assertEqual(kernel.CloseHandle.argtypes, (ctypes.wintypes.HANDLE,))

    def test_ctypes_mutex_close_failure_reports_last_error(self):
        class Function:
            def __init__(self, result):
                self.result = result

            def __call__(self, *args):
                return self.result

        class Kernel:
            CreateMutexW = Function(123)
            CloseHandle = Function(False)

        with (
            patch.object(ctypes, "WinDLL", return_value=Kernel(), create=True),
            patch.object(ctypes, "set_last_error", create=True),
            patch.object(ctypes, "get_last_error", return_value=6, create=True),
            patch.object(
                ctypes,
                "WinError",
                side_effect=lambda code: OSError(code, "win32 failure"),
                create=True,
            ),
        ):
            api = _CtypesNamedMutexApi()
            with self.assertRaises(OSError):
                api.release(123)

    def test_report_json_uses_explicit_field_allowlists(self):
        top = {
            "schema_version",
            "utc_timestamp",
            "platform",
            "python_version",
            "command_scenario",
            "live",
            "countdown_seconds",
            "scenarios",
            "cancellation_reason",
            "operational_error",
            "cleanup_errors",
            "final_release_status",
            "cursor_restoration_status",
            "overall_pass",
        }
        scenario = {
            "name",
            "status",
            "expected_events",
            "observed_events",
            "expected_point",
            "observed_point",
            "tolerance_px",
            "expected_start",
            "expected_end",
            "observed_start",
            "observed_end",
            "observed_displacement_px",
            "detail",
        }
        point = {"x", "y"}
        payload = build_report(
            "cursor",
            True,
            3,
            (ScenarioResult("cursor", "passed", expected_point=ScreenPoint(1, 2)),),
        ).to_dict()
        self.assertEqual(set(payload), top)
        self.assertEqual(set(payload["scenarios"][0]), scenario)
        self.assertEqual(set(payload["scenarios"][0]["expected_point"]), point)
