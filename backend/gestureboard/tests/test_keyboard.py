"""Tests for the keyboard action models and controller."""

from dataclasses import FrozenInstanceError
from unittest.mock import patch

from django.test import SimpleTestCase

from gestureboard.services.keyboard_controller import (
    KeyboardAction,
    KeyboardActionKind,
    KeyboardController,
    KeyboardControllerError,
)


class FakeKeyboardBackend:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.named_keys = {
            "enter": "<enter>",
            "ctrl": "<ctrl>",
            "shift": "<shift>",
        }
        self.fail_press_on: object | None = None
        self.fail_release_on: object | None = None
        self.fail_type = False
        self.close_count = 0

    def press(self, key: object) -> None:
        self.events.append(("press", key))
        if key == self.fail_press_on:
            raise OSError(f"cannot press {key}")

    def release(self, key: object) -> None:
        self.events.append(("release", key))
        if key == self.fail_release_on:
            raise OSError(f"cannot release {key}")

    def type(self, text: str) -> None:
        self.events.append(("type", text))
        if self.fail_type:
            raise OSError("typing failed")

    def close(self) -> None:
        self.close_count += 1


class KeyboardControllerTests(SimpleTestCase):
    def setUp(self) -> None:
        self.backend = FakeKeyboardBackend()
        self.controller = KeyboardController(self.backend)

    def test_tap_printable_key_presses_before_releasing(self) -> None:
        result = self.controller.tap_key("A")

        self.assertTrue(result.executed)
        self.assertEqual(result.action, KeyboardAction.tap("A"))
        self.assertEqual(self.backend.events, [("press", "a"), ("release", "a")])

    def test_tap_named_key_uses_backend_resolution(self) -> None:
        self.controller.tap_key(" ENTER ")

        self.assertEqual(
            self.backend.events,
            [("press", "<enter>"), ("release", "<enter>")],
        )

    def test_hotkey_press_and_reverse_release_order(self) -> None:
        self.controller.hotkey("ctrl", "shift", "x")

        self.assertEqual(
            self.backend.events,
            [
                ("press", "<ctrl>"),
                ("press", "<shift>"),
                ("press", "x"),
                ("release", "x"),
                ("release", "<shift>"),
                ("release", "<ctrl>"),
            ],
        )

    def test_hotkey_cleans_up_after_partial_press_failure(self) -> None:
        self.backend.fail_press_on = "x"

        with self.assertRaises(KeyboardControllerError) as caught:
            self.controller.hotkey("ctrl", "shift", "x")

        self.assertEqual(
            self.backend.events,
            [
                ("press", "<ctrl>"),
                ("press", "<shift>"),
                ("press", "x"),
                ("release", "<shift>"),
                ("release", "<ctrl>"),
            ],
        )
        self.assertIsInstance(caught.exception.__cause__, OSError)

    def test_types_text_once(self) -> None:
        result = self.controller.type_text("Hello, world!")

        self.assertEqual(self.backend.events, [("type", "Hello, world!")])
        self.assertEqual(result.action.text, "Hello, world!")

    def test_malformed_tap_key_is_rejected(self) -> None:
        for keys, text in (((), None), (("a", "b"), None), (("a",), "text")):
            with self.subTest(keys=keys, text=text):
                with self.assertRaises(KeyboardControllerError):
                    KeyboardAction(KeyboardActionKind.TAP_KEY, keys=keys, text=text)

    def test_malformed_hotkey_is_rejected(self) -> None:
        for keys, text in (((), None), (("ctrl",), None), (("ctrl", "x"), "x")):
            with self.subTest(keys=keys, text=text):
                with self.assertRaises(KeyboardControllerError):
                    KeyboardAction(KeyboardActionKind.HOTKEY, keys=keys, text=text)

    def test_malformed_type_text_is_rejected(self) -> None:
        for keys, text in (((), None), ((), ""), (("a",), "text")):
            with self.subTest(keys=keys, text=text):
                with self.assertRaises(KeyboardControllerError):
                    KeyboardAction(KeyboardActionKind.TYPE_TEXT, keys=keys, text=text)

    def test_empty_and_unsupported_keys_are_rejected(self) -> None:
        with self.assertRaises(KeyboardControllerError):
            KeyboardAction.tap(" ")
        with self.assertRaisesRegex(KeyboardControllerError, "Unsupported"):
            self.controller.tap_key("definitely-not-a-key")
        self.assertEqual(self.backend.events, [])

    def test_backend_error_is_wrapped_and_preserved(self) -> None:
        self.backend.fail_type = True

        with self.assertRaises(KeyboardControllerError) as caught:
            self.controller.type_text("failure")

        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertIn("type text", str(caught.exception))

    def test_action_models_are_immutable(self) -> None:
        action = KeyboardAction.tap("a")
        with self.assertRaises(FrozenInstanceError):
            action.text = "changed"

    def test_close_is_idempotent_and_injected_backend_is_not_closed(self) -> None:
        self.controller.close()
        self.controller.close()

        self.assertEqual(self.backend.close_count, 0)
        with self.assertRaisesRegex(KeyboardControllerError, "closed"):
            self.controller.tap_key("a")

    def test_context_manager_rejects_execution_after_exit(self) -> None:
        with KeyboardController(self.backend) as controller:
            controller.tap_key("a")

        with self.assertRaises(KeyboardControllerError):
            controller.tap_key("b")
        self.assertEqual(self.backend.close_count, 0)

    def test_internally_created_backend_is_closed_once(self) -> None:
        owned_backend = FakeKeyboardBackend()
        with patch(
            "gestureboard.services.keyboard_controller._PynputBackend",
            return_value=owned_backend,
        ):
            controller = KeyboardController()
            controller.tap_key("a")
            controller.close()
            controller.close()

        self.assertEqual(owned_backend.close_count, 1)
