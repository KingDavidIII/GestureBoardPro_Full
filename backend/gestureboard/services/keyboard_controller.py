"""Safe, synchronous execution of stateless keyboard actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Any, Protocol


class KeyboardControllerError(RuntimeError):
    """Raised for malformed actions, unsupported keys, or backend failures."""


class KeyboardActionKind(StrEnum):
    """Stateless keyboard operations supported in Alpha 7."""

    TAP_KEY = "TAP_KEY"
    HOTKEY = "HOTKEY"
    TYPE_TEXT = "TYPE_TEXT"


@dataclass(frozen=True, slots=True)
class KeyboardAction:
    """Validated description of one keyboard operation."""

    kind: KeyboardActionKind
    keys: tuple[str, ...] = ()
    text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, KeyboardActionKind):
            raise KeyboardControllerError("kind must be a KeyboardActionKind.")
        if not isinstance(self.keys, tuple) or any(
            not isinstance(key, str) or not key.strip() for key in self.keys
        ):
            raise KeyboardControllerError("keys must be a tuple of non-empty names.")

        if self.kind is KeyboardActionKind.TAP_KEY:
            if len(self.keys) != 1 or self.text is not None:
                raise KeyboardControllerError(
                    "TAP_KEY requires exactly one key and no text."
                )
        elif self.kind is KeyboardActionKind.HOTKEY:
            if len(self.keys) < 2 or self.text is not None:
                raise KeyboardControllerError(
                    "HOTKEY requires at least two keys and no text."
                )
        elif self.kind is KeyboardActionKind.TYPE_TEXT:
            if self.keys or not isinstance(self.text, str) or not self.text:
                raise KeyboardControllerError(
                    "TYPE_TEXT requires non-empty text and no keys."
                )

    @classmethod
    def tap(cls, key: str) -> KeyboardAction:
        return cls(KeyboardActionKind.TAP_KEY, keys=(key,))

    @classmethod
    def hotkey(cls, *keys: str) -> KeyboardAction:
        return cls(KeyboardActionKind.HOTKEY, keys=tuple(keys))

    @classmethod
    def type_text(cls, text: str) -> KeyboardAction:
        return cls(KeyboardActionKind.TYPE_TEXT, text=text)


@dataclass(frozen=True, slots=True)
class KeyboardExecutionResult:
    """Successful execution of exactly one keyboard action."""

    action: KeyboardAction
    executed: bool = True


class KeyboardBackend(Protocol):
    """Minimum interface required from an operating-system keyboard backend."""

    def press(self, key: Any) -> None: ...

    def release(self, key: Any) -> None: ...

    def type(self, text: str) -> None: ...


class _PynputBackend:
    """Small adapter that keeps pynput imports and construction lazy."""

    def __init__(self) -> None:
        from pynput.keyboard import Controller, Key

        self._controller = Controller()
        self._key_type = Key

    def resolve_key(self, name: str) -> Any:
        return getattr(self._key_type, name)

    def press(self, key: Any) -> None:
        self._controller.press(key)

    def release(self, key: Any) -> None:
        self._controller.release(key)

    def type(self, text: str) -> None:
        self._controller.type(text)

    def close(self) -> None:
        close = getattr(self._controller, "close", None)
        if callable(close):
            close()


class KeyboardController:
    """Execute validated keyboard actions through an injectable backend."""

    _NAMED_KEYS = frozenset(
        {
            "enter",
            "space",
            "backspace",
            "tab",
            "esc",
            "ctrl",
            "shift",
            "alt",
            "up",
            "down",
            "left",
            "right",
            "delete",
            "home",
            "end",
            "page_up",
            "page_down",
        }
    )
    _ALIASES = {"escape": "esc", "control": "ctrl", "return": "enter"}

    def __init__(self, backend: KeyboardBackend | None = None) -> None:
        self._backend = backend
        self._owns_backend = backend is None
        self._closed = False

    def execute(self, action: KeyboardAction) -> KeyboardExecutionResult:
        """Execute one validated action or raise with backend context."""

        self._ensure_open()
        if not isinstance(action, KeyboardAction):
            raise KeyboardControllerError("action must be a KeyboardAction.")
        if action.kind is KeyboardActionKind.TAP_KEY:
            self._tap_resolved(self._resolve_key(action.keys[0]))
        elif action.kind is KeyboardActionKind.HOTKEY:
            self._execute_hotkey(tuple(self._resolve_key(key) for key in action.keys))
        else:
            self._execute_text(action.text or "")
        return KeyboardExecutionResult(action=action)

    def tap_key(self, key: str) -> KeyboardExecutionResult:
        return self.execute(KeyboardAction.tap(key))

    def hotkey(self, *keys: str) -> KeyboardExecutionResult:
        return self.execute(KeyboardAction.hotkey(*keys))

    def type_text(self, text: str) -> KeyboardExecutionResult:
        return self.execute(KeyboardAction.type_text(text))

    def _tap_resolved(self, key: Any) -> None:
        backend = self._get_backend()
        pressed = False
        try:
            backend.press(key)
            pressed = True
            backend.release(key)
        except Exception as error:
            if pressed:
                try:
                    backend.release(key)
                except Exception:
                    pass
            raise KeyboardControllerError(f"Failed to tap key {key!r}.") from error

    def _execute_hotkey(self, keys: tuple[Any, ...]) -> None:
        backend = self._get_backend()
        pressed: list[Any] = []
        try:
            for key in keys:
                backend.press(key)
                pressed.append(key)
        except Exception as error:
            self._release_for_cleanup(backend, pressed)
            raise KeyboardControllerError(
                "Failed while pressing hotkey keys."
            ) from error

        release_error: Exception | None = None
        for key in reversed(pressed):
            try:
                backend.release(key)
            except Exception as error:
                if release_error is None:
                    release_error = error
        if release_error is not None:
            raise KeyboardControllerError(
                "Failed while releasing hotkey keys."
            ) from release_error

    @staticmethod
    def _release_for_cleanup(backend: KeyboardBackend, pressed: list[Any]) -> None:
        for key in reversed(pressed):
            try:
                backend.release(key)
            except Exception:
                pass

    def _execute_text(self, text: str) -> None:
        backend = self._get_backend()
        try:
            type_method = getattr(backend, "type", None)
            if callable(type_method):
                type_method(text)
            else:
                write_method = getattr(backend, "write", None)
                if not callable(write_method):
                    raise AttributeError("backend provides neither type() nor write()")
                write_method(text)
        except Exception as error:
            raise KeyboardControllerError("Failed to type text.") from error

    def _resolve_key(self, key: str) -> Any:
        if not isinstance(key, str) or not key.strip():
            raise KeyboardControllerError("Key names must be non-empty strings.")
        normalized = key.strip().lower()
        normalized = self._ALIASES.get(normalized, normalized)
        if len(normalized) == 1 and normalized.isprintable():
            return normalized
        if normalized not in self._NAMED_KEYS:
            raise KeyboardControllerError(f"Unsupported key name: {key!r}.")
        backend = self._get_backend()
        resolver = getattr(backend, "resolve_key", None)
        if callable(resolver):
            try:
                return resolver(normalized)
            except Exception as error:
                raise KeyboardControllerError(
                    f"Backend cannot resolve key {normalized!r}."
                ) from error
        named_keys = getattr(backend, "named_keys", None)
        if named_keys is not None and normalized in named_keys:
            return named_keys[normalized]
        raise KeyboardControllerError(
            f"Backend does not support named key {normalized!r}."
        )

    def _get_backend(self) -> KeyboardBackend:
        if self._backend is None:
            try:
                self._backend = _PynputBackend()
            except Exception as error:
                raise KeyboardControllerError(
                    "Could not initialize the pynput keyboard backend."
                ) from error
        return self._backend

    def _ensure_open(self) -> None:
        if self._closed:
            raise KeyboardControllerError("Keyboard controller has been closed.")

    def close(self) -> None:
        """Close an internally created backend, at most once."""

        if self._closed:
            return
        self._closed = True
        if self._owns_backend and self._backend is not None:
            close = getattr(self._backend, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as error:
                    raise KeyboardControllerError(
                        "Failed to close the keyboard backend."
                    ) from error

    def __enter__(self) -> KeyboardController:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
