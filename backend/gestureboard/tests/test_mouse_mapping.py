from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from math import inf, nan
from threading import Barrier
from unittest import TestCase

from gestureboard.mouse import (
    ActiveCameraRegion,
    CursorTarget,
    MouseValidationError,
    VirtualCursorMapper,
    VirtualCursorMappingPolicy,
    VirtualCursorReason,
    VirtualSurface,
)


def target(x: float, y: float, timestamp: int = 1, source: int = 0) -> CursorTarget:
    return CursorTarget(x, y, timestamp, source)


class VirtualCursorModelTests(TestCase):
    def test_surface_region_and_policy_validate_and_are_immutable(self) -> None:
        surface = VirtualSurface(1, 2)
        region = ActiveCameraRegion(0.1, 0.2, 0.9, 0.8)
        policy = VirtualCursorMappingPolicy(region, True, False, 0.5, 0.1)
        self.assertEqual(
            (surface.width_px, surface.height_px, policy.mirror_x), (1, 2, True)
        )
        with self.assertRaises(FrozenInstanceError):
            surface.width_px = 4  # type: ignore[misc]
        for dimensions in ((0, 1), (-1, 1), (True, 1), (1, False)):
            with self.assertRaises(MouseValidationError):
                VirtualSurface(*dimensions)
        for values in (
            (0.5, 0.0, 0.5, 1.0),
            (0.0, 1.0, 1.0, 1.0),
            (nan, 0.0, 1.0, 1.0),
            (0.0, 0.0, inf, 1.0),
        ):
            with self.assertRaises(MouseValidationError):
                ActiveCameraRegion(*values)
        for alpha, radius in (
            (0.0, 0.0),
            (1.1, 0.0),
            (True, 0.0),
            (1.0, -0.1),
            (1.0, 1.1),
            (1.0, False),
        ):
            with self.assertRaises(MouseValidationError):
                VirtualCursorMappingPolicy(
                    smoothing_alpha=alpha, dead_zone_radius=radius
                )


class VirtualCursorMappingTests(TestCase):
    def test_full_frame_corners_centre_rounding_and_pixel_bounds(self) -> None:
        mapper = VirtualCursorMapper(VirtualSurface(101, 51))
        corners = (
            (target(0, 0, 1), (0, 0)),
            (target(1, 1, 2), (100, 50)),
            (target(0.5, 0.5, 3), (50, 25)),
        )
        for sample, pixels in corners:
            result = mapper.map(sample)
            self.assertEqual(
                (result.virtual_target.x_px, result.virtual_target.y_px), pixels
            )
            self.assertTrue(0 <= result.virtual_target.x_px < 101)
            self.assertTrue(0 <= result.virtual_target.y_px < 51)

    def test_active_region_clamps_and_mirroring_applies_after_normalisation(
        self,
    ) -> None:
        region = ActiveCameraRegion(0.25, 0.25, 0.75, 0.75)
        mapper = VirtualCursorMapper(
            VirtualSurface(11, 11), VirtualCursorMappingPolicy(region)
        )
        self.assertEqual(mapper.map(target(0.25, 0.75, 1)).virtual_target.x_px, 0)
        self.assertEqual(mapper.map(target(0.75, 0.25, 2)).virtual_target.y_px, 0)
        self.assertEqual(mapper.map(target(0.0, 1.0, 3)).virtual_target.x_px, 0)
        mirrored = VirtualCursorMapper(
            VirtualSurface(11, 11), VirtualCursorMappingPolicy(region, True, True)
        )
        result = mirrored.map(target(0.25, 0.75, 1)).virtual_target
        self.assertEqual((result.x_px, result.y_px), (10, 0))

    def test_one_pixel_surface_and_alpha_one_preserve_exact_mapping(self) -> None:
        mapper = VirtualCursorMapper(VirtualSurface(1, 1))
        result = mapper.map(target(0.9, 0.1))
        self.assertEqual(
            (result.virtual_target.x_px, result.virtual_target.y_px), (0, 0)
        )
        exact = VirtualCursorMapper(VirtualSurface(101, 101))
        self.assertEqual(exact.map(target(0.3, 0.7)).virtual_target.x_normalised, 0.3)

    def test_smoothing_dead_zone_and_timestamp_contract(self) -> None:
        mapper = VirtualCursorMapper(
            VirtualSurface(101, 101),
            VirtualCursorMappingPolicy(smoothing_alpha=0.5, dead_zone_radius=0.1),
        )
        first = mapper.map(target(0.0, 0.0, 1))
        suppressed = mapper.map(target(0.2, 0.0, 2))
        self.assertAlmostEqual(mapper.snapshot().smoothed_normalised[0], 0.1)
        emitted = mapper.map(target(0.6, 0.0, 3))
        equal = mapper.map(target(0.6, 0.0, 3))
        stale = mapper.map(target(1.0, 1.0, 2))
        self.assertTrue(first.emitted)
        self.assertEqual(suppressed.reason, VirtualCursorReason.DEAD_ZONE_SUPPRESSED)
        self.assertFalse(suppressed.emitted)
        self.assertEqual(suppressed.retained_target, first.virtual_target)
        self.assertTrue(emitted.emitted)
        self.assertTrue(equal.accepted)
        self.assertEqual(stale.reason, VirtualCursorReason.STALE_TIMESTAMP)

    def test_reset_and_source_change_clear_history_before_new_first_sample(
        self,
    ) -> None:
        mapper = VirtualCursorMapper(
            VirtualSurface(101, 101), VirtualCursorMappingPolicy(smoothing_alpha=0.5)
        )
        mapper.map(target(0.0, 0.0, 10, 0))
        changed = mapper.map(target(1.0, 1.0, 1, 1))
        self.assertTrue(changed.source_reset)
        self.assertEqual(
            (changed.virtual_target.x_normalised, changed.virtual_target.y_normalised),
            (1.0, 1.0),
        )
        reset = mapper.reset()
        self.assertEqual(reset.reason, VirtualCursorReason.RESET)
        self.assertEqual(mapper.snapshot().reset_generation, 1)
        post_reset = mapper.map(target(0.2, 0.2, 0, 0))
        self.assertTrue(post_reset.emitted)

    def test_map_and_reset_are_atomic_under_concurrency(self) -> None:
        mapper = VirtualCursorMapper(VirtualSurface(100, 100))
        barrier = Barrier(3)

        def map_sample() -> None:
            barrier.wait()
            mapper.map(target(0.3, 0.4, 1, 1))

        def reset() -> None:
            barrier.wait()
            mapper.reset()

        with ThreadPoolExecutor(max_workers=2) as executor:
            mapping = executor.submit(map_sample)
            resetting = executor.submit(reset)
            barrier.wait()
            mapping.result()
            resetting.result()
        snapshot = mapper.snapshot()
        self.assertTrue(snapshot.current_target is None or snapshot.source_index == 1)
