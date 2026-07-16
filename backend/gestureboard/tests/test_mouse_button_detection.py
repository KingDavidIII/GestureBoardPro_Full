from math import inf, nan

from django.test import SimpleTestCase

from gestureboard.mouse.buttons import (
    MouseButtonIntent,
    MouseButtonPolicy,
    detect_button_intent,
)
from gestureboard.recognition.observations import (
    Handedness,
    HandObservation,
    Landmark3D,
)


def hand(*, thumb=(0.0, 0.0), index=(1.0, 0.0), middle=(2.0, 0.0), scale=1.0):
    points = [Landmark3D(5.0, 5.0, 0.0) for _ in range(21)]
    for position, value in ((4, thumb), (8, index), (12, middle)):
        points[position] = Landmark3D(*value, 0.0)
    return HandObservation(tuple(points), 3, Handedness.RIGHT, 1.0, None, scale, 1.0)


class ButtonDetectionTests(SimpleTestCase):
    def test_primary_secondary_none_and_ambiguous_contacts(self):
        policy = MouseButtonPolicy()
        self.assertEqual(
            detect_button_intent(hand(index=(0.2, 0)), policy),
            MouseButtonIntent.PRIMARY_CONTACT,
        )
        self.assertEqual(
            detect_button_intent(hand(middle=(0.2, 0)), policy),
            MouseButtonIntent.SECONDARY_CONTACT,
        )
        self.assertEqual(detect_button_intent(hand(), policy), MouseButtonIntent.NONE)
        self.assertEqual(
            detect_button_intent(hand(index=(0.1, 0), middle=(0.1, 0)), policy),
            MouseButtonIntent.AMBIGUOUS,
        )

    def test_hysteresis_and_malformed_hand_are_safe(self):
        policy = MouseButtonPolicy()
        sample = hand(index=(0.22, 0))
        self.assertEqual(detect_button_intent(sample, policy), MouseButtonIntent.NONE)
        self.assertEqual(
            detect_button_intent(sample, policy, MouseButtonIntent.PRIMARY_CONTACT),
            MouseButtonIntent.PRIMARY_CONTACT,
        )
        malformed = hand()
        object.__setattr__(malformed, "palm_scale", 0.0)
        self.assertEqual(
            detect_button_intent(malformed, policy), MouseButtonIntent.AMBIGUOUS
        )
        self.assertEqual(
            detect_button_intent(None, policy), MouseButtonIntent.AMBIGUOUS
        )

    def test_boundaries_collections_and_unsafe_coordinates(self):
        policy = MouseButtonPolicy()
        self.assertEqual(
            detect_button_intent(hand(index=(0.25, 0)), policy),
            MouseButtonIntent.NONE,
        )
        self.assertEqual(
            detect_button_intent((hand(),), policy), MouseButtonIntent.AMBIGUOUS
        )
        for value in (nan, inf, -inf, True):
            sample = hand()
            points = list(sample.landmarks)
            points[4] = type("Point", (), {"x": value, "y": 0.0})()
            object.__setattr__(sample, "landmarks", tuple(points))
            self.assertEqual(
                detect_button_intent(sample, policy), MouseButtonIntent.AMBIGUOUS
            )

    def test_missing_required_landmarks_and_malformed_point_fail_closed(self):
        policy = MouseButtonPolicy()
        for length in (4, 8, 12):
            with self.subTest(length=length):
                sample = hand()
                object.__setattr__(sample, "landmarks", sample.landmarks[:length])
                self.assertEqual(
                    detect_button_intent(sample, policy), MouseButtonIntent.AMBIGUOUS
                )

    def test_isolation_boundaries_and_per_contact_hysteresis(self):
        policy = MouseButtonPolicy()
        self.assertEqual(
            detect_button_intent(hand(index=(0.2, 0), middle=(0.5, 0)), policy),
            MouseButtonIntent.PRIMARY_CONTACT,
        )
        self.assertEqual(
            detect_button_intent(hand(index=(0.2, 0), middle=(0.49, 0)), policy),
            MouseButtonIntent.AMBIGUOUS,
        )
        self.assertEqual(
            detect_button_intent(hand(index=(0.26, 0), middle=(1.0, 0)), policy),
            MouseButtonIntent.NONE,
        )
        self.assertEqual(
            detect_button_intent(
                hand(index=(0.22, 0), middle=(1.0, 0)),
                policy,
                MouseButtonIntent.PRIMARY_CONTACT,
            ),
            MouseButtonIntent.PRIMARY_CONTACT,
        )
        self.assertEqual(
            detect_button_intent(
                hand(index=(0.30, 0), middle=(0.22, 0)),
                policy,
                MouseButtonIntent.PRIMARY_CONTACT,
            ),
            MouseButtonIntent.NONE,
        )
        sample = hand()
        points = list(sample.landmarks)
        points[8] = object()
        object.__setattr__(sample, "landmarks", tuple(points))
        self.assertEqual(
            detect_button_intent(sample, policy), MouseButtonIntent.AMBIGUOUS
        )
