"""Synchronous frame coordination and deterministic single-hand arbitration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Any

from .gesture_engine import (
    GestureEngine,
    GestureEngineDecision,
    GestureEngineInput,
    GestureEngineResult,
    GestureObservation,
    NeutralGestureObservation,
    NeutralObservationReason,
)
from .gesture_pipeline import (
    GesturePipeline,
    GesturePipelineResult,
    HandGestureResult,
)


class GestureRuntimeStage(StrEnum):
    """Runtime coordination stages used in contextual errors."""

    PIPELINE_PROCESSING = "pipeline processing"
    HAND_SELECTION = "hand selection"
    OBSERVATION_ADAPTATION = "observation adaptation"
    ENGINE_PROCESSING = "engine processing"
    LIFECYCLE = "lifecycle"


class GestureRuntimeError(RuntimeError):
    """Raised when a runtime coordination stage cannot complete."""

    def __init__(
        self,
        stage: GestureRuntimeStage,
        message: str,
        *,
        identity: SelectedHandIdentity | None = None,
    ) -> None:
        self.stage = stage
        self.identity = identity
        context = f" for hand {identity!r}" if identity is not None else ""
        super().__init__(f"{stage.value}{context}: {message}")


class HandSelectionPolicy(StrEnum):
    """Deterministic policies for choosing at most one detected hand."""

    FIRST_DETECTED = "FIRST_DETECTED"
    HIGHEST_CONFIDENCE = "HIGHEST_CONFIDENCE"
    PREFERRED_HANDEDNESS = "PREFERRED_HANDEDNESS"


class HandSelectionDecision(StrEnum):
    """Reason the runtime selected its hand for the current frame."""

    NO_HANDS = "NO_HANDS"
    FIRST_DETECTED = "FIRST_DETECTED"
    HIGHEST_CONFIDENCE = "HIGHEST_CONFIDENCE"
    PREFERRED_HANDEDNESS = "PREFERRED_HANDEDNESS"
    PREFERRED_FALLBACK = "PREFERRED_FALLBACK"
    STICKY_RETAINED = "STICKY_RETAINED"
    HAND_SWITCHED = "HAND_SWITCHED"


@dataclass(frozen=True, slots=True)
class SelectedHandIdentity:
    """Best available identity without pretending to provide spatial tracking.

    Hand index and normalized handedness are stable metadata for a processor
    result, but they are not persistent tracking identifiers across occlusion.
    """

    hand_index: int
    handedness: str


@dataclass(frozen=True, slots=True)
class GestureRuntimeConfig:
    """Validated hand arbitration configuration."""

    selection_policy: HandSelectionPolicy = HandSelectionPolicy.FIRST_DETECTED
    preferred_handedness: str | None = None
    sticky_selection: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.selection_policy, HandSelectionPolicy):
            raise GestureRuntimeError(
                GestureRuntimeStage.HAND_SELECTION,
                "selection_policy must be a HandSelectionPolicy",
            )
        if self.preferred_handedness is not None and (
            not isinstance(self.preferred_handedness, str)
            or not self.preferred_handedness.strip()
        ):
            raise GestureRuntimeError(
                GestureRuntimeStage.HAND_SELECTION,
                "preferred_handedness must be a non-empty string",
            )
        if (
            self.selection_policy is HandSelectionPolicy.PREFERRED_HANDEDNESS
            and self.preferred_handedness is None
        ):
            raise GestureRuntimeError(
                GestureRuntimeStage.HAND_SELECTION,
                "PREFERRED_HANDEDNESS requires preferred_handedness",
            )
        if (
            self.selection_policy is not HandSelectionPolicy.PREFERRED_HANDEDNESS
            and self.preferred_handedness is not None
        ):
            raise GestureRuntimeError(
                GestureRuntimeStage.HAND_SELECTION,
                "preferred_handedness is only valid with PREFERRED_HANDEDNESS",
            )
        if not isinstance(self.sticky_selection, bool):
            raise GestureRuntimeError(
                GestureRuntimeStage.HAND_SELECTION,
                "sticky_selection must be a bool",
            )


@dataclass(frozen=True, slots=True)
class GestureRuntimeResult:
    """Immutable references to one complete pipeline/runtime/engine result."""

    pipeline_result: GesturePipelineResult
    selected_hand: HandGestureResult | None
    selected_identity: SelectedHandIdentity | None
    selection_decision: HandSelectionDecision
    observation: GestureEngineInput
    engine_result: GestureEngineResult

    @property
    def annotated_frame(self) -> Any:
        return self.pipeline_result.annotated_frame

    @property
    def detected_hand_count(self) -> int:
        return self.pipeline_result.hand_count

    @property
    def action_executed(self) -> bool:
        return self.engine_result.action_executed

    @property
    def timestamp(self) -> float:
        return self.engine_result.timestamp


class GestureRuntime:
    """Coordinate one frame without owning capture or background execution."""

    def __init__(
        self,
        pipeline: GesturePipeline | None = None,
        engine: GestureEngine | None = None,
        config: GestureRuntimeConfig | None = None,
    ) -> None:
        self.pipeline = pipeline if pipeline is not None else GesturePipeline()
        self.engine = engine if engine is not None else GestureEngine()
        self.config = config or GestureRuntimeConfig()
        self._owns_pipeline = pipeline is None
        self._owns_engine = engine is None
        self._selected_identity: SelectedHandIdentity | None = None
        self._closed = False

    def process(
        self,
        frame: Any,
        *,
        timestamp: float | None = None,
    ) -> GestureRuntimeResult:
        """Process one frame, select one hand, and advance temporal state."""

        self._ensure_open("process")
        try:
            pipeline_result = self.pipeline.process(frame)
        except Exception as error:
            raise GestureRuntimeError(
                GestureRuntimeStage.PIPELINE_PROCESSING,
                str(error) or type(error).__name__,
            ) from error

        if not pipeline_result.hands:
            return self._process_no_hand(pipeline_result, timestamp)

        try:
            selected_hand, decision = self._select_hand(pipeline_result.hands)
            identity = self._identity(selected_hand)
        except Exception as error:
            raise GestureRuntimeError(
                GestureRuntimeStage.HAND_SELECTION,
                str(error) or type(error).__name__,
            ) from error

        try:
            observation = GestureObservation(
                prediction=selected_hand.prediction,
                detection_confidence=selected_hand.confidence,
            )
        except Exception as error:
            raise GestureRuntimeError(
                GestureRuntimeStage.OBSERVATION_ADAPTATION,
                str(error) or type(error).__name__,
                identity=identity,
            ) from error

        switched = (
            self._selected_identity is not None and identity != self._selected_identity
        )
        if switched:
            try:
                self.engine.reset()
            except Exception as error:
                raise GestureRuntimeError(
                    GestureRuntimeStage.ENGINE_PROCESSING,
                    f"failed to reset temporal state before hand switch: {error}",
                    identity=identity,
                ) from error
            self._selected_identity = identity
            decision = HandSelectionDecision.HAND_SWITCHED

        try:
            engine_result = self.engine.process(observation, timestamp=timestamp)
        except Exception as error:
            raise GestureRuntimeError(
                GestureRuntimeStage.ENGINE_PROCESSING,
                str(error) or type(error).__name__,
                identity=identity,
            ) from error

        self._selected_identity = identity
        return GestureRuntimeResult(
            pipeline_result=pipeline_result,
            selected_hand=selected_hand,
            selected_identity=identity,
            selection_decision=decision,
            observation=observation,
            engine_result=engine_result,
        )

    def _process_no_hand(
        self,
        pipeline_result: GesturePipelineResult,
        timestamp: float | None,
    ) -> GestureRuntimeResult:
        observation = NeutralGestureObservation(
            NeutralObservationReason.NO_HAND_DETECTED
        )
        try:
            engine_result = self.engine.process(observation, timestamp=timestamp)
        except Exception as error:
            raise GestureRuntimeError(
                GestureRuntimeStage.ENGINE_PROCESSING,
                str(error) or type(error).__name__,
                identity=self._selected_identity,
            ) from error

        if (
            engine_result.decision is GestureEngineDecision.RELEASED
            or engine_result.active_label is None
        ):
            self._selected_identity = None
        return GestureRuntimeResult(
            pipeline_result=pipeline_result,
            selected_hand=None,
            selected_identity=self._selected_identity,
            selection_decision=HandSelectionDecision.NO_HANDS,
            observation=observation,
            engine_result=engine_result,
        )

    def _select_hand(
        self,
        hands: tuple[HandGestureResult, ...],
    ) -> tuple[HandGestureResult, HandSelectionDecision]:
        if self.config.sticky_selection and self._selected_identity is not None:
            for hand in hands:
                if self._identity(hand) == self._selected_identity:
                    return hand, HandSelectionDecision.STICKY_RETAINED

        if self.config.selection_policy is HandSelectionPolicy.FIRST_DETECTED:
            return hands[0], HandSelectionDecision.FIRST_DETECTED
        if self.config.selection_policy is HandSelectionPolicy.HIGHEST_CONFIDENCE:
            return max(hands, key=lambda hand: hand.confidence), (
                HandSelectionDecision.HIGHEST_CONFIDENCE
            )

        preferred = self.config.preferred_handedness
        assert preferred is not None
        normalized_preference = preferred.strip().casefold()
        for hand in hands:
            if hand.handedness.strip().casefold() == normalized_preference:
                return hand, HandSelectionDecision.PREFERRED_HANDEDNESS
        return hands[0], HandSelectionDecision.PREFERRED_FALLBACK

    @staticmethod
    def _identity(hand: HandGestureResult) -> SelectedHandIdentity:
        handedness = hand.handedness.strip().casefold()
        if not handedness:
            raise ValueError("selected hand has empty handedness metadata")
        return SelectedHandIdentity(hand.hand_index, handedness)

    def reset(self) -> None:
        """Reset temporal and retained hand state without closing dependencies."""

        self._ensure_open("reset")
        self.engine.reset()
        self._selected_identity = None

    def close(self) -> None:
        """Close owned dependencies in engine-then-pipeline order."""

        if self._closed:
            return
        self._closed = True
        failures: list[tuple[str, Exception]] = []
        for name, dependency, owned in (
            ("engine", self.engine, self._owns_engine),
            ("pipeline", self.pipeline, self._owns_pipeline),
        ):
            if not owned:
                continue
            try:
                dependency.close()
            except Exception as error:
                failures.append((name, error))
        if failures:
            names = ", ".join(name for name, _ in failures)
            raise GestureRuntimeError(
                GestureRuntimeStage.LIFECYCLE,
                f"failed to close owned dependencies: {names}",
            ) from failures[0][1]

    def _ensure_open(self, operation: str) -> None:
        if self._closed:
            raise GestureRuntimeError(
                GestureRuntimeStage.LIFECYCLE,
                f"cannot {operation} after runtime closure",
            )

    def __enter__(self) -> GestureRuntime:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
