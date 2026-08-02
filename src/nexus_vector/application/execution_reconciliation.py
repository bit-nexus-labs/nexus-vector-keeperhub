"""Independent, fail-closed recovery for ambiguous execution attempts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol

from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptRecord,
    ExecutionAttemptState,
)
from nexus_vector.domain.mission_models import EffectState, MissionState
from nexus_vector.domain.verification_evidence import (
    ObservedTransfer,
    VerificationObservation,
    VerificationObservationStatus,
    derive_evidence_fingerprint,
    normalize_expected_sender,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
    SQLiteExecutionAttemptStoreError,
    StoredExecutionAttempt,
)


class IndependentVerificationPort(Protocol):
    def observe(self, attempt: ExecutionAttemptRecord) -> VerificationObservation: ...


class ReconciliationOutcome(str, Enum):
    VERIFIED = "VERIFIED"
    UNRESOLVED = "UNRESOLVED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ReconciliationResult:
    outcome: ReconciliationOutcome
    attempt: StoredExecutionAttempt
    mission: object
    evidence_fingerprint: str | None = None


class ExecutionReconciliationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ExecutionReconciliationError(code)


def _utc_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        _fail("INVALID_TIMESTAMP")
    if offset != timedelta(0):
        _fail("NON_UTC_TIMESTAMP")
    return value


class ExecutionReconciliationService:
    """Reconcile from independent evidence without ever resending an effect."""

    _MAX_CAS_STEPS = 12

    def __init__(
        self,
        mission_store: object,
        attempt_store: SQLiteExecutionAttemptStore,
    ) -> None:
        for method in ("get", "transition_mission", "transition_effect"):
            if not hasattr(mission_store, method):
                _fail("INVALID_MISSION_STORE")
        if not isinstance(attempt_store, SQLiteExecutionAttemptStore):
            _fail("INVALID_ATTEMPT_STORE")
        self._mission_store = mission_store
        self._attempt_store = attempt_store

    def reconcile(
        self,
        *,
        attempt_id: str,
        expected_sender: str,
        minimum_confirmations: int,
        verifier: IndependentVerificationPort,
        observed_at_utc: datetime,
    ) -> ReconciliationResult:
        sender = normalize_expected_sender(expected_sender)
        timestamp = _utc_timestamp(observed_at_utc)
        if type(minimum_confirmations) is not int or minimum_confirmations < 1:
            _fail("INVALID_MINIMUM_CONFIRMATIONS")
        if not hasattr(verifier, "observe"):
            _fail("INVALID_VERIFICATION_PORT")

        attempt = self._attempt_store.get(attempt_id)
        if attempt is None:
            _fail("ATTEMPT_NOT_FOUND")
        mission = self._mission_store.get(attempt.record.plan.mission_key)
        if mission is None:
            _fail("MISSION_NOT_FOUND")
        effect = self._find_effect(mission, attempt.record.plan.effect_id)

        if (
            attempt.record.state is ExecutionAttemptState.VERIFIED
            and effect.state is EffectState.CHAIN_CONFIRMED
        ):
            return ReconciliationResult(
                outcome=ReconciliationOutcome.VERIFIED,
                attempt=attempt,
                mission=mission,
            )

        try:
            observation = verifier.observe(attempt.record)
            if not isinstance(observation, VerificationObservation):
                raise TypeError("invalid verification observation")
        except Exception:
            attempt = self._mark_unknown(attempt, timestamp)
            _fail("VERIFICATION_OUTCOME_UNKNOWN")

        if observation.status in {
            VerificationObservationStatus.NOT_FOUND,
            VerificationObservationStatus.AMBIGUOUS,
        }:
            attempt = self._mark_unknown(attempt, timestamp)
            return ReconciliationResult(
                outcome=ReconciliationOutcome.UNRESOLVED,
                attempt=attempt,
                mission=mission,
            )

        transfer = observation.transfer
        if not isinstance(transfer, ObservedTransfer):
            attempt = self._mark_unknown(attempt, timestamp)
            _fail("VERIFICATION_OUTCOME_UNKNOWN")

        if not self._economic_match(effect, sender, transfer):
            attempt = self._block_attempt(attempt, timestamp)
            mission = self._mark_manual_review(mission, timestamp)
            return ReconciliationResult(
                outcome=ReconciliationOutcome.BLOCKED,
                attempt=attempt,
                mission=mission,
            )

        if transfer.confirmations < minimum_confirmations:
            attempt = self._mark_unknown(attempt, timestamp)
            return ReconciliationResult(
                outcome=ReconciliationOutcome.UNRESOLVED,
                attempt=attempt,
                mission=mission,
            )

        if attempt.record.state in {
            ExecutionAttemptState.FAILED_FINAL,
            ExecutionAttemptState.BLOCKED,
        }:
            mission = self._mark_manual_review(mission, timestamp)
            return ReconciliationResult(
                outcome=ReconciliationOutcome.BLOCKED,
                attempt=attempt,
                mission=mission,
            )

        # Safety ordering across independent durable stores:
        # project the verified economic fact into Mission state first. If the
        # process crashes before the attempt becomes VERIFIED, restart sees a
        # recovery candidate but dispatch still cannot duplicate a confirmed
        # Effect. Re-observation then finishes the attempt projection.
        mission = self._project_confirmed_effect(
            mission,
            effect.effect_ref,
            timestamp,
        )
        attempt = self._mark_verified(attempt, timestamp)
        return ReconciliationResult(
            outcome=ReconciliationOutcome.VERIFIED,
            attempt=attempt,
            mission=mission,
            evidence_fingerprint=derive_evidence_fingerprint(transfer),
        )

    @staticmethod
    def _find_effect(mission: object, effect_id: str):
        effect = next(
            (
                item
                for item in mission.record.effects
                if item.effect_id == effect_id
            ),
            None,
        )
        if effect is None:
            _fail("EFFECT_NOT_FOUND")
        return effect

    @staticmethod
    def _economic_match(
        effect: object,
        expected_sender: str,
        transfer: ObservedTransfer,
    ) -> bool:
        return (
            transfer.chain_id == effect.chain_id
            and transfer.token_address == effect.token_address
            and transfer.sender == expected_sender
            and transfer.recipient == effect.recipient
            and transfer.amount_base_units == effect.amount_base_units
        )

    def _mark_unknown(
        self,
        attempt: StoredExecutionAttempt,
        timestamp: datetime,
    ) -> StoredExecutionAttempt:
        if attempt.record.state in {
            ExecutionAttemptState.PREPARED,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
            ExecutionAttemptState.VERIFIED,
            ExecutionAttemptState.FAILED_FINAL,
            ExecutionAttemptState.BLOCKED,
        }:
            return attempt
        return self._transition_attempt(
            attempt,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
            timestamp,
        )

    def _mark_verified(
        self,
        attempt: StoredExecutionAttempt,
        timestamp: datetime,
    ) -> StoredExecutionAttempt:
        if attempt.record.state is ExecutionAttemptState.VERIFIED:
            return attempt
        if attempt.record.state in {
            ExecutionAttemptState.FAILED_FINAL,
            ExecutionAttemptState.BLOCKED,
        }:
            _fail("ATTEMPT_TERMINAL_CONTRADICTION")
        return self._transition_attempt(
            attempt,
            ExecutionAttemptState.VERIFIED,
            timestamp,
        )

    def _block_attempt(
        self,
        attempt: StoredExecutionAttempt,
        timestamp: datetime,
    ) -> StoredExecutionAttempt:
        if attempt.record.state is ExecutionAttemptState.BLOCKED:
            return attempt
        if attempt.record.state in {
            ExecutionAttemptState.VERIFIED,
            ExecutionAttemptState.FAILED_FINAL,
        }:
            return attempt
        return self._transition_attempt(
            attempt,
            ExecutionAttemptState.BLOCKED,
            timestamp,
        )

    def _transition_attempt(
        self,
        current: StoredExecutionAttempt,
        target: ExecutionAttemptState,
        timestamp: datetime,
    ) -> StoredExecutionAttempt:
        for _ in range(self._MAX_CAS_STEPS):
            if current.record.state is target:
                return current
            transition_at = max(timestamp, current.record.updated_at_utc)
            try:
                return self._attempt_store.transition(
                    current.record.attempt_id,
                    current.revision,
                    target,
                    transition_at,
                )
            except SQLiteExecutionAttemptStoreError as error:
                if error.code != "STALE_REVISION":
                    raise
                reread = self._attempt_store.get(current.record.attempt_id)
                if reread is None:
                    _fail("ATTEMPT_NOT_FOUND")
                current = reread
        _fail("ATTEMPT_CAS_EXHAUSTED")

    def _project_confirmed_effect(
        self,
        mission: object,
        effect_ref: str,
        timestamp: datetime,
    ) -> object:
        mission = self._ensure_reconciling(mission, timestamp)
        for _ in range(self._MAX_CAS_STEPS):
            effect = next(
                item
                for item in mission.record.effects
                if item.effect_ref == effect_ref
            )
            if effect.state is EffectState.CHAIN_CONFIRMED:
                break
            if effect.state is EffectState.PLANNED:
                target = EffectState.RESERVED
            elif effect.state is EffectState.RESERVED:
                target = EffectState.SUBMITTED
            elif effect.state in {
                EffectState.SUBMITTED,
                EffectState.EXECUTION_UNKNOWN,
            }:
                target = EffectState.CHAIN_CONFIRMED
            else:
                mission = self._mark_manual_review(mission, timestamp)
                _fail("EFFECT_TERMINAL_CONTRADICTION")
            mission = self._transition_effect(
                mission,
                effect_ref,
                target,
                timestamp,
            )
        else:
            _fail("EFFECT_CAS_EXHAUSTED")

        states = {item.state for item in mission.record.effects}
        if states == {EffectState.CHAIN_CONFIRMED}:
            return self._transition_mission(
                mission,
                MissionState.COMPLETED,
                timestamp,
            )
        if states & {EffectState.FAILED_FINAL, EffectState.BLOCKED}:
            return self._mark_manual_review(mission, timestamp)
        if states & {
            EffectState.RESERVED,
            EffectState.SUBMITTED,
            EffectState.EXECUTION_UNKNOWN,
        }:
            return mission
        if states <= {EffectState.PLANNED, EffectState.CHAIN_CONFIRMED}:
            return self._transition_mission(
                mission,
                MissionState.READY_FOR_EXECUTION,
                timestamp,
            )
        _fail("UNCLASSIFIED_EFFECT_STATE")

    def _ensure_reconciling(
        self,
        mission: object,
        timestamp: datetime,
    ) -> object:
        if mission.record.state is MissionState.RECONCILING:
            return mission
        if mission.record.state is MissionState.COMPLETED:
            return mission
        allowed_sources = {
            MissionState.PERSISTED,
            MissionState.READY_FOR_EXECUTION,
            MissionState.EXECUTING,
            MissionState.VERIFYING,
            MissionState.EXECUTION_UNKNOWN,
            MissionState.VERIFICATION_FAILED,
        }
        if mission.record.state not in allowed_sources:
            _fail("MISSION_NOT_RECONCILABLE")
        return self._transition_mission(
            mission,
            MissionState.RECONCILING,
            timestamp,
        )

    def _mark_manual_review(
        self,
        mission: object,
        timestamp: datetime,
    ) -> object:
        if mission.record.state is MissionState.MANUAL_REVIEW_REQUIRED:
            return mission
        mission = self._ensure_reconciling(mission, timestamp)
        if mission.record.state is MissionState.COMPLETED:
            return mission
        return self._transition_mission(
            mission,
            MissionState.MANUAL_REVIEW_REQUIRED,
            timestamp,
        )

    def _transition_mission(
        self,
        current: object,
        target: MissionState,
        timestamp: datetime,
    ) -> object:
        for _ in range(self._MAX_CAS_STEPS):
            if current.record.state is target:
                return current
            transition_at = max(timestamp, current.record.updated_at_utc)
            try:
                return self._mission_store.transition_mission(
                    current.record.mission_key,
                    current.revision,
                    target,
                    transition_at,
                )
            except Exception as error:
                if getattr(error, "code", None) != "STALE_REVISION":
                    raise
                reread = self._mission_store.get(current.record.mission_key)
                if reread is None:
                    _fail("MISSION_NOT_FOUND")
                current = reread
        _fail("MISSION_CAS_EXHAUSTED")

    def _transition_effect(
        self,
        current: object,
        effect_ref: str,
        target: EffectState,
        timestamp: datetime,
    ) -> object:
        for _ in range(self._MAX_CAS_STEPS):
            effect = next(
                item
                for item in current.record.effects
                if item.effect_ref == effect_ref
            )
            if effect.state is target:
                return current
            transition_at = max(timestamp, current.record.updated_at_utc)
            try:
                return self._mission_store.transition_effect(
                    current.record.mission_key,
                    effect_ref,
                    current.revision,
                    target,
                    transition_at,
                )
            except Exception as error:
                if getattr(error, "code", None) != "STALE_REVISION":
                    raise
                reread = self._mission_store.get(current.record.mission_key)
                if reread is None:
                    _fail("MISSION_NOT_FOUND")
                current = reread
        _fail("EFFECT_CAS_EXHAUSTED")
