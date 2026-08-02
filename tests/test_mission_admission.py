from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
            int(connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0]),
            int(connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0]),
        )


class FailingTransitionStore(SQLiteMissionStore):
    def transition_mission(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise SQLiteMissionStoreError("DATABASE_ERROR")


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


class MissionAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def service(self) -> tuple[MissionAdmissionService, SQLiteMissionStore, Path]:
        db_path = self.root / "missions.sqlite3"
        store = SQLiteMissionStore(db_path)
        return MissionAdmissionService(store), store, db_path

    def test_fresh_admission_is_atomic_and_reaches_persisted(self) -> None:
        admission, _, db_path = self.service()
        stored = admission.admit(make_request(), T0)

        self.assertIs(stored.record.state, MissionState.PERSISTED)
        self.assertEqual(stored.revision, 3)
        self.assertEqual(counts(db_path), (1, 2))
        self.assertEqual(
            tuple(effect.effect_ref for effect in stored.record.effects),
            ("recipient-a", "recipient-b"),
        )
        self.assertTrue(
            all(
                effect.mission_key == stored.record.mission_key
                for effect in stored.record.effects
            )
        )

    def test_duplicate_identical_admission_has_no_revision_churn(self) -> None:
        admission, _, db_path = self.service()
        first = admission.admit(make_request(), T0)
        second = admission.admit(make_request(), T0 + timedelta(hours=1))

        self.assertEqual(second, first)
        self.assertEqual(second.revision, 3)
        self.assertEqual(counts(db_path), (1, 2))

    def test_changed_content_conflicts_and_preserves_original(self) -> None:
        admission, store, db_path = self.service()
        first = admission.admit(make_request(), T0)

        with self.assertRaises(SQLiteMissionStoreError) as caught:
            admission.admit(
                make_request(second_amount=999),
                T0 + timedelta(minutes=1),
            )

        self.assertEqual(caught.exception.code, "MISSION_CONFLICT")
        self.assertEqual(store.get(first.record.mission_key), first)
        self.assertEqual(counts(db_path), (1, 2))

    def test_resume_received_without_recreating_effects(self) -> None:
        admission, store, db_path = self.service()
        initial = create_initial_mission_record(make_request(), T0)
        store.initialize()
        received = store.create(initial)
        self.assertIs(received.record.state, MissionState.RECEIVED)

        resumed = admission.admit(make_request(), T0 + timedelta(minutes=5))

        self.assertIs(resumed.record.state, MissionState.PERSISTED)
        self.assertEqual(resumed.revision, 3)
        self.assertEqual(counts(db_path), (1, 2))

    def test_resume_validated_uses_monotonic_timestamp(self) -> None:
        admission, store, db_path = self.service()
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

        self.assertIs(resumed.record.state, MissionState.PERSISTED)
        self.assertEqual(resumed.record.updated_at_utc, validated_at)
        self.assertEqual(resumed.revision, validated.revision + 1)
        self.assertEqual(counts(db_path), (1, 2))

    def test_existing_persisted_returns_unchanged(self) -> None:
        admission, _, _ = self.service()
        first = admission.admit(make_request(), T0)
        self.assertEqual(
            admission.admit(make_request(), T0 + timedelta(days=1)),
            first,
        )

    def test_existing_later_state_never_regresses(self) -> None:
        admission, store, _ = self.service()
        persisted = admission.admit(make_request(), T0)
        reconciling = store.transition_mission(
            persisted.record.mission_key,
            persisted.revision,
            MissionState.RECONCILING,
            T0 + timedelta(minutes=1),
        )

        returned = admission.admit(make_request(), T0 + timedelta(minutes=2))

        self.assertEqual(returned, reconciling)
        self.assertIs(returned.record.state, MissionState.RECONCILING)

    def test_blocked_before_persistence_is_not_reported_as_admitted(self) -> None:
        admission, store, _ = self.service()
        initial = create_initial_mission_record(make_request(), T0)
        store.initialize()
        received = store.create(initial)
        blocked = store.transition_mission(
            received.record.mission_key,
            received.revision,
            MissionState.BLOCKED,
            T0 + timedelta(minutes=1),
        )

        with self.assertRaises(MissionAdmissionError) as caught:
            admission.admit(make_request(), T0 + timedelta(minutes=2))

        self.assertEqual(caught.exception.code, "ADMISSION_NOT_PERSISTED")
        self.assertEqual(store.get(initial.mission_key), blocked)

    def test_invalid_timestamps_fail_before_database_creation(self) -> None:
        cases = (
            (datetime(2026, 8, 2, 7, 0), "INVALID_TIMESTAMP"),
            (
                datetime(
                    2026,
                    8,
                    2,
                    10,
                    0,
                    tzinfo=timezone(timedelta(hours=3)),
                ),
                "NON_UTC_TIMESTAMP",
            ),
        )
        for index, (invalid_time, expected_code) in enumerate(cases):
            with self.subTest(expected_code=expected_code):
                db_path = self.root / f"invalid-{index}.sqlite3"
                admission = MissionAdmissionService(SQLiteMissionStore(db_path))
                with self.assertRaises(MissionModelValidationError) as caught:
                    admission.admit(make_request(), invalid_time)
                self.assertEqual(caught.exception.code, expected_code)
                self.assertFalse(db_path.exists())

    def test_invalid_store_is_rejected(self) -> None:
        with self.assertRaises(MissionAdmissionError) as caught:
            MissionAdmissionService(object())  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, "INVALID_STORE")

    def test_transition_failure_never_reports_false_success(self) -> None:
        db_path = self.root / "failure.sqlite3"
        store = FailingTransitionStore(db_path)
        admission = MissionAdmissionService(store)

        with self.assertRaises(SQLiteMissionStoreError) as caught:
            admission.admit(make_request(), T0)

        self.assertEqual(caught.exception.code, "DATABASE_ERROR")
        durable = store.get(make_request().build_identity().mission_key)
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertIs(durable.record.state, MissionState.RECEIVED)
        self.assertEqual(durable.revision, 1)
        self.assertEqual(counts(db_path), (1, 2))

    def test_bounded_stale_revision_reread_resumes_safely(self) -> None:
        store = OneStaleRevisionStore(self.root / "stale.sqlite3")
        stored = MissionAdmissionService(store).admit(make_request(), T0)
        self.assertIs(stored.record.state, MissionState.PERSISTED)
        self.assertEqual(stored.revision, 3)

    def test_restart_with_new_store_instance_returns_same_record(self) -> None:
        db_path = self.root / "restart.sqlite3"
        first = MissionAdmissionService(SQLiteMissionStore(db_path)).admit(
            make_request(),
            T0,
        )
        second = MissionAdmissionService(SQLiteMissionStore(db_path)).admit(
            make_request(),
            T0 + timedelta(hours=2),
        )
        self.assertEqual(second, first)

    def test_effects_match_canonical_economic_identity(self) -> None:
        request = make_request()
        stored = MissionAdmissionService(
            SQLiteMissionStore(self.root / "effects.sqlite3")
        ).admit(request, T0)
        by_ref = {
            effect.effect_ref: effect
            for effect in stored.record.effects
        }

        for requested in request.effects:
            effect = by_ref[requested.effect_ref]
            self.assertEqual(effect.recipient, requested.recipient)
            self.assertEqual(
                effect.amount_base_units,
                requested.amount_base_units,
            )
            self.assertEqual(effect.chain_id, request.chain_id)
            self.assertEqual(
                effect.token_address,
                request.asset.token_address,
            )
            self.assertEqual(effect.token_decimals, request.asset.decimals)
            self.assertEqual(
                effect.effect_id,
                stored.record.effect_id_for(requested.effect_ref),
            )

    def test_application_module_has_no_external_action_imports(self) -> None:
        source_path = (
            Path(__file__).parents[1]
            / "src"
            / "nexus_vector"
            / "application"
            / "mission_admission.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        self.assertLessEqual(
            imported_roots,
            {"__future__", "datetime", "nexus_vector"},
        )
        self.assertFalse(
            {
                "os",
                "subprocess",
                "socket",
                "urllib",
                "http",
                "requests",
            }
            & imported_roots
        )


if __name__ == "__main__":
    unittest.main()
