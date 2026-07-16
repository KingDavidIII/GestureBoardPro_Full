from django.test import SimpleTestCase

from gestureboard.mouse.button_output import (
    NullMouseButtonOutput,
    WindowsMouseButtonOutput,
)
from gestureboard.mouse.buttons import MouseButton
from gestureboard.mouse.models import MouseLifecycleError, MouseOutputError


class FakeApi:
    def __init__(self):
        self.calls = []

    def send_input(self, button, is_down):
        self.calls.append((button, is_down))
        return True


class FailingApi(FakeApi):
    def send_input(self, button, is_down):
        super().send_input(button, is_down)
        return False


class OneFailureApi(FakeApi):
    def __init__(self):
        super().__init__()
        self.fail_next_up = True

    def send_input(self, button, is_down):
        super().send_input(button, is_down)
        if not is_down and self.fail_next_up:
            self.fail_next_up = False
            return False
        return True


class SelectiveFailureApi(FakeApi):
    def __init__(self, failures: set[tuple[MouseButton, bool]]) -> None:
        super().__init__()
        self.failures = failures

    def send_input(self, button: MouseButton, is_down: bool) -> bool:
        super().send_input(button, is_down)
        return (button, is_down) not in self.failures


class RaisingApi(FakeApi):
    def __init__(self, *, fail_down: bool = False, fail_up: bool = False) -> None:
        super().__init__()
        self.fail_down = fail_down
        self.fail_up = fail_up

    def send_input(self, button: MouseButton, is_down: bool) -> bool:
        super().send_input(button, is_down)
        if (is_down and self.fail_down) or (not is_down and self.fail_up):
            raise OSError("fake native failure")
        return True


class ButtonOutputTests(SimpleTestCase):
    def test_null_output_tracks_buttons_without_os_input(self) -> None:
        output = NullMouseButtonOutput()
        output.button_down(MouseButton.PRIMARY)
        output.button_down(MouseButton.PRIMARY)
        with self.assertRaises(MouseOutputError):
            output.button_down(MouseButton.SECONDARY)
        output.button_up(MouseButton.PRIMARY)
        output.button_up(MouseButton.PRIMARY)
        output.button_down(MouseButton.SECONDARY)
        output.button_up(MouseButton.SECONDARY)
        output.release_all()
        output.release_all()
        output.close()
        output.close()
        with self.assertRaises(MouseLifecycleError):
            output.button_down(MouseButton.PRIMARY)
        with self.assertRaises(MouseLifecycleError):
            output.button_up(MouseButton.PRIMARY)
        self.assertFalse(hasattr(output, "move"))
        self.assertFalse(hasattr(output, "scroll"))
        self.assertFalse(hasattr(output, "key_down"))

    def test_fake_windows_output_is_edge_triggered_and_releases_on_close(self) -> None:
        api = FakeApi()
        output = WindowsMouseButtonOutput(api, platform_name="nt")
        output.button_down(MouseButton.PRIMARY)
        output.button_down(MouseButton.PRIMARY)
        output.close()
        output.close()
        self.assertEqual(
            api.calls, [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)]
        )

    def test_primary_and_secondary_edges_map_to_fake_native_operations(self) -> None:
        api = FakeApi()
        output = WindowsMouseButtonOutput(api, platform_name="nt")

        output.button_down(MouseButton.PRIMARY)
        output.button_down(MouseButton.PRIMARY)
        output.button_up(MouseButton.PRIMARY)
        output.button_up(MouseButton.PRIMARY)
        output.button_down(MouseButton.SECONDARY)
        output.button_up(MouseButton.SECONDARY)

        self.assertEqual(
            api.calls,
            [
                (MouseButton.PRIMARY, True),
                (MouseButton.PRIMARY, False),
                (MouseButton.SECONDARY, True),
                (MouseButton.SECONDARY, False),
            ],
        )

    def test_conflicting_button_and_native_failure_are_rejected(self) -> None:
        api = FakeApi()
        output = WindowsMouseButtonOutput(api, platform_name="nt")
        output.button_down(MouseButton.PRIMARY)
        with self.assertRaises(MouseOutputError):
            output.button_down(MouseButton.SECONDARY)
        output.button_up(MouseButton.SECONDARY)
        self.assertEqual(api.calls, [(MouseButton.PRIMARY, True)])
        failed = WindowsMouseButtonOutput(FailingApi(), platform_name="nt")
        with self.assertRaises(MouseOutputError):
            failed.button_down(MouseButton.PRIMARY)

    def test_secondary_mapping_and_failed_up_are_retryable(self) -> None:
        api = OneFailureApi()
        output = WindowsMouseButtonOutput(api, platform_name="nt")
        output.button_down(MouseButton.SECONDARY)
        with self.assertRaises(MouseOutputError):
            output.button_up(MouseButton.SECONDARY)
        output.button_up(MouseButton.SECONDARY)
        output.button_up(MouseButton.SECONDARY)
        self.assertEqual(
            api.calls,
            [
                (MouseButton.SECONDARY, True),
                (MouseButton.SECONDARY, False),
                (MouseButton.SECONDARY, False),
            ],
        )

    def test_failed_down_does_not_leave_a_logical_button_held(self) -> None:
        failed = WindowsMouseButtonOutput(FailingApi(), platform_name="nt")

        with self.assertRaises(MouseOutputError):
            failed.button_down(MouseButton.PRIMARY)
        self.assertEqual(failed._held, set())

    def test_release_all_is_harmless_and_idempotent_when_nothing_is_held(self) -> None:
        api = FakeApi()
        output = WindowsMouseButtonOutput(api, platform_name="nt")

        output.release_all()
        output.release_all()

        self.assertEqual(api.calls, [])

    def test_release_all_attempts_each_internal_held_button_after_a_failure(
        self,
    ) -> None:
        api = SelectiveFailureApi({(MouseButton.PRIMARY, False)})
        output = WindowsMouseButtonOutput(api, platform_name="nt")
        # Mutual exclusion is enforced by the public API.  This seam models
        # recovery of an interrupted native lifecycle with two logical holds.
        output._held.update({MouseButton.PRIMARY, MouseButton.SECONDARY})

        with self.assertRaises(MouseOutputError):
            output.release_all()

        self.assertCountEqual(
            api.calls,
            [(MouseButton.PRIMARY, False), (MouseButton.SECONDARY, False)],
        )
        self.assertEqual(output._held, {MouseButton.PRIMARY})

        api.failures.clear()
        output.release_all()
        self.assertEqual(output._held, set())

    def test_close_failure_preserves_held_state_for_a_retry(self) -> None:
        api = OneFailureApi()
        output = WindowsMouseButtonOutput(api, platform_name="nt")
        output.button_down(MouseButton.PRIMARY)

        with self.assertRaises(MouseOutputError):
            output.close()

        self.assertFalse(output._closed)
        self.assertEqual(output._held, {MouseButton.PRIMARY})
        output.close()
        self.assertTrue(output._closed)
        self.assertEqual(output._held, set())

    def test_closed_output_never_invokes_fake_native_api(self) -> None:
        api = FakeApi()
        output = WindowsMouseButtonOutput(api, platform_name="nt")
        output.close()

        with self.assertRaises(MouseLifecycleError):
            output.button_down(MouseButton.PRIMARY)
        with self.assertRaises(MouseLifecycleError):
            output.button_up(MouseButton.PRIMARY)
        with self.assertRaises(MouseLifecycleError):
            output.release_all()
        self.assertEqual(api.calls, [])

    def test_platform_rejection_uses_no_native_input(self) -> None:
        with self.assertRaises(MouseOutputError):
            WindowsMouseButtonOutput(FakeApi(), platform_name="posix")

    def test_native_exceptions_are_normalised_and_held_state_is_retryable(self) -> None:
        down_api = RaisingApi(fail_down=True)
        down = WindowsMouseButtonOutput(down_api, platform_name="nt")
        with self.assertRaises(MouseOutputError) as raised:
            down.button_down(MouseButton.PRIMARY)
        self.assertIsInstance(raised.exception.__cause__, OSError)

        up_api = RaisingApi(fail_up=True)
        up = WindowsMouseButtonOutput(up_api, platform_name="nt")
        up.button_down(MouseButton.PRIMARY)
        with self.assertRaises(MouseOutputError) as raised:
            up.button_up(MouseButton.PRIMARY)
        self.assertIsInstance(raised.exception.__cause__, OSError)
        up_api.fail_up = False
        up.button_up(MouseButton.PRIMARY)
