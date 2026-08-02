"""Fail-closed execution dispatch boundary with durable pre-call journaling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptPlan,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    create_initial_execution_attempt,
)
from nexus_vector.domain.mission_models import EffectState, MissionState
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
    SQLiteExecutionAttemptStoreError,
    StoredExecutionAttempt,
)


class ExecutionPortOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED_FINAL = "REJECTED_FINAL"


@dataclass(frozen=True)
class ExecutionPortResult:
    outcome: ExecutionPortOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ExecutionPortOutcome):
            _fail("INVALID_PORT_RESULT")


class ExecutionPort(Protocol):
    def execute(self, attempt: ExecutionAttemptRecord) -> ExecutionPortResult: ...


class MissionLookup(Protocol):
    def get(self, mission_key: str): ...


class ExecutionDispatchError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ExecutionDispatchError(code)


class ExecutionDispatchService:
    """Invoke a provider-neutral port only after durable IN_FLIGHT state."""

    def __init__(
        self,
        mission_lookup: MissionLookup,
        attempt_store: SQLiteExecutionAttemptStore,
    ) -> None:
        if not hasattr(mission_lookup, "get"):
            _fail("INVALID_MISSION_LOOKUP")
        if not isinstance(attempt_store, SQLiteExecutionAttemptStore):
            _fail("INVALID_ATTEMPT_STORE")
        self._mission_lookup = mission_lookup
        self._attempt_store = attempt_store

    def dispatch(
        self,
        plan: ExecutionAttemptPlan,
        port: ExecutionPort,
        dispatched_at_utc: datetime,
    ) -> StoredExecutionAttempt:
        if not isinstance(plan, ExecutionAttemptPlan):
            _fail("INVALID_ATTEMPT_PLAN")
        if not hasattr(port, "execute"):
            _fail("INVALID_EXECUTION_PORT")
        mission = self._mission_lookup.get(plan.mission_key)
        if mission is None:
            _fail("MISSION_NOT_FOUND")
        if mission.record.state is not MissionState.READY_FOR_EXECUTION:
            _fail("MISSION_NOT_READY_FOR_EXECUTION")
        effect = next(
            (
                item
                for item in mission.record.effects
                if item.effect_id == plan.effect_id
            ),
            None,
        )
        if effect is None:
            _fail("EFFECT_NOT_FOUND")
        if effect.state is not EffectState.PLANNED:
            _fail("EFFECT_NOT_DISPATCHABLE")

        self._attempt_store.initialize()
        current = self._attempt_store.create(
            create_initial_execution_attempt(plan, dispatched_at_utc)
        )
        if current.record.state is not ExecutionAttemptState.PREPARED:
            _fail("RECONCILIATION_REQUIRED")

        try:
            in_flight = self._attempt_store.transition(
                current.record.attempt_id,
                current.revision,
                ExecutionAttemptState.IN_FLIGHT,
                dispatched_at_utc,
            )
        except SQLiteExecutionAttemptStoreError as error:
            if error.code == "STALE_REVISION":
                _fail("RECONCILIATION_REQUIRED")
            raise

        try:
            result = port.execute(in_flight.record)
            if not isinstance(result, ExecutionPortResult):
                raise TypeError("invalid port result")
            target = (
                ExecutionAttemptState.PROVIDER_ACKNOWLEDGED
                if result.outcome is ExecutionPortOutcome.ACCEPTED
                else ExecutionAttemptState.FAILED_FINAL
            )
            return self._attempt_store.transition(
                in_flight.record.attempt_id,
                in_flight.revision,
                target,
                dispatched_at_utc,
            )
        except Exception:
            self._mark_unknown_best_effort(in_flight, dispatched_at_utc)
            _fail("EXECUTION_OUTCOME_UNKNOWN")

    def _mark_unknown_best_effort(
        self,
        in_flight: StoredExecutionAttempt,
        updated_at_utc: datetime,
    ) -> None:
        try:
            self._attempt_store.transition(
                in_flight.record.attempt_id,
                in_flight.revision,
                ExecutionAttemptState.EXECUTION_UNKNOWN,
                updated_at_utc,
            )
        except SQLiteExecutionAttemptStoreError:
            # IN_FLIGHT is already a durable recovery state. Never mask the
            # original ambiguity with a false FAILED classification.
            return
