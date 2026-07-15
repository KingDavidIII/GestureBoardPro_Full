from __future__ import annotations

from dataclasses import dataclass
from math import inf, nan
from unittest import TestCase

from gestureboard.recognition.observations import (
    Handedness,
    HandObservation,
    Landmark3D,
    adapt_hands,
    select_primary,
)
from gestureboard.recognition.service import RecognitionService
from gestureboard.recognition.task_engine import (
    GestureTaskResult,
    MediaPipeGestureTaskError,
    classify_task_hand,
)


@dataclass(frozen=True)
class FakeCategory:
    category_name: str
    score: float
    index: int = 0
    display_name: str = ""


@dataclass(frozen=True)
class FakeLandmark:
    x: float
    y: float
    z: float
    visibility: float = 1.0
    presence: float = 1.0


def hand(value: float = 0.1) -> list[FakeLandmark]:
    return [FakeLandmark(value + index * 0.001, 0.2, 0.0) for index in range(21)]


def result(
    *,
    hands: list[list[FakeLandmark]],
    gestures: object = (),
    handedness: object = (),
    worlds: object = (),
) -> GestureTaskResult:
    return GestureTaskResult(gestures, handedness, hands, worlds)


class GestureTaskResultTests(TestCase):
    def test_realistic_aligned_collections_and_missing_worlds_are_valid(self) -> None:
        task = result(
            hands=[hand(), hand(0.3)],
            gestures=[
                [FakeCategory("Open_Palm", 0.9)],
                [FakeCategory("Closed_Fist", 0.9)],
            ],
            handedness=[[FakeCategory("Right", 0.8)], [FakeCategory("Left", 0.9)]],
        )
        task.validate()
        self.assertEqual(len(task.multi_hand_landmarks), 2)

    def test_empty_landmarks_means_no_hand(self) -> None:
        result(hands=[], gestures=[], handedness=[]).validate()

    def test_misaligned_and_nonfinite_task_results_fail_safely(self) -> None:
        cases = (
            result(hands=[hand()], handedness=[]),
            result(
                hands=[hand()],
                gestures=[[FakeCategory("Open_Palm", 0.9)], []],
                handedness=[[FakeCategory("Right", 0.9)]],
            ),
            result(
                hands=[hand()],
                handedness=[[FakeCategory("Right", 0.9)]],
                worlds=[hand(), hand()],
            ),
            result(hands=[hand()[:20]], handedness=[[FakeCategory("Right", 0.9)]]),
            result(
                hands=[hand() + [FakeLandmark(0.1, 0.2, 0.3)]],
                handedness=[[FakeCategory("Right", 0.9)]],
            ),
            result(
                hands=[[FakeLandmark(nan, 0.2, 0.3)] * 21],
                handedness=[[FakeCategory("Right", 0.9)]],
            ),
            result(
                hands=[[FakeLandmark(inf, 0.2, 0.3)] * 21],
                handedness=[[FakeCategory("Right", 0.9)]],
            ),
        )
        for task in cases:
            with self.assertRaises(MediaPipeGestureTaskError):
                task.validate()

    def test_canned_confidence_boundaries_and_unsupported_categories(self) -> None:
        observation = task_hand()
        supported = (
            ("Closed_Fist", "closed_fist", "mediapipe_closed_fist"),
            ("Open_Palm", "open_palm", "mediapipe_open_palm"),
            ("Pointing_Up", "point", "mediapipe_point"),
        )
        for category, gesture, reason in supported:
            candidate = classify_task_hand(
                GestureTaskResult([[FakeCategory(category, 0.65)]], (), (), ()),
                observation,
            )
            self.assertEqual(
                (candidate.gesture_id.value, candidate.confidence, candidate.reason),
                (gesture, 0.65, reason),
            )
        low = classify_task_hand(
            GestureTaskResult([[FakeCategory("Closed_Fist", 0.649999)]], (), (), ()),
            observation,
        )
        self.assertNotEqual(low.reason, "mediapipe_closed_fist")
        for category in (
            "Thumb_Up",
            "Thumb_Down",
            "Victory",
            "ILoveYou",
            "Future_Gesture",
        ):
            candidate = classify_task_hand(
                GestureTaskResult([[FakeCategory(category, 0.9)]], (), (), ()),
                observation,
            )
            self.assertEqual(
                (candidate.gesture_id.value, candidate.confidence, candidate.reason),
                ("unknown", 0.0, "unsupported_canned_gesture"),
            )

    def test_highest_usable_category_wins_and_invalid_scores_are_ignored(self) -> None:
        observation = task_hand()
        categories = [
            FakeCategory("", 0.99),
            FakeCategory("Open_Palm", 0.8),
            FakeCategory("Closed_Fist", 0.9),
        ]
        candidate = classify_task_hand(
            GestureTaskResult([categories], (), (), ()), observation
        )
        self.assertEqual(candidate.gesture_id.value, "closed_fist")
        invalid = [
            FakeCategory("Open_Palm", nan),
            FakeCategory("Closed_Fist", inf),
            FakeCategory("Pointing_Up", -inf),
        ]
        candidate = classify_task_hand(
            GestureTaskResult([invalid], (), (), ()), observation
        )
        self.assertNotIn(
            candidate.reason,
            {"mediapipe_open_palm", "mediapipe_closed_fist", "mediapipe_point"},
        )

    def test_isolated_pinch_distance_and_ratio_boundaries(self) -> None:
        for distance, expected in ((0.199, True), (0.2, True), (0.201, False)):
            candidate = classify_task_hand(
                GestureTaskResult([[]], (), (), ()), pinch_hand(distance, 0.6)
            )
            self.assertEqual(candidate.gesture_id.value == "pinch", expected)
        for ratio, expected in ((0.399, True), (0.4, True), (0.401, False)):
            candidate = classify_task_hand(
                GestureTaskResult([[]], (), (), ()), pinch_hand(0.1, 0.1 / ratio)
            )
            self.assertEqual(candidate.gesture_id.value == "pinch", expected)
        accepted = classify_task_hand(
            GestureTaskResult([[]], (), (), ()), pinch_hand(0.2, 0.5)
        )
        self.assertEqual(
            (accepted.gesture_id.value, accepted.confidence, accepted.reason),
            ("pinch", 0.85, "isolated_thumb_index_contact"),
        )

    def test_pinch_arbitration_and_invariance(self) -> None:
        base = pinch_hand(0.1, 0.5)
        for category, expected in (
            ("Closed_Fist", "closed_fist"),
            ("Open_Palm", "open_palm"),
            ("Pointing_Up", "point"),
        ):
            candidate = classify_task_hand(
                GestureTaskResult([[FakeCategory(category, 0.9)]], (), (), ()), base
            )
            self.assertEqual(candidate.gesture_id.value, expected)
        for category in (
            "Thumb_Up",
            "Thumb_Down",
            "Victory",
            "ILoveYou",
            "Future_Gesture",
        ):
            candidate = classify_task_hand(
                GestureTaskResult([[FakeCategory(category, 0.9)]], (), (), ()), base
            )
            self.assertEqual(
                (candidate.gesture_id.value, candidate.confidence, candidate.reason),
                ("pinch", 0.85, "isolated_thumb_index_contact"),
            )
        for categories in (
            [[FakeCategory("Open_Palm", 0.649999)]],
            [[]],
        ):
            candidate = classify_task_hand(
                GestureTaskResult(categories, (), (), ()), base
            )
            self.assertEqual(candidate.gesture_id.value, "pinch")
        for offset, scale, mirror in (
            ((4, -3, 2), 1, False),
            ((0, 0, 0), 3, False),
            ((0, 0, 0), 1, True),
        ):
            candidate = classify_task_hand(
                GestureTaskResult([[]], (), (), ()),
                pinch_hand(0.1, 0.5, offset=offset, scale=scale, mirror=mirror),
            )
            self.assertEqual(candidate.gesture_id.value, "pinch")

    def test_confident_unsupported_category_without_pinch_stays_unknown(self) -> None:
        candidate = classify_task_hand(
            GestureTaskResult([[FakeCategory("Thumb_Up", 0.9)]], (), (), ()),
            task_hand(),
        )

        self.assertEqual(
            (candidate.gesture_id.value, candidate.confidence, candidate.reason),
            ("unknown", 0.0, "unsupported_canned_gesture"),
        )

    def test_task_result_without_a_hand_has_no_candidate(self) -> None:
        frame = RecognitionService().process(
            GestureTaskResult([], [], [], []), frame_sequence=0, timestamp_ms=0
        )

        self.assertIsNone(frame.candidate)

    def test_zero_palm_scale_returns_safe_unknown(self) -> None:
        invalid = pinch_hand(0.1, 0.5)
        object.__setattr__(invalid, "palm_scale", 0)
        candidate = classify_task_hand(GestureTaskResult([[]], (), (), ()), invalid)
        self.assertEqual(
            (candidate.gesture_id.value, candidate.confidence, candidate.reason),
            ("unknown", 0.0, "invalid_pinch_geometry"),
        )

    def test_two_hand_task_adapter_preserves_distinct_ranking_inputs(self) -> None:
        hand_a = task_landmarks(marker=1, pinch=False)
        hand_b = task_landmarks(marker=4, pinch=True)
        task = two_hand_result(hand_a, hand_b, first_score=0.9, second_score=0.8)
        observations = adapt_hands(task)

        self.assertEqual(len(observations), 2)
        self.assertEqual(
            [
                (hand.source_index, hand.handedness.value, hand.handedness_confidence)
                for hand in observations
            ],
            [(0, "right", 0.9), (1, "left", 0.8)],
        )
        self.assertEqual(observations[0].landmarks, hand_a)
        self.assertEqual(observations[1].landmarks, hand_b)
        self.assertGreater(observations[0].palm_scale, 0)
        self.assertGreater(observations[1].palm_scale, 0)
        self.assertGreaterEqual(observations[0].palm_area, 0)
        self.assertGreaterEqual(observations[1].palm_area, 0)
        self.assertIsNone(observations[0].detection_confidence)
        self.assertIsNone(observations[1].detection_confidence)

    def test_two_hand_a_wins_on_higher_handedness_confidence(self) -> None:
        hand_a = task_landmarks(marker=1, pinch=False)
        hand_b = task_landmarks(marker=4, pinch=True)
        frame = RecognitionService().process(
            two_hand_result(hand_a, hand_b, first_score=0.9, second_score=0.8),
            frame_sequence=0,
            timestamp_ms=0,
        )

        self.assertEqual(
            (frame.primary_hand.source_index, frame.primary_hand.handedness.value),
            (0, "right"),
        )
        self.assertEqual(frame.primary_hand.landmarks, hand_a)
        self.assertEqual(frame.candidate.gesture_id.value, "open_palm")
        self.assertEqual(frame.candidate.reason, "mediapipe_open_palm")

    def test_two_hand_b_wins_on_higher_handedness_confidence(self) -> None:
        hand_a = task_landmarks(marker=1, pinch=False)
        hand_b = task_landmarks(marker=4, pinch=True)
        frame = RecognitionService().process(
            two_hand_result(hand_a, hand_b, first_score=0.8, second_score=0.9),
            frame_sequence=0,
            timestamp_ms=0,
        )

        self.assertEqual(
            (frame.primary_hand.source_index, frame.primary_hand.handedness.value),
            (1, "left"),
        )
        self.assertEqual(frame.primary_hand.landmarks, hand_b)
        self.assertEqual(
            (frame.candidate.gesture_id.value, frame.candidate.reason),
            ("pinch", "isolated_thumb_index_contact"),
        )

    def test_two_hand_candidate_never_leaks_from_the_non_primary_hand(self) -> None:
        hand_a = task_landmarks(marker=1, pinch=False)
        hand_b = task_landmarks(marker=4, pinch=True)
        a_wins = RecognitionService().process(
            two_hand_result(hand_a, hand_b, first_score=0.9, second_score=0.8),
            frame_sequence=0,
            timestamp_ms=0,
        )
        b_wins = RecognitionService().process(
            two_hand_result(hand_a, hand_b, first_score=0.8, second_score=0.9),
            frame_sequence=0,
            timestamp_ms=0,
        )

        self.assertEqual(a_wins.candidate.gesture_id.value, "open_palm")
        self.assertEqual(b_wins.candidate.gesture_id.value, "pinch")

    def test_two_hand_primary_selection_is_deterministic_for_repeated_input(
        self,
    ) -> None:
        task = two_hand_result(
            task_landmarks(marker=1, pinch=False),
            task_landmarks(marker=4, pinch=True),
            first_score=0.8,
            second_score=0.9,
        )
        selected = [
            RecognitionService()
            .process(task, frame_sequence=0, timestamp_ms=0)
            .primary_hand.source_index
            for _ in range(4)
        ]
        self.assertEqual(selected, [1, 1, 1, 1])

    def test_two_hand_exact_tie_uses_lower_source_index(self) -> None:
        task = two_hand_result(
            task_landmarks(marker=1, pinch=False),
            task_landmarks(marker=4, pinch=True),
            first_score=0.9,
            second_score=0.9,
        )
        primary = select_primary(adapt_hands(task)).primary_hand
        self.assertEqual(primary.source_index, 0)

    def test_two_hand_reversed_aligned_arrays_keep_logical_data_aligned(self) -> None:
        hand_a = task_landmarks(marker=1, pinch=False)
        hand_b = task_landmarks(marker=4, pinch=True)
        reversed_task = GestureTaskResult(
            [[], [FakeCategory("Open_Palm", 0.9)]],
            [[FakeCategory("Left", 0.9)], [FakeCategory("Right", 0.8)]],
            [hand_b, hand_a],
            [(), ()],
        )
        frame = RecognitionService().process(
            reversed_task, frame_sequence=0, timestamp_ms=0
        )
        self.assertEqual(
            (frame.primary_hand.source_index, frame.primary_hand.handedness.value),
            (0, "left"),
        )
        self.assertEqual(frame.primary_hand.landmarks, hand_b)
        self.assertEqual(frame.candidate.gesture_id.value, "pinch")

    def test_direct_and_legacy_landmark_containers_adapt_equivalently(self) -> None:
        landmarks = task_landmarks(marker=1, pinch=False)
        direct = GestureTaskResult(
            [[]], [[FakeCategory("Right", 0.9)]], [landmarks], [()]
        )
        legacy = type(
            "LegacyResult",
            (),
            {
                "multi_hand_landmarks": [
                    type("LegacyHand", (), {"landmark": landmarks})()
                ],
                "multi_handedness": [[FakeCategory("Right", 0.9)]],
            },
        )()
        self.assertEqual(adapt_hands(direct), adapt_hands(legacy))

    def test_malformed_direct_landmark_sequences_are_skipped(self) -> None:
        incomplete = GestureTaskResult(
            [[]], [[FakeCategory("Right", 0.9)]], [task_landmarks()[:20]], [()]
        )
        nonfinite_points = list(task_landmarks())
        nonfinite_points[8] = FakeLandmark(nan, 0, 0)
        nonfinite = GestureTaskResult(
            [[]], [[FakeCategory("Right", 0.9)]], [tuple(nonfinite_points)], [()]
        )
        self.assertEqual(adapt_hands(incomplete), ())
        self.assertEqual(adapt_hands(nonfinite), ())

    def test_malformed_pinch_geometry_returns_safe_unknown(self) -> None:
        malformed = pinch_hand(0.1, 0.5)
        points = list(malformed.landmarks)
        object.__setattr__(malformed, "landmarks", tuple(points[:20]))
        candidate = classify_task_hand(GestureTaskResult([[]], (), (), ()), malformed)
        self.assertEqual(
            (candidate.gesture_id.value, candidate.confidence, candidate.reason),
            ("unknown", 0.0, "invalid_pinch_geometry"),
        )


def task_hand() -> HandObservation:
    points = tuple(
        Landmark3D(float(index % 5), float(index // 5), 0.0) for index in range(21)
    )
    return HandObservation(points, 0, Handedness.RIGHT, 0.9, 0.9, 1.0, 1.0)


def pinch_hand(
    distance: float,
    denominator: float,
    *,
    offset: tuple[float, float, float] = (0, 0, 0),
    scale: float = 1,
    mirror: bool = False,
) -> HandObservation:
    """Palm scale is one; thumb is origin, index is d, other fingertips are denominator away."""
    points = [Landmark3D(0, 0, 0) for _ in range(21)]
    direction = -1 if mirror else 1
    for index, x in (
        (4, 0),
        (8, distance),
        (12, denominator),
        (16, denominator * 2),
        (20, denominator * 3),
    ):
        points[index] = Landmark3D(
            (x * direction * scale) + offset[0], offset[1], offset[2]
        )
    points[0] = Landmark3D(offset[0], offset[1], offset[2])
    return HandObservation(tuple(points), 0, Handedness.RIGHT, 0.9, 0.9, scale, 1.0)


def task_landmarks(*, marker: float = 0, pinch: bool = True) -> tuple[Landmark3D, ...]:
    """Direct task landmarks with a non-degenerate palm and distinct tip geometry."""
    points = [Landmark3D(marker, 0, 0) for _ in range(21)]
    points[0] = Landmark3D(marker, 0, 0)
    for index, x, y in ((5, 0, 1), (9, 0.5, 1), (13, 0.5, 2), (17, 0, 2)):
        points[index] = Landmark3D(marker + x, y, 0)
    for index, x in ((4, 0), (8, 0.1 if pinch else 1), (12, 0.5), (16, 1), (20, 1.5)):
        points[index] = Landmark3D(marker + x, 0, 0)
    return tuple(points)


def two_hand_result(
    hand_a: tuple[Landmark3D, ...],
    hand_b: tuple[Landmark3D, ...],
    *,
    first_score: float,
    second_score: float,
) -> GestureTaskResult:
    return GestureTaskResult(
        [[FakeCategory("Open_Palm", 0.9)], []],
        [[FakeCategory("Right", first_score)], [FakeCategory("Left", second_score)]],
        [hand_a, hand_b],
        [(), ()],
    )
