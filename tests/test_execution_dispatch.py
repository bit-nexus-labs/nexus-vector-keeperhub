from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.application.execution_dispatch import (
    ExecutionDispatchError,
    ExecutionDispatchService,
    ExecutionPortOutcome,
    ExecutionPortResult,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptError,
    ExecutionAttemptState,
    build_execution_attempt_plan,
    create_initial_execution_attempt,
)
from nexus_vector.domain.mission_models import EffectState, MissionState
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
    SQLiteExecutionAttemptStoreError,
)

T0 = datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc)
MISSION_KEY = "msn_" + "11" * 32
EFFECT_ID = "eff_" + "22" * 32


def make_plan(*, request_key: str = "req-1", amount: int = 10):
    return build_execution_attempt_plan(
        mission_key=MISSION_KEY,
        effect_id=EFFECT_ID,
        provider_namespace="keeperhub.direct.v1",
        request_key=request_key,
        request_material={
            "chain_id": 84532,
            "recipient": "0x" + "33" * 20,
            "amount_base_units": amount,
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
    def __init__(self, mission: FakeStoredMission | None = None, *, missing: bool = False):
        self.mission = None if missing else (mission or FakeStoredMission())

    def get(self, mission_key: str):
        return self.mission if mission_key == MISSION_KEY else None


class InspectingPort:
    def __init__(self, store: SQLiteExecutionAttemptStore, *, result=None, error=None):
        self.store = store
        self.result = result or ExecutionPortResult(ExecutionPortOutcome.ACCEPTED)
        self.error = error
        self.calls = 0

    def execute(self, attempt):
        self.calls += 1
        durable = self.store.get(attempt.attempt_id)
        assert durable is not None
        assert durable.record.state is ExecutionAttemptState.IN_FLIGHT
        if self.error is not None:
            raise self.error
        return self.result


class ExecutionAttemptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "execution.sqlite3"
        self.store = SQLiteExecutionAttemptStore(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_fingerprint_is_deterministic_and_changed_content_conflicts(self):
        first = make_plan()
        same = make_plan()
        changed = make_plan(amount=11)
        self.assertEqual(first, same)
        self.assertEqual(first.attempt_id, changed.attempt_id)
        self.assertNotEqual(first.request_fingerprint, changed.request_fingerprint)

        self.store.initialize()
        created = self.store.create(create_initial_execution_attempt(first, T0))
        duplicate = self.store.create(create_initial_execution_attempt(same, T0))
        self.assertEqual(created, duplicate)
        with self.assertRaises(SQLiteExecutionAttemptStoreError) as caught:
            self.store.create(create_initial_execution_attempt(changed, T0))
        self.assertEqual(caught.exception.code, "EXECUTION_ATTEMPT_CONFLICT")

    def test_float_request_material_is_rejected(self):
        with self.assertRaises(ExecutionAttemptError) as caught:
            build_execution_attempt_plan(
                mission_key=MISSION_KEY,
                effect_id=EFFECT_ID,
                provider_namespace="keeperhub.direct.v1",
                request_key="req-1",
                request_material={"amount": 1.5},
            )
        self.assertEqual(caught.exception.code, "INVALID_REQUEST_MATERIAL")

    def test_store_persists_cas_and_restart_candidates(self):
        self.store.initialize()
        prepared = self.store.create(create_initial_execution_attempt(make_plan(), T0))
        in_flight = self.store.transition(
            prepared.record.attempt_id,
            prepared.revision,
            ExecutionAttemptState.IN_FLIGHT,
            T0,
        )
        self.assertEqual(in_flight.revision, 2)
        reopened = SQLiteExecutionAttemptStore(self.db)
        reopened.initialize()
        self.assertEqual(reopened.get(in_flight.record.attempt_id), in_flight)
        self.assertEqual(reopened.list_recovery_candidates(), (in_flight,))
        with self.assertRaises(SQLiteExecutionAttemptStoreError) as caught:
            reopened.transition(
                in_flight.record.attempt_id,
                1,
                ExecutionAttemptState.PROVIDER_ACKNOWLEDGED,
                T0,
            )
        self.assertEqual(caught.exception.code, "STALE_REVISION")

    def test_dispatch_persists_in_flight_before_port_call(self):
        port = InspectingPort(self.store)
        service = ExecutionDispatchService(FakeMissionLookup(), self.store)
        result = service.dispatch(make_plan(), port, T0)
        self.assertEqual(port.calls, 1)
        self.assertEqual(result.record.state, ExecutionAttemptState.PROVIDER_ACKNOWLEDGED)
        self.assertEqual(result.revision, 3)

    def test_unknown_exception_is_durable_and_never_blindly_retried(self):
        port = InspectingPort(self.store, error=TimeoutError("lost response"))
        service = ExecutionDispatchService(FakeMissionLookup(), self.store)
        with self.assertRaises(ExecutionDispatchError) as caught:
            service.dispatch(make_plan(), port, T0)
        self.assertEqual(caught.exception.code, "EXECUTION_OUTCOME_UNKNOWN")
        durable = self.store.get(make_plan().attempt_id)
        self.assertIsNotNone(durable)
        self.assertEqual(durable.record.state, ExecutionAttemptState.EXECUTION_UNKNOWN)

        second_port = InspectingPort(self.store)
        with self.assertRaises(ExecutionDispatchError) as second:
            service.dispatch(make_plan(), second_port, T0 + timedelta(minutes=1))
        self.assertEqual(second.exception.code, "RECONCILIATION_REQUIRED")
        self.assertEqual(second_port.calls, 0)

    def test_final_rejection_is_explicit_not_unknown(self):
        port = InspectingPort(
            self.store,
            result=ExecutionPortResult(ExecutionPortOutcome.REJECTED_FINAL),
        )
        result = ExecutionDispatchService(FakeMissionLookup(), self.store).dispatch(
            make_plan(), port, T0
        )
        self.assertEqual(result.record.state, ExecutionAttemptState.FAILED_FINAL)
        self.assertEqual(self.store.list_recovery_candidates(), ())

    def test_not_ready_mission_never_calls_port_or_creates_database(self):
        lookup = FakeMissionLookup(FakeStoredMission(FakeRecord(MissionState.PERSISTED)))
        port = InspectingPort(self.store)
        with self.assertRaises(ExecutionDispatchError) as caught:
            ExecutionDispatchService(lookup, self.store).dispatch(make_plan(), port, T0)
        self.assertEqual(caught.exception.code, "MISSION_NOT_READY_FOR_EXECUTION")
        self.assertEqual(port.calls, 0)
        self.assertFalse(self.db.exists())

    def test_two_concurrent_dispatches_have_one_port_call(self):
        barrier = threading.Barrier(2)
        call_lock = threading.Lock()
        calls = []
        errors = []

        class RacingPort:
            def execute(inner_self, attempt):
                with call_lock:
                    calls.append(attempt.attempt_id)
                return ExecutionPortResult(ExecutionPortOutcome.ACCEPTED)

        def worker():
            local_store = SQLiteExecutionAttemptStore(self.db)
            service = ExecutionDispatchService(FakeMissionLookup(), local_store)
            barrier.wait()
            try:
                service.dispatch(make_plan(), RacingPort(), T0)
            except (ExecutionDispatchError, SQLiteExecutionAttemptStoreError) as exc:
                errors.append(getattr(exc, "code", type(exc).__name__))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn(errors[0], {"RECONCILIATION_REQUIRED", "STALE_REVISION"})
        durable = SQLiteExecutionAttemptStore(self.db).get(make_plan().attempt_id)
        self.assertIsNotNone(durable)
        self.assertEqual(durable.record.state, ExecutionAttemptState.PROVIDER_ACKNOWLEDGED)


    def test_normalized_mapping_key_collision_is_rejected(self):
        with self.assertRaises(ExecutionAttemptError) as caught:
            build_execution_attempt_plan(
                mission_key=MISSION_KEY,
                effect_id=EFFECT_ID,
                provider_namespace="keeperhub.direct.v1",
                request_key="req-1",
                request_material={"é": 1, "e\u0301": 2},
            )
        self.assertEqual(caught.exception.code, "DUPLICATE_NORMALIZED_KEY")

    def test_invalid_port_result_becomes_unknown_not_final_failure(self):
        class InvalidPort:
            calls = 0
            def execute(inner_self, attempt):
                inner_self.calls += 1
                object.__new__(ExecutionPortResult)
                return object()

        port = InvalidPort()
        service = ExecutionDispatchService(FakeMissionLookup(), self.store)
        with self.assertRaises(ExecutionDispatchError) as caught:
            service.dispatch(make_plan(), port, T0)
        self.assertEqual(caught.exception.code, "EXECUTION_OUTCOME_UNKNOWN")
        durable = self.store.get(make_plan().attempt_id)
        self.assertIsNotNone(durable)
        self.assertEqual(durable.record.state, ExecutionAttemptState.EXECUTION_UNKNOWN)
        self.assertEqual(port.calls, 1)

    def test_prepared_attempt_can_resume_but_in_flight_cannot(self):
        self.store.initialize()
        prepared = self.store.create(create_initial_execution_attempt(make_plan(), T0))
        port = InspectingPort(self.store)
        result = ExecutionDispatchService(FakeMissionLookup(), self.store).dispatch(
            make_plan(), port, T0 + timedelta(seconds=1)
        )
        self.assertEqual(prepared.revision, 1)
        self.assertEqual(port.calls, 1)
        self.assertEqual(result.record.state, ExecutionAttemptState.PROVIDER_ACKNOWLEDGED)


    def test_new_request_key_for_same_effect_is_conflict(self):
        self.store.initialize()
        self.store.create(create_initial_execution_attempt(make_plan(), T0))
        with self.assertRaises(SQLiteExecutionAttemptStoreError) as caught:
            self.store.create(
                create_initial_execution_attempt(
                    make_plan(request_key="req-2"),
                    T0 + timedelta(seconds=1),
                )
            )
        self.assertEqual(caught.exception.code, "EXECUTION_ATTEMPT_CONFLICT")

    def test_terminal_attempt_cannot_be_dispatched_again(self):
        port = InspectingPort(
            self.store,
            result=ExecutionPortResult(ExecutionPortOutcome.REJECTED_FINAL),
        )
        service = ExecutionDispatchService(FakeMissionLookup(), self.store)
        first = service.dispatch(make_plan(), port, T0)
        self.assertEqual(first.record.state, ExecutionAttemptState.FAILED_FINAL)
        second_port = InspectingPort(self.store)
        with self.assertRaises(ExecutionDispatchError) as caught:
            service.dispatch(make_plan(), second_port, T0 + timedelta(minutes=1))
        self.assertEqual(caught.exception.code, "RECONCILIATION_REQUIRED")
        self.assertEqual(second_port.calls, 0)

    def test_modules_have_no_network_or_secret_capabilities(self):
        import ast
        root = Path(__file__).parents[1] / "src" / "nexus_vector"
        forbidden = {
            "http", "urllib", "socket", "requests", "subprocess", "os",
            "secrets", "web3", "eth_account", "ccxt",
        }
        for relative in (
            Path("domain/execution_attempts.py"),
            Path("persistence/sqlite_execution_attempt_store.py"),
            Path("application/execution_dispatch.py"),
        ):
            tree = ast.parse((root / relative).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".", 1)[0])
            self.assertTrue(imported.isdisjoint(forbidden), (relative, imported & forbidden))

    def test_schema_rejects_foreign_table(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE foreign_table (id INTEGER)")
        with self.assertRaises(SQLiteExecutionAttemptStoreError) as caught:
            self.store.initialize()
        self.assertEqual(caught.exception.code, "INCOMPATIBLE_SCHEMA")


if __name__ == "__main__":
    unittest.main()
