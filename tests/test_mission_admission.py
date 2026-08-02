from __future__ import annotations

import ast
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nexus_vector.application import MissionAdmissionError, MissionAdmissionService
from nexus_vector.domain.mission_identity import SCHEMA_VERSION
from nexus_vector.domain.mission_models import (
    AssetSpec,
    EffectRequest,
    MissionModelValidationError,
    MissionRequest,
    MissionState,
    create_initial_mission_record,
)
from nexus_vector.persistence import (
    SQLiteMissionStore,
    SQLiteMissionStoreError,
)

T0 = datetime(2026, 8, 2, 7, 0, tzinfo=timezone.utc)
TOKEN = "0x" + "11" * 20
RECIPIENT_A = "0x" + "22" * 20
RECIPIENT_B = "0x" + "33" * 20


def make_request(*, second_amount: int = 200) -> MissionRequest:
    return MissionRequest(
        schema_version=SCHEMA_VERSION,
        mission_namespace="keeperhub-hackathon",
        mission_ref="mission-001",
        mission_type="MULTI_RECIPIENT_PAYMENT",
        chain_id=84532,
        asset=AssetSpec(token_address=TOKEN, decimals=6),
        effects=(
            EffectRequest(
                effect_ref="recipient-a",
                recipient=RECIPIENT_A,
                amount_base_units=100,
            ),
            EffectRequest(
                effect_ref="recipient-b",
                recipient=RECIPIENT_B,
                amount_base_units=second_amount,
            ),
        ),
    )


def counts(db_path: Path) -> tuple[int, int]:
    with sqlite3.connect(db_path) as connection:
        return (
            connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0],
        )


def service(tmp_path: Path) -> tuple[MissionAdmissionService, SQLiteMissionStore, Path]:
    db_path = tmp_path / "missions.sqlite3"
    store = SQLiteMissionStore(db_path)
    return MissionAdmissionService(store), store, db_path


def test_fresh_admission_is_atomic_and_reaches_persisted(tmp_path: Path) -> None:
    admission, _, db_path = service(tmp_path)
    stored = admission.admit(make_request(), T0)

    assert stored.record.state is MissionState.PERSISTED
    assert stored.revision == 3
    assert counts(db_path) == (1, 2)
    assert tuple(effect.effect_ref for effect in stored.record.effects) == (
        "recipient-a",
        "recipient-b",
    )
    assert all(effect.mission_key == stored.record.mission_key for effect in stored.record.effects)


def test_duplicate_identical_admission_has_no_revision_churn(tmp_path: Path) -> None:
    admission, _, db_path = service(tmp_path)
    first = admission.admit(make_request(), T0)
    second = admission.admit(make_request(), T0 + timedelta(hours=1))

    assert second == first
    assert second.revision == 3
    assert counts(db_path) == (1, 2)


def test_changed_content_same_mission_key_is_conflict_and_preserves_original(tmp_path: Path) -> None:
    admission, store, db_path = service(tmp_path)
    first = admission.admit(make_request(), T0)

    with pytest.raises(SQLiteMissionStoreError) as caught:
        admission.admit(make_request(second_amount=999), T0 + timedelta(minutes=1))

    assert caught.value.code == "MISSION_CONFLICT"
    assert store.get(first.record.mission_key) == first
    assert counts(db_path) == (1, 2)


def test_resume_received_without_recreating_effects(tmp_path: Path) -> None:
    admission, store, db_path = service(tmp_path)
    initial = create_initial_mission_record(make_request(), T0)
    store.initialize()
    received = store.create(initial)
    assert received.record.state is MissionState.RECEIVED

    resumed = admission.admit(make_request(), T0 + timedelta(minutes=5))

    assert resumed.record.state is MissionState.PERSISTED
    assert resumed.revision == 3
    assert counts(db_path) == (1, 2)


def test_resume_validated_with_older_requested_timestamp_is_monotonic(tmp_path: Path) -> None:
    admission, store, db_path = service(tmp_path)
    initial = create_initial_mission_record(make_request(), T0)
    store.initialize()
    received = store.create(initial)
    validated_at = T0 + timedelta(minutes=10)
    validated = store.transition_mission(
        received.record.mission_key,
        received.revision,
        MissionState.VALIDATED,
        validated_at,
    )

    resumed = admission.admit(make_request(), T0 + timedelta(minutes=1))

    assert resumed.record.state is MissionState.PERSISTED
    assert resumed.record.updated_at_utc == validated_at
    assert resumed.revision == validated.revision + 1
    assert counts(db_path) == (1, 2)


def test_existing_persisted_returns_unchanged(tmp_path: Path) -> None:
    admission, _, _ = service(tmp_path)
    first = admission.admit(make_request(), T0)
    assert admission.admit(make_request(), T0 + timedelta(days=1)) == first


def test_existing_later_state_never_regresses(tmp_path: Path) -> None:
    admission, store, _ = service(tmp_path)
    persisted = admission.admit(make_request(), T0)
    reconciling = store.transition_mission(
        persisted.record.mission_key,
        persisted.revision,
        MissionState.RECONCILING,
        T0 + timedelta(minutes=1),
    )

    returned = admission.admit(make_request(), T0 + timedelta(minutes=2))

    assert returned == reconciling
    assert returned.record.state is MissionState.RECONCILING


def test_blocked_before_persistence_is_not_reported_as_admitted(tmp_path: Path) -> None:
    admission, store, _ = service(tmp_path)
    initial = create_initial_mission_record(make_request(), T0)
    store.initialize()
    received = store.create(initial)
    blocked = store.transition_mission(
        received.record.mission_key,
        received.revision,
        MissionState.BLOCKED,
        T0 + timedelta(minutes=1),
    )

    with pytest.raises(MissionAdmissionError) as caught:
        admission.admit(make_request(), T0 + timedelta(minutes=2))

    assert caught.value.code == "ADMISSION_NOT_PERSISTED"
    assert store.get(initial.mission_key) == blocked


@pytest.mark.parametrize(
    "invalid_time, expected_code",
    [
        (datetime(2026, 8, 2, 7, 0), "INVALID_TIMESTAMP"),
        (
            datetime(2026, 8, 2, 10, 0, tzinfo=timezone(timedelta(hours=3))),
            "NON_UTC_TIMESTAMP",
        ),
    ],
)
def test_invalid_timestamp_fails_before_database_creation(
    tmp_path: Path,
    invalid_time: datetime,
    expected_code: str,
) -> None:
    admission, _, db_path = service(tmp_path)

    with pytest.raises(MissionModelValidationError) as caught:
        admission.admit(make_request(), invalid_time)

    assert caught.value.code == expected_code
    assert not db_path.exists()


def test_invalid_store_is_rejected() -> None:
    with pytest.raises(MissionAdmissionError) as caught:
        MissionAdmissionService(object())  # type: ignore[arg-type]
    assert caught.value.code == "INVALID_STORE"


class FailingTransitionStore(SQLiteMissionStore):
    def transition_mission(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise SQLiteMissionStoreError("DATABASE_ERROR")


def test_transition_failure_never_reports_false_success(tmp_path: Path) -> None:
    db_path = tmp_path / "failure.sqlite3"
    store = FailingTransitionStore(db_path)
    admission = MissionAdmissionService(store)

    with pytest.raises(SQLiteMissionStoreError) as caught:
        admission.admit(make_request(), T0)

    assert caught.value.code == "DATABASE_ERROR"
    durable = store.get(make_request().build_identity().mission_key)
    assert durable is not None
    assert durable.record.state is MissionState.RECEIVED
    assert durable.revision == 1
    assert counts(db_path) == (1, 2)


class OneStaleRevisionStore(SQLiteMissionStore):
    def __init__(self, database_path: Path) -> None:
        super().__init__(database_path)
        self._raised = False

    def transition_mission(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not self._raised:
            self._raised = True
            super().transition_mission(*args, **kwargs)
            raise SQLiteMissionStoreError("STALE_REVISION")
        return super().transition_mission(*args, **kwargs)


def test_bounded_stale_revision_reread_resumes_safely(tmp_path: Path) -> None:
    store = OneStaleRevisionStore(tmp_path / "stale.sqlite3")
    stored = MissionAdmissionService(store).admit(make_request(), T0)
    assert stored.record.state is MissionState.PERSISTED
    assert stored.revision == 3


def test_restart_with_new_store_instance_returns_same_durable_record(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.sqlite3"
    first = MissionAdmissionService(SQLiteMissionStore(db_path)).admit(make_request(), T0)
    second = MissionAdmissionService(SQLiteMissionStore(db_path)).admit(
        make_request(),
        T0 + timedelta(hours=2),
    )
    assert second == first


def test_effects_match_canonical_economic_identity(tmp_path: Path) -> None:
    request = make_request()
    stored = MissionAdmissionService(SQLiteMissionStore(tmp_path / "effects.sqlite3")).admit(request, T0)
    by_ref = {effect.effect_ref: effect for effect in stored.record.effects}

    for requested in request.effects:
        effect = by_ref[requested.effect_ref]
        assert effect.recipient == requested.recipient
        assert effect.amount_base_units == requested.amount_base_units
        assert effect.chain_id == request.chain_id
        assert effect.token_address == request.asset.token_address
        assert effect.token_decimals == request.asset.decimals
        assert effect.effect_id == stored.record.effect_id_for(requested.effect_ref)


def test_application_module_has_no_external_action_imports() -> None:
    source_path = Path(__file__).parents[1] / "src/nexus_vector/application/mission_admission.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots <= {"__future__", "datetime", "nexus_vector"}
    assert not ({"os", "subprocess", "socket", "urllib", "http", "requests"} & imported_roots)
