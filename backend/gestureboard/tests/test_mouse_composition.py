from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from gestureboard.mouse.button_output import (
    NullMouseButtonOutput,
    WindowsMouseButtonOutput,
)
from gestureboard.mouse.buttons import MouseButton, MouseButtonIntent, MouseButtonPolicy
from gestureboard.mouse.composition import build_mouse_runtime_dependencies
from gestureboard.mouse.config import (
    GestureMouseOutputMode,
    GestureMouseRuntimeConfig,
    MouseButtonOutputMode,
)
from gestureboard.mouse.models import MouseOutputError
from gestureboard.mouse.output import NullVirtualCursorOutput, WindowsCursorOutput
from gestureboard.mouse.ownership import WindowsCursorOwnershipLease
from gestureboard.mouse.runtime import GestureMouseRuntimeCoordinator
from gestureboard.recognition.models import GestureId
from gestureboard.recognition.observations import (
    Handedness,
    HandObservation,
    HandSelection,
    Landmark3D,
)


class FakeCursorApi:
    def __init__(self) -> None:
        self.metric_calls: list[int] = []
        self.moves: list[tuple[int, int]] = []

    def get_system_metrics(self, metric: int) -> int:
        self.metric_calls.append(metric)
        return (0, 0, 100, 100)[len(self.metric_calls) - 1]

    def set_cursor_pos(self, x: int, y: int) -> bool:
        self.moves.append((x, y))
        return True


class FakeButtonApi:
    def __init__(self) -> None:
        self.calls: list[tuple[MouseButton, bool]] = []

    def send_input(self, button: MouseButton, is_down: bool) -> bool:
        self.calls.append((button, is_down))
        return True


class FalsyCursorApi(FakeCursorApi):
    def __bool__(self) -> bool:
        return False


class FalsyButtonApi(FakeButtonApi):
    def __bool__(self) -> bool:
        return False


class FakeNamedMutexApi:
    def create(self, name):
        return object(), False

    def release(self, handle):
        return None


class FakeNamedMutex:
    instances = []

    def __init__(self):
        self.released = False
        self.instances.append(self)

    def release(self):
        self.released = True


def selected_hand(x: float = 0.25) -> HandSelection:
    points = [Landmark3D(0, 0, 0) for _ in range(21)]
    points[8] = Landmark3D(x, 0.75, 0)
    return HandSelection(
        1, HandObservation(tuple(points), 0, Handedness.RIGHT, 1, None, 1, 1)
    )


class MouseCompositionTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = patch(
            "gestureboard.mouse.ownership._CtypesNamedMutexApi", FakeNamedMutexApi
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_composed_drag_moves_after_primary_down(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        cursor, buttons, lease = (
            FakeCursorApi(),
            FakeButtonApi(),
            WindowsCursorOwnershipLease(),
        )
        built = build_mouse_runtime_dependencies(
            GestureMouseRuntimeConfig(
                enabled=True,
                output_mode=GestureMouseOutputMode.WINDOWS,
                button_policy=policy,
                button_output_mode=MouseButtonOutputMode.WINDOWS,
            ),
            owner_id="down",
            cursor_api=cursor,
            button_api=buttons,
            ownership_lease=lease,
            platform_name="nt",
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            built.coordinator.process(
                selected_hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
            )
            built.coordinator.process(
                selected_hand(0.4),
                timestamp_ms=policy.drag_hold_ms,
                stable_gesture=GestureId.POINT,
            )
            moves = len(cursor.moves)
            built.coordinator.process(
                selected_hand(0.7),
                timestamp_ms=policy.drag_hold_ms + 20,
                stable_gesture=GestureId.POINT,
            )
        self.assertEqual(buttons.calls, [(MouseButton.PRIMARY, True)])
        self.assertGreater(len(cursor.moves), moves)
        built.coordinator.tracking_lost()
        self.assertEqual(
            buttons.calls,
            [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)],
        )
        self.assertIsNone(lease.owner_id)
        built.coordinator.close()
        self.assertEqual(
            buttons.calls,
            [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)],
        )

    def test_two_composed_coordinators_deny_then_transfer_ownership(self) -> None:
        lease = WindowsCursorOwnershipLease()
        policy = MouseButtonPolicy(buttons_enabled=True)
        config = GestureMouseRuntimeConfig(
            enabled=True,
            output_mode=GestureMouseOutputMode.WINDOWS,
            button_policy=policy,
            button_output_mode=MouseButtonOutputMode.WINDOWS,
        )
        first_cursor, first_button = FakeCursorApi(), FakeButtonApi()
        first = build_mouse_runtime_dependencies(
            config,
            owner_id="a",
            cursor_api=first_cursor,
            button_api=first_button,
            ownership_lease=lease,
            platform_name="nt",
        )
        second_api = FakeCursorApi()
        second_button = FakeButtonApi()
        second = build_mouse_runtime_dependencies(
            config,
            owner_id="b",
            cursor_api=second_api,
            button_api=second_button,
            ownership_lease=lease,
            platform_name="nt",
        )
        first.coordinator.process(
            selected_hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            denied_results = [
                second.coordinator.process(
                    selected_hand(), timestamp_ms=stamp, stable_gesture=GestureId.POINT
                )
                for stamp in (
                    0,
                    policy.intent_activation_ms,
                    policy.intent_activation_ms + 1,
                )
            ]
        for denied in denied_results:
            self.assertFalse(denied.moved)
            self.assertIsNone(denied.button_decision)
        self.assertEqual(second_api.moves, [])
        self.assertEqual(second_button.calls, [])
        first.coordinator.tracking_lost()
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            second.coordinator.process(
                selected_hand(), timestamp_ms=200, stable_gesture=GestureId.POINT
            )
            second.coordinator.process(
                selected_hand(),
                timestamp_ms=200 + policy.intent_activation_ms,
                stable_gesture=GestureId.POINT,
            )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.NONE,
        ):
            second.coordinator.process(
                selected_hand(),
                timestamp_ms=201 + policy.intent_activation_ms,
                stable_gesture=GestureId.POINT,
            )
            accepted = second.coordinator.process(
                selected_hand(),
                timestamp_ms=201
                + policy.intent_activation_ms
                + policy.intent_release_ms,
                stable_gesture=GestureId.POINT,
            )
        self.assertIsNotNone(accepted.button_decision)
        self.assertGreater(len(second_api.moves), 0)
        self.assertEqual(
            second_button.calls,
            [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)],
        )
        self.assertIs(first.ownership_lease, lease)
        self.assertIs(second.ownership_lease, lease)

    def test_production_composition_configures_mutex_idempotently(self) -> None:
        FakeNamedMutex.instances.clear()
        lease = WindowsCursorOwnershipLease()
        config = GestureMouseRuntimeConfig(
            enabled=True,
            output_mode=GestureMouseOutputMode.WINDOWS,
        )
        first_api, second_api = FakeCursorApi(), FakeCursorApi()
        first = build_mouse_runtime_dependencies(
            config,
            owner_id="first",
            cursor_api=first_api,
            ownership_lease=lease,
            platform_name="nt",
            mutex_factory=FakeNamedMutex,
        )
        first.coordinator.process(
            selected_hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
        )
        second = build_mouse_runtime_dependencies(
            config,
            owner_id="second",
            cursor_api=second_api,
            ownership_lease=lease,
            platform_name="nt",
            mutex_factory=FakeNamedMutex,
        )
        denied = second.coordinator.process(
            selected_hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
        )
        self.assertFalse(denied.moved)
        self.assertEqual(second_api.moves, [])
        first.coordinator.tracking_lost()
        accepted = second.coordinator.process(
            selected_hand(), timestamp_ms=1, stable_gesture=GestureId.POINT
        )
        self.assertTrue(accepted.moved)
        self.assertEqual(len(FakeNamedMutex.instances), 2)
        self.assertTrue(FakeNamedMutex.instances[0].released)

    def test_button_failure_releases_lease_for_second_composed_coordinator(
        self,
    ) -> None:
        lease = WindowsCursorOwnershipLease()
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)

        class FailingOutput:
            def __init__(self) -> None:
                self.button_down_calls = self.release_calls = self.close_calls = 0

            def button_down(self, button: MouseButton) -> None:
                self.button_down_calls += 1
                raise MouseOutputError("distinct primary action failure")

            def button_up(self, button: MouseButton) -> None:
                del button

            def release_all(self) -> None:
                self.release_calls += 1
                raise MouseOutputError("distinct cleanup failure")

            def close(self) -> None:
                self.close_calls += 1

        failing_output = FailingOutput()
        config = GestureMouseRuntimeConfig(
            enabled=True,
            button_policy=policy,
            button_output_mode=MouseButtonOutputMode.WINDOWS,
        )
        built = build_mouse_runtime_dependencies(
            config,
            owner_id="failure",
            button_api=FakeButtonApi(),
            ownership_lease=lease,
            platform_name="nt",
            button_output_factory=lambda *args, **kwargs: failing_output,
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            built.coordinator.process(
                selected_hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
            )
            self.assertEqual(lease.owner_id, "failure")
            with self.assertRaisesRegex(
                MouseOutputError, "distinct primary action failure"
            ):
                built.coordinator.process(
                    selected_hand(), timestamp_ms=500, stable_gesture=GestureId.POINT
                )
        self.assertEqual(failing_output.button_down_calls, 1)
        self.assertEqual(failing_output.release_calls, 1)
        self.assertFalse(built.coordinator.enabled)
        self.assertIsNone(lease.owner_id)
        successful = FakeButtonApi()
        second = build_mouse_runtime_dependencies(
            config,
            owner_id="second",
            button_api=successful,
            ownership_lease=lease,
            platform_name="nt",
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            second.coordinator.process(
                selected_hand(), timestamp_ms=600, stable_gesture=GestureId.POINT
            )
            second.coordinator.process(
                selected_hand(),
                timestamp_ms=600 + policy.drag_hold_ms,
                stable_gesture=GestureId.POINT,
            )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.NONE,
        ):
            second.coordinator.process(
                selected_hand(),
                timestamp_ms=601 + policy.drag_hold_ms,
                stable_gesture=GestureId.POINT,
            )
            second.coordinator.process(
                selected_hand(),
                timestamp_ms=601 + policy.drag_hold_ms + policy.intent_release_ms,
                stable_gesture=GestureId.POINT,
            )
        self.assertEqual(
            successful.calls,
            [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)],
        )
        second.coordinator.tracking_lost()
        self.assertIsNone(lease.owner_id)

    def test_factory_closes_cursor_when_button_construction_fails(self) -> None:
        calls = []

        class Cursor:
            def close(self):
                calls.append("cursor")

        def fail(*args, **kwargs):
            raise MouseOutputError("button build")

        config = GestureMouseRuntimeConfig(
            enabled=True,
            output_mode=GestureMouseOutputMode.WINDOWS,
            button_policy=MouseButtonPolicy(buttons_enabled=True),
            button_output_mode=MouseButtonOutputMode.WINDOWS,
        )
        with self.assertRaisesRegex(MouseOutputError, "button build"):
            build_mouse_runtime_dependencies(
                config,
                owner_id="x",
                cursor_api=FakeCursorApi(),
                button_api=FakeButtonApi(),
                ownership_lease=WindowsCursorOwnershipLease(),
                platform_name="nt",
                cursor_output_factory=lambda *args, **kwargs: Cursor(),
                button_output_factory=fail,
            )
        self.assertEqual(calls, ["cursor"])

    def test_factory_closes_both_outputs_when_coordinator_construction_fails(
        self,
    ) -> None:
        calls = []

        class Output:
            def release_all(self):
                calls.append("release")

            def close(self):
                calls.append("close")

        config = GestureMouseRuntimeConfig(
            enabled=True,
            output_mode=GestureMouseOutputMode.WINDOWS,
            button_policy=MouseButtonPolicy(buttons_enabled=True),
            button_output_mode=MouseButtonOutputMode.WINDOWS,
        )
        with self.assertRaisesRegex(MouseOutputError, "coordinator build"):
            build_mouse_runtime_dependencies(
                config,
                owner_id="x",
                cursor_api=FakeCursorApi(),
                button_api=FakeButtonApi(),
                ownership_lease=WindowsCursorOwnershipLease(),
                platform_name="nt",
                cursor_output_factory=lambda *args, **kwargs: Output(),
                button_output_factory=lambda *args, **kwargs: Output(),
                coordinator_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
                    MouseOutputError("coordinator build")
                ),
            )
        self.assertGreaterEqual(calls.count("close"), 2)

    def test_factory_cleanup_failure_preserves_construction_error(self) -> None:
        calls = []

        class Output:
            def release_all(self):
                calls.append("release")

            def close(self):
                calls.append("close")
                raise MouseOutputError("cleanup")

        config = GestureMouseRuntimeConfig(
            enabled=True,
            output_mode=GestureMouseOutputMode.WINDOWS,
            button_policy=MouseButtonPolicy(buttons_enabled=True),
            button_output_mode=MouseButtonOutputMode.WINDOWS,
        )
        with self.assertRaisesRegex(MouseOutputError, "coordinator build"):
            build_mouse_runtime_dependencies(
                config,
                owner_id="x",
                cursor_api=FakeCursorApi(),
                button_api=FakeButtonApi(),
                ownership_lease=WindowsCursorOwnershipLease(),
                platform_name="nt",
                cursor_output_factory=lambda *args, **kwargs: Output(),
                button_output_factory=lambda *args, **kwargs: Output(),
                coordinator_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
                    MouseOutputError("coordinator build")
                ),
            )
        self.assertGreaterEqual(calls.count("close"), 2)

    def test_composed_primary_click_uses_native_api_once(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True)
        api = FakeButtonApi()
        lease = WindowsCursorOwnershipLease()
        dependencies = build_mouse_runtime_dependencies(
            GestureMouseRuntimeConfig(
                enabled=True,
                button_policy=policy,
                button_output_mode=MouseButtonOutputMode.WINDOWS,
            ),
            owner_id="click",
            button_api=api,
            ownership_lease=lease,
            platform_name="nt",
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            dependencies.coordinator.process(
                selected_hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
            )
            dependencies.coordinator.process(
                selected_hand(),
                timestamp_ms=policy.intent_activation_ms,
                stable_gesture=GestureId.POINT,
            )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.NONE,
        ):
            dependencies.coordinator.process(
                selected_hand(),
                timestamp_ms=policy.intent_activation_ms + 1,
                stable_gesture=GestureId.POINT,
            )
            dependencies.coordinator.process(
                selected_hand(),
                timestamp_ms=policy.intent_activation_ms + 1 + policy.intent_release_ms,
                stable_gesture=GestureId.POINT,
            )
        self.assertEqual(
            api.calls, [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)]
        )
        dependencies.coordinator.tracking_lost()
        self.assertIsNone(lease.owner_id)
        dependencies.coordinator.close()
        self.assertEqual(len(api.calls), 2)

    def test_composed_drag_moves_cursor_and_releases_once(self) -> None:
        policy = MouseButtonPolicy(buttons_enabled=True, drag_enabled=True)
        cursor_api, button_api, lease = (
            FakeCursorApi(),
            FakeButtonApi(),
            WindowsCursorOwnershipLease(),
        )
        dependencies = build_mouse_runtime_dependencies(
            GestureMouseRuntimeConfig(
                enabled=True,
                output_mode=GestureMouseOutputMode.WINDOWS,
                button_policy=policy,
                button_output_mode=MouseButtonOutputMode.WINDOWS,
            ),
            owner_id="drag",
            cursor_api=cursor_api,
            button_api=button_api,
            ownership_lease=lease,
            platform_name="nt",
        )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.PRIMARY_CONTACT,
        ):
            dependencies.coordinator.process(
                selected_hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
            )
            dependencies.coordinator.process(
                selected_hand(0.4),
                timestamp_ms=policy.drag_hold_ms,
                stable_gesture=GestureId.POINT,
            )
            dependencies.coordinator.process(
                selected_hand(0.6),
                timestamp_ms=policy.drag_hold_ms + 1,
                stable_gesture=GestureId.POINT,
            )
        with patch(
            "gestureboard.mouse.runtime.detect_button_intent",
            return_value=MouseButtonIntent.NONE,
        ):
            dependencies.coordinator.process(
                selected_hand(),
                timestamp_ms=policy.drag_hold_ms + 2,
                stable_gesture=GestureId.POINT,
            )
            dependencies.coordinator.process(
                selected_hand(),
                timestamp_ms=policy.drag_hold_ms + 2 + policy.intent_release_ms,
                stable_gesture=GestureId.POINT,
            )
        self.assertEqual(
            button_api.calls,
            [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)],
        )
        self.assertGreaterEqual(len(cursor_api.moves), 2)
        dependencies.coordinator.tracking_lost()
        self.assertIsNone(lease.owner_id)
        dependencies.coordinator.close()
        self.assertEqual(len(button_api.calls), 2)

    def test_defaults_construct_null_outputs_without_native_boundaries(self) -> None:
        dependencies = build_mouse_runtime_dependencies(
            GestureMouseRuntimeConfig(), owner_id="defaults"
        )
        self.assertIsInstance(dependencies.cursor_output, NullVirtualCursorOutput)
        self.assertIsInstance(dependencies.button_output, NullMouseButtonOutput)
        self.assertFalse(dependencies.coordinator.enabled)
        dependencies.coordinator.close()
        dependencies.coordinator.close()

    def test_null_button_output_is_kept_when_buttons_are_enabled(self) -> None:
        config = GestureMouseRuntimeConfig(
            enabled=True,
            button_policy=MouseButtonPolicy(buttons_enabled=True),
        )
        dependencies = build_mouse_runtime_dependencies(config, owner_id="null")
        self.assertIsInstance(dependencies.button_output, NullMouseButtonOutput)
        self.assertTrue(dependencies.coordinator._button_policy.buttons_enabled)
        dependencies.coordinator.close()

    def test_windows_outputs_use_injected_boundaries_and_shared_lease(self) -> None:
        cursor_api = FakeCursorApi()
        button_api = FakeButtonApi()
        lease = WindowsCursorOwnershipLease()
        config = GestureMouseRuntimeConfig(
            enabled=True,
            output_mode=GestureMouseOutputMode.WINDOWS,
            button_policy=MouseButtonPolicy(buttons_enabled=True),
            button_output_mode=MouseButtonOutputMode.WINDOWS,
        )
        dependencies = build_mouse_runtime_dependencies(
            config,
            owner_id="windows",
            cursor_api=cursor_api,
            button_api=button_api,
            ownership_lease=lease,
            platform_name="nt",
        )
        self.assertIsInstance(dependencies.cursor_output, WindowsCursorOutput)
        self.assertIsInstance(dependencies.button_output, WindowsMouseButtonOutput)
        self.assertIs(dependencies.ownership_lease, lease)
        dependencies.button_output.button_down(MouseButton.PRIMARY)
        dependencies.button_output.button_up(MouseButton.PRIMARY)
        self.assertEqual(
            button_api.calls,
            [(MouseButton.PRIMARY, True), (MouseButton.PRIMARY, False)],
        )
        dependencies.coordinator.close()

    def test_active_windows_output_requires_an_explicit_shared_lease(self) -> None:
        config = GestureMouseRuntimeConfig(
            enabled=True,
            output_mode=GestureMouseOutputMode.WINDOWS,
        )
        with self.assertRaisesRegex(MouseOutputError, "shared ownership lease"):
            build_mouse_runtime_dependencies(
                config,
                owner_id="no-lease",
                cursor_api=FakeCursorApi(),
                platform_name="nt",
            )

    def test_injected_active_windows_output_is_safe_on_non_windows(self) -> None:
        api = FakeCursorApi()
        lease = WindowsCursorOwnershipLease()
        dependencies = build_mouse_runtime_dependencies(
            GestureMouseRuntimeConfig(
                enabled=True,
                output_mode=GestureMouseOutputMode.WINDOWS,
            ),
            owner_id="injected-posix",
            cursor_api=api,
            ownership_lease=lease,
            platform_name="posix",
        )

        result = dependencies.coordinator.process(
            selected_hand(), timestamp_ms=0, stable_gesture=GestureId.POINT
        )

        self.assertTrue(result.moved)
        self.assertTrue(api.moves)
        self.assertEqual(lease.owner_id, "injected-posix")
        dependencies.coordinator.close()
        self.assertIsNone(lease.owner_id)

    def test_button_only_windows_output_forwards_the_exact_lease(self) -> None:
        lease = WindowsCursorOwnershipLease()
        captured: dict[str, object] = {}

        def recording_factory(*args: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return GestureMouseRuntimeCoordinator(*args, **kwargs)

        dependencies = build_mouse_runtime_dependencies(
            GestureMouseRuntimeConfig(
                enabled=True,
                button_policy=MouseButtonPolicy(buttons_enabled=True),
                button_output_mode=MouseButtonOutputMode.WINDOWS,
            ),
            owner_id="button-only",
            button_api=FakeButtonApi(),
            ownership_lease=lease,
            platform_name="nt",
            coordinator_factory=recording_factory,
        )
        self.assertIs(dependencies.ownership_lease, lease)
        self.assertIs(captured["windows_lease"], lease)
        dependencies.coordinator.close()

    def test_falsy_native_apis_are_retained_without_factory_replacement(self) -> None:
        cursor_api = FalsyCursorApi()
        button_api = FalsyButtonApi()
        dependencies = build_mouse_runtime_dependencies(
            GestureMouseRuntimeConfig(
                enabled=True,
                output_mode=GestureMouseOutputMode.WINDOWS,
                button_policy=MouseButtonPolicy(buttons_enabled=True),
                button_output_mode=MouseButtonOutputMode.WINDOWS,
            ),
            owner_id="falsy",
            cursor_api=cursor_api,
            button_api=button_api,
            ownership_lease=WindowsCursorOwnershipLease(),
            platform_name="nt",
        )
        self.assertIs(dependencies.cursor_output._api, cursor_api)
        self.assertIs(dependencies.button_output._api, button_api)
        dependencies.coordinator.close()

    def test_globally_disabled_buttons_remain_null_without_a_lease(self) -> None:
        dependencies = build_mouse_runtime_dependencies(
            GestureMouseRuntimeConfig(
                button_policy=MouseButtonPolicy(buttons_enabled=True),
                button_output_mode=MouseButtonOutputMode.WINDOWS,
            ),
            owner_id="disabled-buttons",
            platform_name="nt",
        )
        self.assertIsInstance(dependencies.button_output, NullMouseButtonOutput)
        self.assertIsNone(dependencies.ownership_lease)
        dependencies.coordinator.close()

    def test_unsupported_platform_does_not_call_native_factories(self) -> None:
        calls: list[str] = []
        config = GestureMouseRuntimeConfig(
            output_mode=GestureMouseOutputMode.WINDOWS,
            button_output_mode=MouseButtonOutputMode.WINDOWS,
        )

        def unexpected(*args: object, **kwargs: object) -> object:
            del args, kwargs
            calls.append("called")
            raise AssertionError("factory must not run")

        with self.assertRaises(MouseOutputError):
            build_mouse_runtime_dependencies(
                config,
                owner_id="platform",
                platform_name="posix",
                coordinator_factory=unexpected,
            )
        self.assertEqual(calls, [])

    def test_explicit_windows_button_mode_fails_before_processing_on_non_windows(
        self,
    ) -> None:
        config = GestureMouseRuntimeConfig(
            button_policy=MouseButtonPolicy(buttons_enabled=True),
            button_output_mode=MouseButtonOutputMode.WINDOWS,
        )
        with self.assertRaises(MouseOutputError):
            build_mouse_runtime_dependencies(
                config,
                owner_id="unsupported",
                button_api=FakeButtonApi(),
                platform_name="posix",
            )

    def test_partial_coordinator_construction_closes_outputs(self) -> None:
        captured: dict[str, object] = {}

        def failing_factory(*args: object, **kwargs: object) -> object:
            del args
            captured.update(kwargs)
            raise MouseOutputError("coordinator construction failed")

        config = GestureMouseRuntimeConfig(
            button_policy=MouseButtonPolicy(buttons_enabled=True)
        )
        with self.assertRaisesRegex(
            MouseOutputError, "coordinator construction failed"
        ):
            build_mouse_runtime_dependencies(
                config,
                owner_id="failure",
                coordinator_factory=failing_factory,
            )
        self.assertTrue(captured["button_output"]._closed)
        self.assertTrue(captured["output"]._closed)
