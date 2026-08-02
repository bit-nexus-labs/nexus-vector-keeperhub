"""Deterministic read-only diagnosis for Mission execution state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from nexus_vector.application.continuation_planner import (
    ContinuationAction,
    EffectContinuationDecision,
    MissionContinuationPlan,
)


class ProviderObservationState(str, Enum):
    NOT_QUERIED = "NOT_QUERIED"
    NOT_FOUND = "NOT_FOUND"
    ACCEPTED = "ACCEPTED"
    REJECTED_FINAL = "REJECTED_FINAL"
    UNKNOWN = "UNKNOWN"


class ChainObservationState(str, Enum):
    NOT_QUERIED = "NOT_QUERIED"
    NOT_FOUND = "NOT_FOUND"
    CONFIRMED = "CONFIRMED"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


class DoctorAction(str, Enum):
    SKIP_VERIFIED = "SKIP_VERIFIED"
    EXECUTE_MISSING = "EXECUTE_MISSING"
    RECONCILE = "RECONCILE"
    WAIT_FOR_CONFIRMATIONS = "WAIT_FOR_CONFIRMATIONS"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class EffectObservation:
    effect_id: str
    provider_state: ProviderObservationState
    chain_state: ChainObservationState
    confirmations: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.effect_id, str) or not self.effect_id:
            _fail("INVALID_EFFECT_ID")
        if not isinstance(self.provider_state, ProviderObservationState):
            _fail("INVALID_PROVIDER_STATE")
        if not isinstance(self.chain_state, ChainObservationState):
            _fail("INVALID_CHAIN_STATE")
        if type(self.confirmations) is not int or self.confirmations < 0:
            _fail("INVALID_CONFIRMATIONS")
        if (
            self.chain_state is not ChainObservationState.CONFIRMED
            and self.confirmations != 0
        ):
            _fail("UNEXPECTED_CONFIRMATIONS")


@dataclass(frozen=True)
class EffectDiagnosis:
    effect_ref: str
    effect_id: str
    action: DoctorAction
    diagnosis_code: str


@dataclass(frozen=True)
class ExecutionDoctorReport:
    mission_key: str
    next_action: DoctorAction
    diagnoses: tuple[EffectDiagnosis, ...]
    total_amount_base_units: int
    skipped_amount_base_units: int
    executable_amount_base_units: int
    unresolved_amount_base_units: int
    manual_review_amount_base_units: int


class ExecutionDoctorError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ExecutionDoctorError(code)


class ExecutionDoctor:
    """Compare local continuation state with explicit sanitized observations."""

    def diagnose(
        self,
        plan: MissionContinuationPlan,
        observations: Mapping[str, EffectObservation],
        *,
        minimum_confirmations: int,
    ) -> ExecutionDoctorReport:
        if not isinstance(plan, MissionContinuationPlan):
            _fail("INVALID_CONTINUATION_PLAN")
        if not isinstance(observations, Mapping):
            _fail("INVALID_OBSERVATIONS")
        if type(minimum_confirmations) is not int or minimum_confirmations < 1:
            _fail("INVALID_MINIMUM_CONFIRMATIONS")
        if any(not isinstance(key, str) for key in observations):
            _fail("INVALID_OBSERVATION_KEY")

        decision_ids = {decision.effect_id for decision in plan.decisions}
        observation_ids = set(observations)
        if observation_ids != decision_ids:
            _fail("OBSERVATION_SET_MISMATCH")
        if any(
            not isinstance(observation, EffectObservation)
            or observation.effect_id != effect_id
            for effect_id, observation in observations.items()
        ):
            _fail("INVALID_OBSERVATION")

        diagnoses = tuple(
            self._diagnose_effect(
                decision,
                observations[decision.effect_id],
                minimum_confirmations,
            )
            for decision in plan.decisions
        )
        next_action = self._overall_action(diagnoses)
        return ExecutionDoctorReport(
            mission_key=plan.mission_key,
            next_action=next_action,
            diagnoses=diagnoses,
            total_amount_base_units=plan.total_amount_base_units,
            skipped_amount_base_units=plan.skipped_amount_base_units,
            executable_amount_base_units=plan.executable_amount_base_units,
            unresolved_amount_base_units=plan.unresolved_amount_base_units,
            manual_review_amount_base_units=plan.manual_review_amount_base_units,
        )

    @staticmethod
    def _diagnose_effect(
        decision: EffectContinuationDecision,
        observation: EffectObservation,
        minimum_confirmations: int,
    ) -> EffectDiagnosis:
        provider = observation.provider_state
        chain = observation.chain_state

        if chain is ChainObservationState.MISMATCH:
            return _diagnosis(
                decision,
                DoctorAction.MANUAL_REVIEW,
                "CHAIN_ECONOMIC_MISMATCH",
            )

        if decision.action is ContinuationAction.MANUAL_REVIEW:
            return _diagnosis(
                decision,
                DoctorAction.MANUAL_REVIEW,
                "LOCAL_STATE_REQUIRES_MANUAL_REVIEW",
            )

        if decision.action is ContinuationAction.SKIP_VERIFIED:
            if chain is ChainObservationState.NOT_FOUND:
                return _diagnosis(
                    decision,
                    DoctorAction.MANUAL_REVIEW,
                    "LOCAL_VERIFIED_CHAIN_NOT_FOUND",
                )
            if provider is ProviderObservationState.REJECTED_FINAL:
                return _diagnosis(
                    decision,
                    DoctorAction.MANUAL_REVIEW,
                    "LOCAL_VERIFIED_PROVIDER_REJECTED",
                )
            if (
                chain is ChainObservationState.CONFIRMED
                and observation.confirmations < minimum_confirmations
            ):
                return _diagnosis(
                    decision,
                    DoctorAction.WAIT_FOR_CONFIRMATIONS,
                    "VERIFIED_EFFECT_CONFIRMATIONS_BELOW_POLICY",
                )
            return _diagnosis(
                decision,
                DoctorAction.SKIP_VERIFIED,
                "LOCAL_VERIFIED_NEVER_RESEND",
            )

        if decision.action is ContinuationAction.RECONCILE_REQUIRED:
            if chain is ChainObservationState.CONFIRMED:
                if observation.confirmations < minimum_confirmations:
                    return _diagnosis(
                        decision,
                        DoctorAction.WAIT_FOR_CONFIRMATIONS,
                        "CHAIN_CONFIRMATIONS_BELOW_POLICY",
                    )
                return _diagnosis(
                    decision,
                    DoctorAction.RECONCILE,
                    "CHAIN_CONFIRMED_LOCAL_PROJECTION_PENDING",
                )
            if provider is ProviderObservationState.REJECTED_FINAL:
                return _diagnosis(
                    decision,
                    DoctorAction.MANUAL_REVIEW,
                    "PROVIDER_FINAL_LOCAL_ACTIVE_CONTRADICTION",
                )
            return _diagnosis(
                decision,
                DoctorAction.RECONCILE,
                "EXECUTION_OUTCOME_NOT_DURABLY_RESOLVED",
            )

        if decision.action is ContinuationAction.EXECUTE_MISSING:
            if chain is ChainObservationState.CONFIRMED:
                if observation.confirmations < minimum_confirmations:
                    return _diagnosis(
                        decision,
                        DoctorAction.WAIT_FOR_CONFIRMATIONS,
                        "UNEXPECTED_CHAIN_CONFIRMATION_BELOW_POLICY",
                    )
                return _diagnosis(
                    decision,
                    DoctorAction.RECONCILE,
                    "CHAIN_EFFECT_EXISTS_LOCAL_MISSING",
                )
            if provider in {
                ProviderObservationState.ACCEPTED,
                ProviderObservationState.UNKNOWN,
            }:
                return _diagnosis(
                    decision,
                    DoctorAction.RECONCILE,
                    "PROVIDER_EFFECT_MAY_EXIST_LOCAL_MISSING",
                )
            if chain is ChainObservationState.UNKNOWN:
                return _diagnosis(
                    decision,
                    DoctorAction.RECONCILE,
                    "CHAIN_STATE_UNKNOWN",
                )
            if provider is ProviderObservationState.REJECTED_FINAL:
                return _diagnosis(
                    decision,
                    DoctorAction.MANUAL_REVIEW,
                    "PROVIDER_REJECTION_WITHOUT_DURABLE_ATTEMPT",
                )
            return _diagnosis(
                decision,
                DoctorAction.EXECUTE_MISSING,
                "NO_DURABLE_OR_EXTERNAL_EXECUTION_EVIDENCE",
            )

        _fail("UNCLASSIFIED_CONTINUATION_ACTION")

    @staticmethod
    def _overall_action(
        diagnoses: tuple[EffectDiagnosis, ...],
    ) -> DoctorAction:
        if not diagnoses:
            _fail("EMPTY_DIAGNOSES")
        actions = {diagnosis.action for diagnosis in diagnoses}
        for action in (
            DoctorAction.MANUAL_REVIEW,
            DoctorAction.WAIT_FOR_CONFIRMATIONS,
            DoctorAction.RECONCILE,
            DoctorAction.EXECUTE_MISSING,
        ):
            if action in actions:
                return action
        if actions == {DoctorAction.SKIP_VERIFIED}:
            return DoctorAction.COMPLETE
        _fail("UNCLASSIFIED_REPORT_ACTION")


def _diagnosis(
    decision: EffectContinuationDecision,
    action: DoctorAction,
    code: str,
) -> EffectDiagnosis:
    return EffectDiagnosis(
        effect_ref=decision.effect_ref,
        effect_id=decision.effect_id,
        action=action,
        diagnosis_code=code,
    )
