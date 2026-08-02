"""Deterministic duplicate suppression and partial Mission continuation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    derive_attempt_id,
)
from nexus_vector.domain.mission_models import EffectState, MissionState


class AttemptLookup(Protocol):
    def get(self, attempt_id: str): ...


class ContinuationAction(str, Enum):
    SKIP_VERIFIED = "SKIP_VERIFIED"
    EXECUTE_MISSING = "EXECUTE_MISSING"
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True)
class EffectContinuationDecision:
    effect_ref: str
    effect_id: str
    amount_base_units: int
    action: ContinuationAction
    reason_code: str


@dataclass(frozen=True)
class MissionContinuationPlan:
    mission_key: str
    mission_state: MissionState
    decisions: tuple[EffectContinuationDecision, ...]
    total_amount_base_units: int
    skipped_amount_base_units: int
    executable_amount_base_units: int
    unresolved_amount_base_units: int
    manual_review_amount_base_units: int

    @property
    def executable_effect_ids(self) -> tuple[str, ...]:
        return tuple(
            decision.effect_id
            for decision in self.decisions
            if decision.action is ContinuationAction.EXECUTE_MISSING
        )

    @property
    def requires_reconciliation(self) -> bool:
        return any(
            decision.action is ContinuationAction.RECONCILE_REQUIRED
            for decision in self.decisions
        )

    @property
    def requires_manual_review(self) -> bool:
        return any(
            decision.action is ContinuationAction.MANUAL_REVIEW
            for decision in self.decisions
        )


class ContinuationPlanningError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ContinuationPlanningError(code)


_MANUAL_MISSION_STATES = frozenset(
    {
        MissionState.MISSION_CONFLICT,
        MissionState.BLOCKED,
        MissionState.MANUAL_REVIEW_REQUIRED,
    }
)

_EXECUTION_PLANNING_STATES = frozenset(
    {
        MissionState.READY_FOR_EXECUTION,
        MissionState.RECONCILING,
    }
)

_NON_EXECUTABLE_MISSION_STATES = frozenset(
    {
        MissionState.RECEIVED,
        MissionState.VALIDATED,
        MissionState.PERSISTED,
        MissionState.EXECUTING,
        MissionState.VERIFYING,
        MissionState.EXECUTION_UNKNOWN,
        MissionState.VERIFICATION_FAILED,
    }
)

_RECONCILE_EFFECT_STATES = frozenset(
    {
        EffectState.RESERVED,
        EffectState.SUBMITTED,
        EffectState.EXECUTION_UNKNOWN,
    }
)

_RECONCILE_ATTEMPT_STATES = frozenset(
    {
        ExecutionAttemptState.IN_FLIGHT,
        ExecutionAttemptState.PROVIDER_ACKNOWLEDGED,
        ExecutionAttemptState.EXECUTION_UNKNOWN,
    }
)


class ContinuationPlanner:
    """Classify every canonical effect exactly once without external actions."""

    def __init__(self, attempt_lookup: AttemptLookup) -> None:
        if not hasattr(attempt_lookup, "get"):
            _fail("INVALID_ATTEMPT_LOOKUP")
        self._attempt_lookup = attempt_lookup

    def plan(self, mission: object) -> MissionContinuationPlan:
        if not hasattr(mission, "record"):
            _fail("INVALID_MISSION")
        record = mission.record
        if not isinstance(getattr(record, "state", None), MissionState):
            _fail("INVALID_MISSION_STATE")
        mission_key = getattr(record, "mission_key", None)
        if not isinstance(mission_key, str) or not mission_key:
            _fail("INVALID_MISSION_KEY")
        effects = getattr(record, "effects", None)
        if not isinstance(effects, tuple) or not effects:
            _fail("INVALID_EFFECTS")

        seen_refs: set[str] = set()
        seen_ids: set[str] = set()
        decisions: list[EffectContinuationDecision] = []

        for effect in sorted(effects, key=lambda item: item.effect_ref):
            self._validate_effect(effect, mission_key, seen_refs, seen_ids)
            attempt = self._attempt_lookup.get(
                derive_attempt_id(effect.effect_id)
            )
            decisions.append(
                self._classify(
                    mission_key,
                    record.state,
                    effect,
                    attempt,
                )
            )

        result = self._build_plan(
            mission_key,
            record.state,
            tuple(decisions),
        )
        partition_total = (
            result.skipped_amount_base_units
            + result.executable_amount_base_units
            + result.unresolved_amount_base_units
            + result.manual_review_amount_base_units
        )
        if partition_total != result.total_amount_base_units:
            _fail("CONTINUATION_TOTAL_MISMATCH")
        if len(result.decisions) != len(effects):
            _fail("EFFECT_CLASSIFICATION_INCOMPLETE")
        return result

    @staticmethod
    def _validate_effect(
        effect: object,
        mission_key: str,
        seen_refs: set[str],
        seen_ids: set[str],
    ) -> None:
        effect_ref = getattr(effect, "effect_ref", None)
        effect_id = getattr(effect, "effect_id", None)
        amount = getattr(effect, "amount_base_units", None)
        state = getattr(effect, "state", None)
        effect_mission_key = getattr(effect, "mission_key", mission_key)

        if not isinstance(effect_ref, str) or not effect_ref:
            _fail("INVALID_EFFECT_REF")
        if not isinstance(effect_id, str) or not effect_id:
            _fail("INVALID_EFFECT_ID")
        if type(amount) is not int or amount < 1:
            _fail("INVALID_EFFECT_AMOUNT")
        if not isinstance(state, EffectState):
            _fail("INVALID_EFFECT_STATE")
        if effect_mission_key != mission_key:
            _fail("EFFECT_MISSION_KEY_MISMATCH")
        if effect_ref in seen_refs:
            _fail("DUPLICATE_EFFECT_REF")
        if effect_id in seen_ids:
            _fail("DUPLICATE_EFFECT_ID")
        seen_refs.add(effect_ref)
        seen_ids.add(effect_id)

    @staticmethod
    def _classify(
        mission_key: str,
        mission_state: MissionState,
        effect: object,
        attempt: object | None,
    ) -> EffectContinuationDecision:
        attempt_state = None
        if attempt is not None:
            if not hasattr(attempt, "record"):
                _fail("INVALID_ATTEMPT_RECORD")
            attempt_record = attempt.record
            if attempt_record.plan.effect_id != effect.effect_id:
                _fail("ATTEMPT_EFFECT_MISMATCH")
            if attempt_record.plan.mission_key != mission_key:
                _fail("ATTEMPT_MISSION_MISMATCH")
            attempt_state = attempt_record.state
            if not isinstance(attempt_state, ExecutionAttemptState):
                _fail("INVALID_ATTEMPT_STATE")

        if mission_state in _MANUAL_MISSION_STATES:
            return ContinuationPlanner._decision(
                effect,
                ContinuationAction.MANUAL_REVIEW,
                "MISSION_REQUIRES_MANUAL_REVIEW",
            )

        if (
            mission_state is MissionState.COMPLETED
            and effect.state is not EffectState.CHAIN_CONFIRMED
        ):
            return ContinuationPlanner._decision(
                effect,
                ContinuationAction.MANUAL_REVIEW,
                "COMPLETED_MISSION_EFFECT_CONTRADICTION",
            )

        if mission_state in _NON_EXECUTABLE_MISSION_STATES:
            if (
                effect.state is EffectState.CHAIN_CONFIRMED
                and attempt_state is ExecutionAttemptState.VERIFIED
            ):
                return ContinuationPlanner._decision(
                    effect,
                    ContinuationAction.SKIP_VERIFIED,
                    "EFFECT_ALREADY_VERIFIED",
                )
            if effect.state in {
                EffectState.FAILED_FINAL,
                EffectState.BLOCKED,
            }:
                return ContinuationPlanner._decision(
                    effect,
                    ContinuationAction.MANUAL_REVIEW,
                    "EFFECT_TERMINAL_FAILURE",
                )
            return ContinuationPlanner._decision(
                effect,
                ContinuationAction.RECONCILE_REQUIRED,
                "MISSION_NOT_READY_FOR_CONTINUATION",
            )

        if (
            mission_state not in _EXECUTION_PLANNING_STATES
            and mission_state is not MissionState.COMPLETED
        ):
            _fail("UNCLASSIFIED_MISSION_STATE")

        if effect.state is EffectState.CHAIN_CONFIRMED:
            if attempt_state is ExecutionAttemptState.VERIFIED:
                return ContinuationPlanner._decision(
                    effect,
                    ContinuationAction.SKIP_VERIFIED,
                    "EFFECT_ALREADY_VERIFIED",
                )
            if attempt_state in {
                ExecutionAttemptState.FAILED_FINAL,
                ExecutionAttemptState.BLOCKED,
            }:
                return ContinuationPlanner._decision(
                    effect,
                    ContinuationAction.MANUAL_REVIEW,
                    "CONFIRMED_EFFECT_ATTEMPT_CONTRADICTION",
                )
            return ContinuationPlanner._decision(
                effect,
                ContinuationAction.RECONCILE_REQUIRED,
                "CONFIRMED_EFFECT_ATTEMPT_NOT_VERIFIED",
            )

        if effect.state is EffectState.PLANNED:
            if (
                attempt is None
                or attempt_state is ExecutionAttemptState.PREPARED
            ):
                return ContinuationPlanner._decision(
                    effect,
                    ContinuationAction.EXECUTE_MISSING,
                    "PLANNED_EFFECT_NOT_DISPATCHED",
                )
            if attempt_state in _RECONCILE_ATTEMPT_STATES:
                return ContinuationPlanner._decision(
                    effect,
                    ContinuationAction.RECONCILE_REQUIRED,
                    "PLANNED_EFFECT_HAS_AMBIGUOUS_ATTEMPT",
                )
            return ContinuationPlanner._decision(
                effect,
                ContinuationAction.MANUAL_REVIEW,
                "PLANNED_EFFECT_ATTEMPT_CONTRADICTION",
            )

        if effect.state in _RECONCILE_EFFECT_STATES:
            if attempt_state in {
                ExecutionAttemptState.FAILED_FINAL,
                ExecutionAttemptState.BLOCKED,
            }:
                return ContinuationPlanner._decision(
                    effect,
                    ContinuationAction.MANUAL_REVIEW,
                    "ACTIVE_EFFECT_TERMINAL_ATTEMPT_CONTRADICTION",
                )
            return ContinuationPlanner._decision(
                effect,
                ContinuationAction.RECONCILE_REQUIRED,
                "EFFECT_EXECUTION_NOT_FINAL",
            )

        if effect.state in {
            EffectState.FAILED_FINAL,
            EffectState.BLOCKED,
        }:
            return ContinuationPlanner._decision(
                effect,
                ContinuationAction.MANUAL_REVIEW,
                "EFFECT_TERMINAL_FAILURE",
            )

        _fail("UNCLASSIFIED_EFFECT_STATE")

    @staticmethod
    def _decision(
        effect: object,
        action: ContinuationAction,
        reason_code: str,
    ) -> EffectContinuationDecision:
        return EffectContinuationDecision(
            effect_ref=effect.effect_ref,
            effect_id=effect.effect_id,
            amount_base_units=effect.amount_base_units,
            action=action,
            reason_code=reason_code,
        )

    @staticmethod
    def _build_plan(
        mission_key: str,
        mission_state: MissionState,
        decisions: tuple[EffectContinuationDecision, ...],
    ) -> MissionContinuationPlan:
        def amount_for(action: ContinuationAction) -> int:
            return sum(
                decision.amount_base_units
                for decision in decisions
                if decision.action is action
            )

        return MissionContinuationPlan(
            mission_key=mission_key,
            mission_state=mission_state,
            decisions=decisions,
            total_amount_base_units=sum(
                decision.amount_base_units
                for decision in decisions
            ),
            skipped_amount_base_units=amount_for(
                ContinuationAction.SKIP_VERIFIED
            ),
            executable_amount_base_units=amount_for(
                ContinuationAction.EXECUTE_MISSING
            ),
            unresolved_amount_base_units=amount_for(
                ContinuationAction.RECONCILE_REQUIRED
            ),
            manual_review_amount_base_units=amount_for(
                ContinuationAction.MANUAL_REVIEW
            ),
        )
