from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase

from gestureboard.mouse import MouseValidationError, WindowsCursorOwnershipLease


class MouseOwnershipTests(TestCase):
    def test_ownership_contract_and_validation(self) -> None:
        lease = WindowsCursorOwnershipLease()
        with self.assertRaises(MouseValidationError):
            lease.acquire(" ")
        self.assertTrue(lease.acquire("a"))
        self.assertTrue(lease.acquire("a"))
        self.assertFalse(lease.acquire("b"))
        self.assertFalse(lease.release("b"))
        self.assertEqual(lease.owner_id, "a")
        self.assertTrue(lease.release("a"))
        self.assertTrue(lease.acquire("b"))

    def test_concurrent_acquisition_has_one_owner(self) -> None:
        lease = WindowsCursorOwnershipLease()
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lease.acquire, ("a", "b")))
        self.assertEqual(outcomes.count(True), 1)
        self.assertIn(lease.owner_id, {"a", "b"})

    def test_cross_process_configuration_is_idempotent_without_disturbing_owner(self):
        class Mutex:
            def release(self):
                return None

        lease = WindowsCursorOwnershipLease()
        lease.enable_cross_process(Mutex)
        self.assertTrue(lease.acquire("a"))
        lease.enable_cross_process(Mutex)
        self.assertEqual(lease.owner_id, "a")

        class ConflictingMutex(Mutex):
            pass

        with self.assertRaisesRegex(MouseValidationError, "different factory"):
            lease.enable_cross_process(ConflictingMutex)
        self.assertEqual(lease.owner_id, "a")
