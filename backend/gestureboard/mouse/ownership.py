"""Process-local owner-checked lease for Windows cursor control."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from threading import RLock
from typing import Protocol

from .models import MouseOutputError, MouseValidationError

_MUTEX_NAME = "Global\\GestureBoardPro.NativeMouseOutput"
_ERROR_ALREADY_EXISTS = 183


class NamedMutexApi(Protocol):
    def create(self, name: str) -> tuple[object, bool]: ...
    def release(self, handle: object) -> None: ...


class _CtypesNamedMutexApi:
    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            ctypes.wintypes.BOOL,
            ctypes.wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (ctypes.wintypes.HANDLE,)
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        self._create_mutex = kernel32.CreateMutexW
        self._close_handle = kernel32.CloseHandle

    def create(self, name: str) -> tuple[object, bool]:
        ctypes.set_last_error(0)
        handle = self._create_mutex(None, False, name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        return handle, error == _ERROR_ALREADY_EXISTS

    def release(self, handle: object) -> None:
        ctypes.set_last_error(0)
        if not self._close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


class WindowsNamedMutex:
    def __init__(self, api: NamedMutexApi | None = None) -> None:
        self._api = api or _CtypesNamedMutexApi()
        self._handle, already_exists = self._api.create(_MUTEX_NAME)
        self._released = False
        if already_exists:
            acquisition_error = MouseOutputError(
                "native mouse output is already owned by another Windows process"
            )
            try:
                self._api.release(self._handle)
            except Exception as cleanup_error:
                self._released = True
                raise acquisition_error from cleanup_error
            self._released = True
            raise acquisition_error

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._api.release(self._handle)


class MouseOwnershipLease:
    """Process-local owner-checked lease interface."""

    def acquire(self, owner_id: str) -> bool: ...
    def release(self, owner_id: str) -> bool: ...


class WindowsCursorOwnershipLease:
    def __init__(self) -> None:
        self._owner: str | None = None
        self._lock = RLock()
        self._system_mutex_factory = None
        self._system_mutex = None

    def enable_cross_process(self, factory=WindowsNamedMutex) -> None:
        with self._lock:
            if self._system_mutex_factory is None:
                self._system_mutex_factory = factory
                return
            if self._system_mutex_factory is not factory:
                raise MouseValidationError(
                    "cross-process ownership is already configured with a different factory."
                )

    @property
    def owner_id(self) -> str | None:
        with self._lock:
            return self._owner

    def acquire(self, owner: str) -> bool:
        self._validate_owner(owner)
        with self._lock:
            if self._owner is None:
                if self._system_mutex_factory is not None:
                    self._system_mutex = self._system_mutex_factory()
                self._owner = owner
                return True
            if self._owner == owner:
                return True
            return False

    def release(self, owner: str) -> bool:
        self._validate_owner(owner)
        with self._lock:
            if self._owner != owner:
                return False
            self._owner = None
            mutex, self._system_mutex = self._system_mutex, None
            if mutex is not None:
                mutex.release()
            return True

    @staticmethod
    def _validate_owner(owner: str) -> None:
        if not isinstance(owner, str) or not owner.strip():
            raise MouseValidationError("owner_id must be a non-blank string.")
