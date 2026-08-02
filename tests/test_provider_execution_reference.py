from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.application.execution_dispatch import (
    ExecutionDispatchError,
    ExecutionDispatchService,
    ExecutionPortOutcome,
)
from nexus_vector.application.provider_reference_port import (
    ProviderExecutionResult,
    ProviderReferencePersistingPort,
    ProviderReferencePortError,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    build_execution_attempt_plan,
    create_initial_execution_attempt,
)
from nexus_vector.domain.mission_models import EffectState, MissionState
from nexus_vector.domain.provider_execution_references import (
    ProviderExecutionReference,
    ProviderExecutionReferenceError,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_provider_execution_reference_store import (
    SQLiteProviderExecutionReferenceStore,
    SQLiteProviderExecutionReferenceStoreError,
)

T0 = datetime(2026, 8, 2, 18, 0, tzinfo=timezone.utc)
MISSION_KEY = "msn_" + "11" * 32
EFFECT_ID = "eff_" + "22" * 32
PROVIDER_REFERENCE = "kh-execution-123"


def make_plan(*, effect_id: str = EFFECT_ID):
    return build_execution_attempt_plan(
        mission_key=MISSION_KEY,
        effect_id=effect_id,
        provider_namespace="keeperhub.direct.v1",
        request_key="request-key-1",
        request_material={
            "chain_id": 84532,
            "recipient": "0x" + "33" * 20,
            "amount_base_units": 10,
        },
    )


@dataclass
class FakeEffect:
    effect_id: str = EFFECT_ID
    state: EffectState = EffectState.PLANNED


@dataclass
class FakeRecord:
    state: MissionState = MissionState.READY_FOR_EXECUTION
    effects: tuple[FakeEffect, ...] = field(default_factory=lambda: (FakeEffect(),))


@dataclass
class FakeStoredMission:
    record: FakeRecord = field(default_factory=FakeRecord)


class FakeMissionLookup:
    def get(self, mission_key: str):
        return FakeStoredMission() if mission_key == MISSION_KEY else None


class AcceptedProvider:
    def __init__(self, reference: str = PROVIDER_REFERENCE) -> None:
        self.reference = reference
        self.calls = 0

    def execute(self, attempt):
        self.calls += 1
        return ProviderExecutionResult(
            ExecutionPortOutcome.ACCEPTED,
            self.reference,
        )


class ProviderExecutionReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.attempt_db = root / "attempts.sqlite3"
        self.reference_db = root / "provider-references.sqlite3"
        self.attempt_store = SQLiteExecutionAttemptStore(self.attempt_db)
        self.reference_store = SQLiteProviderExecutionReferenceStore(
            self.reference_db
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def reference(self, *, attempt_id: str | None = None, value: str = PROVIDER_REFERENCE):
        plan = make_plan()
        return ProviderExecutionReference(
            attempt_id=attempt_id or plan.attempt_id,
            provider_namespace=plan.provider_namespace,
            request_fingerprint=plan.request_fingerprint,
            provider_reference=value,
            created_at_utc=T0,
        )

    def test_reference_model_is_strict_immutable_and_does_not_echo(self) -> None:
        value = self.reference()
        self.assertEqual(value.provider_reference, PROVIDER_REFERENCE)
        with self.assertRaises(ProviderExecutionReferenceError) as caught:
            self.reference(value=" bad-reference ")
        self.assertEqual(caught.exception.code, "INVALID_PROVIDER_REFERENCE")
        self.assertNotIn("bad-reference", str(caught.exception))
        with self.assertRaises(AttributeError):
            value.provider_reference = "changed"

    def test_store_round_trip_reopen_and_exact_duplicate_are_idempotent(self) -> None:
        self.reference_store.initialize()
        expected = self.reference_store.create(self.reference())
        duplicate = self.reference_store.create(self.reference())
        self.assertEqual(duplicate, expected)

        reopened = SQLiteProviderExecutionReferenceStore(self.reference_db)
        reopened.initialize()
        self.assertEqual(reopened.get(expected.attempt_id), expected)
        self.assertEqual(
            reopened.get_by_provider_reference(
                expected.provider_namespace,
                expected.provider_reference,
            ),
            expected,
        )

    def test_changed_reference_or_reused_provider_reference_conflicts(self) -> None:
        self.reference_store.initialize()
        first = self.reference_store.create(self.reference())
        with self.assertRaises(SQLiteProviderExecutionReferenceStoreError) as changed:
            self.reference_store.create(self.reference(value="different"))
        self.assertEqual(changed.exception.code, "PROVIDER_REFERENCE_CONFLICT")

        second_plan = make_plan(effect_id="eff_" + "44" * 32)
        second = ProviderExecutionReference(
            attempt_id=second_plan.attempt_id,
            provider_namespace=first.provider_namespace,
            request_fingerprint=second_plan.request_fingerprint,
            provider_reference=first.provider_reference,
            created_at_utc=T0,
        )
        with self.assertRaises(SQLiteProviderExecutionReferenceStoreError) as reused:
            self.reference_store.create(second)
        self.assertEqual(reused.exception.code, "PROVIDER_REFERENCE_CONFLICT")

    def test_incompatible_existing_database_fails_closed(self) -> None:
        with sqlite3.connect(self.reference_db) as connection:
            connection.execute("CREATE TABLE foreign_table (id INTEGER)")
        with self.assertRaises(SQLiteProviderExecutionReferenceStoreError) as caught:
            self.reference_store.initialize()
        self.assertEqual(caught.exception.code, "INCOMPATIBLE_SCHEMA")

    def test_dispatch_persists_provider_reference_before_acknowledgement(self) -> None:
        provider = AcceptedProvider()
        port = ProviderReferencePersistingPort(
            provider,
            self.reference_store,
            provider_namespace="keeperhub.direct.v1",
        )
        result = ExecutionDispatchService(
            FakeMissionLookup(),
            self.attempt_store,
        ).dispatch(make_plan(), port, T0)

        self.assertEqual(result.record.state, ExecutionAttemptState.PROVIDER_ACKNOWLEDGED)
        durable = self.reference_store.get(result.record.attempt_id)
        self.assertIsNotNone(durable)
        self.assertEqual(durable.provider_reference, PROVIDER_REFERENCE)
        self.assertEqual(provider.calls, 1)

    def test_crash_after_reference_write_before_ack_is_restart_safe(self) -> None:
        self.attempt_store.initialize()
        prepared = self.attempt_store.create(
            create_initial_execution_attempt(make_plan(), T0)
        )
        in_flight = self.attempt_store.transition(
            prepared.record.attempt_id,
            prepared.revision,
            ExecutionAttemptState.IN_FLIGHT,
            T0,
        )
        port = ProviderReferencePersistingPort(
            AcceptedProvider(),
            self.reference_store,
            provider_namespace="keeperhub.direct.v1",
        )
        port.execute(in_flight.record)

        reopened_attempts = SQLiteExecutionAttemptStore(self.attempt_db)
        reopened_attempts.initialize()
        reopened_references = SQLiteProviderExecutionReferenceStore(
            self.reference_db
        )
        reopened_references.initialize()
        attempt = reopened_attempts.get(in_flight.record.attempt_id)
        reference = reopened_references.get(in_flight.record.attempt_id)
        self.assertEqual(attempt.record.state, ExecutionAttemptState.IN_FLIGHT)
        self.assertEqual(reference.provider_reference, PROVIDER_REFERENCE)
        self.assertEqual(reopened_attempts.list_recovery_candidates(), (attempt,))

    def test_reference_persistence_failure_makes_attempt_unknown(self) -> None:
        with sqlite3.connect(self.reference_db) as connection:
            connection.execute("CREATE TABLE foreign_table (id INTEGER)")
        port = ProviderReferencePersistingPort(
            AcceptedProvider(),
            self.reference_store,
            provider_namespace="keeperhub.direct.v1",
        )
        with self.assertRaises(ExecutionDispatchError) as caught:
            ExecutionDispatchService(
                FakeMissionLookup(),
                self.attempt_store,
            ).dispatch(make_plan(), port, T0)
        self.assertEqual(caught.exception.code, "EXECUTION_OUTCOME_UNKNOWN")
        durable = self.attempt_store.get(make_plan().attempt_id)
        self.assertEqual(durable.record.state, ExecutionAttemptState.EXECUTION_UNKNOWN)

    def test_final_rejection_creates_no_provider_reference(self) -> None:
        class RejectedProvider:
            def execute(self, attempt):
                return ProviderExecutionResult(ExecutionPortOutcome.REJECTED_FINAL)

        port = ProviderReferencePersistingPort(
            RejectedProvider(),
            self.reference_store,
            provider_namespace="keeperhub.direct.v1",
        )
        result = ExecutionDispatchService(
            FakeMissionLookup(),
            self.attempt_store,
        ).dispatch(make_plan(), port, T0)
        self.assertEqual(result.record.state, ExecutionAttemptState.FAILED_FINAL)
        self.assertFalse(self.reference_db.exists())

    def test_missing_or_unexpected_reference_fails_before_journal_write(self) -> None:
        with self.assertRaises(ProviderReferencePortError) as missing:
            ProviderExecutionResult(ExecutionPortOutcome.ACCEPTED)
        self.assertEqual(missing.exception.code, "MISSING_PROVIDER_REFERENCE")
        with self.assertRaises(ProviderReferencePortError) as unexpected:
            ProviderExecutionResult(
                ExecutionPortOutcome.REJECTED_FINAL,
                PROVIDER_REFERENCE,
            )
        self.assertEqual(unexpected.exception.code, "UNEXPECTED_PROVIDER_REFERENCE")

    def test_modules_have_no_network_wallet_secret_or_process_capabilities(self) -> None:
        root = Path(__file__).parents[1] / "src" / "nexus_vector"
        forbidden = {
            "http", "urllib", "socket", "requests", "subprocess", "os",
            "secrets", "web3", "eth_account", "ccxt",
        }
        for relative in (
            Path("domain/provider_execution_references.py"),
            Path("persistence/sqlite_provider_execution_reference_store.py"),
            Path("application/provider_reference_port.py"),
        ):
            tree = ast.parse((root / relative).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(
                        alias.name.split(".", 1)[0] for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".", 1)[0])
            self.assertTrue(imported.isdisjoint(forbidden), (relative, imported & forbidden))


if __name__ == "__main__":
    unittest.main()
