"""Process-local owner-checked lease for Windows cursor control."""

from __future__ import annotations

from threading import RLock

from .models import MouseValidationError


class MouseOwnershipLease:
    """Process-local owner-checked lease interface."""

    def acquire(self, owner_id: str) -> bool: ...
    def release(self, owner_id: str) -> bool: ...


class WindowsCursorOwnershipLease:
    def __init__(self) -> None:
        self._owner: str | None = None
        self._lock = RLock()

    @property
    def owner_id(self) -> str | None:
        with self._lock:
            return self._owner

    def acquire(self, owner: str) -> bool:
        self._validate_owner(owner)
        with self._lock:
            if self._owner is None or self._owner == owner:
                self._owner = owner
                return True
            return False

    def release(self, owner: str) -> bool:
        self._validate_owner(owner)
        with self._lock:
            if self._owner != owner:
                return False
            self._owner = None
            return True

    @staticmethod
    def _validate_owner(owner: str) -> None:
        if not isinstance(owner, str) or not owner.strip():
            raise MouseValidationError("owner_id must be a non-blank string.")
