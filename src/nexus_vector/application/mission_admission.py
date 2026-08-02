"""Durable, provider-neutral Mission admission boundary."""

from __future__ import annotations

from datetime import datetime

from nexus_vector.domain.mission_models import (
    MissionRequest,
    MissionState,
    create_initial_mission_record,
)
from nexus_vector.persistence.sqlite_mission_store import (
    SQLiteMissionStore,
    SQLiteMissionStoreError,
    StoredMission,
)


class MissionAdmissionError(RuntimeError):
    """Machine-classifiable admission failure with no request-data echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise MissionAdmissionError(code)


class MissionAdmissionService:
    """Persist one Mission aggregate before any execution boundary."""

    _MAX_CAS_STEPS = 4
    _ADMITTED_STATES = frozenset(
        {
            MissionState.PERSISTED,
            MissionState.RECONCILING,
            MissionState.READY_FOR_EXECUTION,
            MissionState.EXECUTING,
            MissionState.VERIFYING,
            MissionState.COMPLETED,
            MissionState.EXECUTION_UNKNOWN,
            MissionState.VERIFICATION_FAILED,
            MissionState.MANUAL_REVIEW_REQUIRED,
        }
    )

    def __init__(self, store: SQLiteMissionStore) -> None:
        if not isinstance(store, SQLiteMissionStore):
            _fail("INVALID_STORE")
        self._store = store

    def admit(
        self,
        request: MissionRequest,
        admitted_at_utc: datetime,
    ) -> StoredMission:
        """Durably admit or resume one Mission without execution authority."""

        initial = create_initial_mission_record(request, admitted_at_utc)
        self._store.initialize()
        current = self._store.create(initial)

        for _ in range(self._MAX_CAS_STEPS):
            state = current.record.state
            if state is MissionState.RECEIVED:
                target_state = MissionState.VALIDATED
            elif state is MissionState.VALIDATED:
                target_state = MissionState.PERSISTED
            else:
                break

            transition_at = max(
                admitted_at_utc,
                current.record.updated_at_utc,
            )
            try:
                current = self._store.transition_mission(
                    current.record.mission_key,
                    current.revision,
                    target_state,
                    transition_at,
                )
            except SQLiteMissionStoreError as error:
                if error.code != "STALE_REVISION":
                    raise
                reread = self._store.get(initial.mission_key)
                if reread is None:
                    _fail("MISSION_NOT_FOUND")
                current = reread
        else:
            _fail("ADMISSION_CAS_EXHAUSTED")

        final = self._store.get(initial.mission_key)
        if final is None:
            _fail("MISSION_NOT_FOUND")
        if final.record.content_fingerprint != initial.content_fingerprint:
            _fail("MISSION_CONFLICT")
        if final.record.state not in self._ADMITTED_STATES:
            _fail("ADMISSION_NOT_PERSISTED")
        return final
