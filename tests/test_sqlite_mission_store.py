from __future__ import annotations

import ast
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.domain.mission_identity import SCHEMA_VERSION
from nexus_vector.domain.mission_models import (
    AssetSpec,
    EffectRequest,
    EffectState,
    MissionRequest,
    MissionState,
    create_initial_mission_record,
)
from nexus_vector.persistence import (
    SQLiteMissionStore,
    SQLiteMissionStoreError,
    StoredMission,
)


CREATED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
LATER = CREATED_AT + timedelta(seconds=1)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def mission_request(
    mission_ref: str = "MISSION-SQLITE-1",
    *,
    amount: int = 1_000_000,
) -> MissionRequest:
    return MissionRequest(
        schema_version=SCHEMA_VERSION,
        mission_namespace="nexus-vector:sqlite-test",
        mission_ref=mission_ref,
        mission_type="ERC20_PAYOUT",
        chain_id=84532,
        asset=AssetSpec(
            token_address="0x0000000000000000000000000000000000000001",
            decimals=6,
        ),
        effects=(
            EffectRequest(
                effect_ref="primary",
                recipient="0x00000000000000000000000000000000000000a1",
                amount_base_units=amount,
            ),
        ),
    )


def initial_record(
    mission_ref: str = "MISSION-SQLITE-1",
    *,
    amount: int = 1_000_000,
):
    return create_initial_mission_record(
        mission_request(mission_ref, amount=amount),
        CREATED_AT,
    )


def assert_store_error(
    test_case: unittest.TestCase,
    code: str,
    operation,
) -> SQLiteMissionStoreError:
    with test_case.assertRaises(SQLiteMissionStoreError) as caught:
        operation()
    test_case.assertEqual(code, caught.exception.code)
    test_case.assertEqual(code, str(caught.exception))
    return caught.exception


class CoordinatedReadStore(SQLiteMissionStore):
    """Pause one selected SELECT before SQLite starts executing it."""

    def __init__(
        self,
        database_path: Path,
        statement_fragment: str,
        statement_reached: threading.Event,
        continue_read: threading.Event,
    ) -> None:
        super().__init__(database_path)
        self._statement_fragment = " ".join(
            statement_fragment.lower().split()
        )
        self._statement_reached = statement_reached
        self._continue_read = continue_read
        self._paused = False

    def _connect(self) -> sqlite3.Connection:
        connection = super()._connect()

        def trace(statement: str) -> None:
            normalized = " ".join(statement.lower().split())
            if (
                not self._paused
                and self._statement_fragment in normalized
            ):
                self._paused = True
                self._statement_reached.set()
                if not self._continue_read.wait(timeout=5):
                    raise AssertionError("coordinated read was not released")

        connection.set_trace_callback(trace)
        return connection


class SQLiteMissionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = (
            Path(self.temporary_directory.name) / "mission-store.sqlite3"
        )
        self.store = SQLiteMissionStore(self.database_path)
        self.store.initialize()

    def _direct_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        self.addCleanup(connection.close)
        return connection

    def _advance_mission(
        self,
        stored: StoredMission,
        *states: MissionState,
    ) -> StoredMission:
        current = stored
        timestamp = current.record.updated_at_utc
        for state in states:
            timestamp += timedelta(seconds=1)
            current = self.store.transition_mission(
                current.record.mission_key,
                current.revision,
                state,
                timestamp,
            )
        return current

    def _advance_effect(
        self,
        stored: StoredMission,
        *states: EffectState,
    ) -> StoredMission:
        current = stored
        timestamp = current.record.updated_at_utc
        for state in states:
            timestamp += timedelta(seconds=1)
            current = self.store.transition_effect(
                current.record.mission_key,
                "primary",
                current.revision,
                state,
                timestamp,
            )
        return current

    def test_initialize_schema_version_and_connection_pragmas(self) -> None:
        connection = self.store._connect()  # noqa: SLF001
        self.addCleanup(connection.close)
        self.assertEqual(1, connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(1, connection.execute("PRAGMA foreign_keys").fetchone()[0])
        self.assertEqual("wal", connection.execute("PRAGMA journal_mode").fetchone()[0])
        self.assertEqual(2, connection.execute("PRAGMA synchronous").fetchone()[0])
        self.assertGreater(
            connection.execute("PRAGMA busy_timeout").fetchone()[0],
            0,
        )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        self.assertEqual({"missions", "effects"}, tables)

    def test_initialize_is_idempotent_and_reopens(self) -> None:
        self.store.initialize()
        reopened = SQLiteMissionStore(self.database_path)
        reopened.initialize()
        self.assertIsNone(reopened.get(initial_record().mission_key))

    def test_create_get_round_trip_and_revision_one(self) -> None:
        record = initial_record()
        created = self.store.create(record)
        self.assertEqual(StoredMission(record, 1), created)
        self.assertEqual(created, self.store.get(record.mission_key))

    def test_same_content_create_is_idempotent_without_overwrite(self) -> None:
        record = initial_record()
        created = self.store.create(record)
        advanced = self.store.transition_mission(
            record.mission_key,
            created.revision,
            MissionState.VALIDATED,
            LATER,
        )
        duplicate = self.store.create(record)
        self.assertEqual(advanced, duplicate)
        self.assertEqual(2, duplicate.revision)
        self.assertIs(MissionState.VALIDATED, duplicate.record.state)

    def test_same_key_changed_content_conflicts_without_mutation(self) -> None:
        original = initial_record(amount=1_000_000)
        conflicting = initial_record(amount=2_000_000)
        created = self.store.create(original)
        assert_store_error(
            self,
            "MISSION_CONFLICT",
            lambda: self.store.create(conflicting),
        )
        self.assertEqual(created, self.store.get(original.mission_key))

    def test_non_initial_create_is_rejected(self) -> None:
        record = initial_record()
        stored = self.store.create(record)
        advanced = self.store.transition_mission(
            record.mission_key,
            stored.revision,
            MissionState.VALIDATED,
            LATER,
        )
        assert_store_error(
            self,
            "INVALID_INITIAL_AGGREGATE",
            lambda: self.store.create(advanced.record),
        )

    def test_database_enforces_effect_identity_and_reference_uniqueness(self) -> None:
        record = initial_record()
        self.store.create(record)
        connection = self._direct_connection()
        row = connection.execute("SELECT * FROM effects").fetchone()
        placeholders = ", ".join("?" for _ in row)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO effects VALUES ({placeholders})",
                tuple(row),
            )
        duplicate_ref = list(row)
        duplicate_ref[0] = "eff_" + "f" * 64
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO effects VALUES ({placeholders})",
                duplicate_ref,
            )

    def test_mission_transition_uses_domain_policy_and_one_revision(self) -> None:
        stored = self.store.create(initial_record())
        transitioned = self.store.transition_mission(
            stored.record.mission_key,
            1,
            MissionState.VALIDATED,
            LATER,
        )
        self.assertEqual(2, transitioned.revision)
        self.assertIs(MissionState.VALIDATED, transitioned.record.state)
        self.assertEqual(LATER, transitioned.record.updated_at_utc)

    def test_effect_transition_uses_canonical_reference_and_one_revision(self) -> None:
        stored = self.store.create(initial_record())
        effect_id = stored.record.effect_id_for("primary")
        transitioned = self.store.transition_effect(
            stored.record.mission_key,
            "primary",
            1,
            EffectState.RESERVED,
            LATER,
        )
        self.assertEqual(2, transitioned.revision)
        by_id = {effect.effect_id: effect for effect in transitioned.record.effects}
        self.assertIs(EffectState.RESERVED, by_id[effect_id].state)

    def test_stale_revision_rejects_without_mutation(self) -> None:
        stored = self.store.create(initial_record())
        advanced = self.store.transition_mission(
            stored.record.mission_key,
            stored.revision,
            MissionState.VALIDATED,
            LATER,
        )
        assert_store_error(
            self,
            "STALE_REVISION",
            lambda: self.store.transition_mission(
                stored.record.mission_key,
                stored.revision,
                MissionState.BLOCKED,
                LATER,
            ),
        )
        self.assertEqual(advanced, self.store.get(stored.record.mission_key))

    def test_two_stores_racing_same_revision_have_one_winner(self) -> None:
        stored = self.store.create(initial_record())
        other_store = SQLiteMissionStore(self.database_path)
        barrier = threading.Barrier(2)

        def transition(target: MissionState) -> str:
            barrier.wait()
            try:
                self.store.transition_mission(
                    stored.record.mission_key,
                    1,
                    target,
                    LATER,
                ) if target is MissionState.VALIDATED else (
                    other_store.transition_mission(
                        stored.record.mission_key,
                        1,
                        target,
                        LATER,
                    )
                )
                return "SUCCESS"
            except SQLiteMissionStoreError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    transition,
                    (MissionState.VALIDATED, MissionState.BLOCKED),
                )
            )
        self.assertCountEqual(("SUCCESS", "STALE_REVISION"), results)
        final = self.store.get(stored.record.mission_key)
        self.assertIsNotNone(final)
        self.assertEqual(2, final.revision)

    def test_invalid_transition_rolls_back_without_partial_change(self) -> None:
        stored = self.store.create(initial_record())
        assert_store_error(
            self,
            "MISSION_TRANSITION_NOT_ALLOWED",
            lambda: self.store.transition_mission(
                stored.record.mission_key,
                1,
                MissionState.COMPLETED,
                LATER,
            ),
        )
        self.assertEqual(stored, self.store.get(stored.record.mission_key))

    def test_completion_guard_remains_enforced(self) -> None:
        stored = self.store.create(initial_record())
        verifying = self._advance_mission(
            stored,
            MissionState.VALIDATED,
            MissionState.PERSISTED,
            MissionState.RECONCILING,
            MissionState.READY_FOR_EXECUTION,
            MissionState.EXECUTING,
            MissionState.VERIFYING,
        )
        assert_store_error(
            self,
            "MISSION_COMPLETION_REQUIRES_CONFIRMED_EFFECTS",
            lambda: self.store.transition_mission(
                verifying.record.mission_key,
                verifying.revision,
                MissionState.COMPLETED,
                verifying.record.updated_at_utc + timedelta(seconds=1),
            ),
        )
        self.assertEqual(verifying, self.store.get(verifying.record.mission_key))

    def test_injected_sqlite_failure_rolls_back_aggregate_and_revision(self) -> None:
        stored = self.store.create(initial_record())
        connection = self._direct_connection()
        connection.execute(
            "CREATE TRIGGER fail_effect_rewrite BEFORE UPDATE ON effects "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        connection.commit()
        assert_store_error(
            self,
            "DATABASE_ERROR",
            lambda: self.store.transition_effect(
                stored.record.mission_key,
                "primary",
                stored.revision,
                EffectState.RESERVED,
                LATER,
            ),
        )
        self.assertEqual(stored, self.store.get(stored.record.mission_key))

    def test_close_reopen_preserves_exact_aggregate_and_revision(self) -> None:
        stored = self.store.create(initial_record())
        advanced = self.store.transition_effect(
            stored.record.mission_key,
            "primary",
            stored.revision,
            EffectState.RESERVED,
            LATER,
        )
        reopened = SQLiteMissionStore(self.database_path)
        reopened.initialize()
        self.assertEqual(advanced, reopened.get(advanced.record.mission_key))

    def test_restart_candidates_are_ordered_filtered_and_read_only(self) -> None:
        received = self.store.create(initial_record("RESTART-RECEIVED"))
        persisted = self.store.create(initial_record("RESTART-PERSISTED"))
        persisted = self._advance_mission(
            persisted,
            MissionState.VALIDATED,
            MissionState.PERSISTED,
        )

        conflict = self.store.create(initial_record("TERMINAL-CONFLICT"))
        conflict = self._advance_mission(
            conflict,
            MissionState.MISSION_CONFLICT,
        )
        blocked = self.store.create(initial_record("TERMINAL-BLOCKED"))
        blocked = self._advance_mission(blocked, MissionState.BLOCKED)
        manual = self.store.create(initial_record("TERMINAL-MANUAL"))
        manual = self._advance_mission(
            manual,
            MissionState.VALIDATED,
            MissionState.PERSISTED,
            MissionState.RECONCILING,
            MissionState.MANUAL_REVIEW_REQUIRED,
        )
        completed = self.store.create(initial_record("TERMINAL-COMPLETED"))
        completed = self._advance_effect(
            completed,
            EffectState.RESERVED,
            EffectState.SUBMITTED,
            EffectState.CHAIN_CONFIRMED,
        )
        completed = self._advance_mission(
            completed,
            MissionState.VALIDATED,
            MissionState.PERSISTED,
            MissionState.RECONCILING,
            MissionState.COMPLETED,
        )

        before = {
            item.record.mission_key: item
            for item in (
                received,
                persisted,
                conflict,
                blocked,
                manual,
                completed,
            )
        }
        candidates = self.store.list_restart_candidates()
        candidate_keys = tuple(item.record.mission_key for item in candidates)
        self.assertEqual(tuple(sorted(candidate_keys)), candidate_keys)
        self.assertEqual(
            {received.record.mission_key, persisted.record.mission_key},
            set(candidate_keys),
        )
        for key, expected in before.items():
            self.assertEqual(expected, self.store.get(key))

    def test_get_observes_one_snapshot_during_concurrent_effect_commit(self) -> None:
        original = self.store.create(initial_record("SNAPSHOT-GET"))
        reached = threading.Event()
        release = threading.Event()
        reader = CoordinatedReadStore(
            self.database_path,
            "from effects where mission_key",
            reached,
            release,
        )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                reader.get,
                original.record.mission_key,
            )
            self.assertTrue(reached.wait(timeout=5))
            transitioned = self.store.transition_effect(
                original.record.mission_key,
                "primary",
                original.revision,
                EffectState.RESERVED,
                LATER,
            )
            release.set()
            observed = future.result(timeout=5)

        self.assertEqual(original, observed)
        self.assertEqual(
            transitioned,
            self.store.get(original.record.mission_key),
        )

    def test_restart_candidates_observe_one_candidate_revision_snapshot(self) -> None:
        original = self.store.create(initial_record("SNAPSHOT-RESTART"))
        reached = threading.Event()
        release = threading.Event()
        reader = CoordinatedReadStore(
            self.database_path,
            "from missions where mission_key",
            reached,
            release,
        )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(reader.list_restart_candidates)
            self.assertTrue(reached.wait(timeout=5))
            blocked = self.store.transition_mission(
                original.record.mission_key,
                original.revision,
                MissionState.BLOCKED,
                LATER,
            )
            release.set()
            observed = future.result(timeout=5)

        self.assertEqual((original,), observed)
        self.assertEqual(
            blocked,
            self.store.get(original.record.mission_key),
        )

    def test_read_transactions_do_not_change_state_or_revision(self) -> None:
        original = self.store.create(initial_record("READ-ONLY"))
        self.assertEqual(original, self.store.get(original.record.mission_key))
        self.assertEqual((original,), self.store.list_restart_candidates())
        self.assertEqual(original, self.store.get(original.record.mission_key))

    def _create_version_one_lookalike_schema(
        self,
        path: Path,
        *,
        mission_revision_check: str,
        chain_id_check: str,
        token_decimals_check: str,
        amount_check: str,
    ) -> tuple[int, tuple[str, str]]:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                f"""
                CREATE TABLE missions (
                    mission_key TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    revision INTEGER NOT NULL {mission_revision_check}
                );

                CREATE TABLE effects (
                    effect_id TEXT PRIMARY KEY,
                    mission_key TEXT NOT NULL,
                    effect_ref TEXT NOT NULL,
                    chain_id INTEGER NOT NULL {chain_id_check},
                    token_address TEXT NOT NULL,
                    token_decimals INTEGER NOT NULL {token_decimals_check},
                    recipient TEXT NOT NULL,
                    amount_base_units INTEGER NOT NULL {amount_check},
                    state TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    FOREIGN KEY (mission_key)
                        REFERENCES missions(mission_key)
                        ON DELETE CASCADE,
                    UNIQUE (mission_key, effect_ref)
                );

                PRAGMA user_version = 1;
                """
            )
            connection.commit()
            before_version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            before_sql = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name IN ('missions', 'effects') "
                    "ORDER BY name"
                )
            )
            return before_version, before_sql
        finally:
            connection.close()

    def test_version_one_schema_without_checks_fails_without_mutation(self) -> None:
        path = Path(self.temporary_directory.name) / "missing-checks.db"
        before_version, before_sql = self._create_version_one_lookalike_schema(
            path,
            mission_revision_check="",
            chain_id_check="",
            token_decimals_check="",
            amount_check="",
        )

        assert_store_error(
            self,
            "INCOMPATIBLE_SCHEMA",
            lambda: SQLiteMissionStore(path).initialize(),
        )

        connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                before_version,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            self.assertEqual(
                before_sql,
                tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' "
                        "AND name IN ('missions', 'effects') "
                        "ORDER BY name"
                    )
                ),
            )
        finally:
            connection.close()

    def test_version_one_schema_with_altered_check_fails_without_mutation(self) -> None:
        path = Path(self.temporary_directory.name) / "altered-check.db"
        before_version, before_sql = self._create_version_one_lookalike_schema(
            path,
            mission_revision_check="CHECK (revision >= 0)",
            chain_id_check="CHECK (chain_id > 0)",
            token_decimals_check="CHECK (token_decimals >= 0)",
            amount_check="CHECK (amount_base_units > 0)",
        )

        assert_store_error(
            self,
            "INCOMPATIBLE_SCHEMA",
            lambda: SQLiteMissionStore(path).initialize(),
        )

        connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                before_version,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            self.assertEqual(
                before_sql,
                tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' "
                        "AND name IN ('missions', 'effects') "
                        "ORDER BY name"
                    )
                ),
            )
        finally:
            connection.close()

    def test_corrupted_rows_fail_closed_with_safe_code(self) -> None:
        corruptions = (
            ("missions", "state", "NOT_A_STATE"),
            ("missions", "updated_at_utc", "not-a-timestamp"),
            ("missions", "request_json", "{not-json"),
            ("effects", "recipient", "not-an-address"),
        )
        for index, (table, column, value) in enumerate(corruptions):
            with self.subTest(column=column):
                path = Path(self.temporary_directory.name) / f"corrupt-{index}.db"
                store = SQLiteMissionStore(path)
                store.initialize()
                stored = store.create(initial_record(f"CORRUPT-{index}"))
                connection = sqlite3.connect(path)
                try:
                    connection.execute(
                        f"UPDATE {table} SET {column} = ?",
                        (value,),
                    )
                    connection.commit()
                finally:
                    connection.close()
                error = assert_store_error(
                    self,
                    "CORRUPT_RECORD",
                    lambda store=store, key=stored.record.mission_key: (
                        store.get(key)
                    ),
                )
                self.assertNotIn(str(value), str(error))

    def test_unsupported_future_schema_fails_closed(self) -> None:
        path = Path(self.temporary_directory.name) / "future.db"
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA user_version = 2")
        connection.close()
        assert_store_error(
            self,
            "UNSUPPORTED_SCHEMA_VERSION",
            lambda: SQLiteMissionStore(path).initialize(),
        )

    def test_incompatible_existing_schema_fails_closed(self) -> None:
        path = Path(self.temporary_directory.name) / "incompatible.db"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.close()
        assert_store_error(
            self,
            "INCOMPATIBLE_SCHEMA",
            lambda: SQLiteMissionStore(path).initialize(),
        )

    def test_module_has_no_external_action_or_clock_capabilities(self) -> None:
        module_path = (
            PROJECT_ROOT
            / "src"
            / "nexus_vector"
            / "persistence"
            / "sqlite_mission_store.py"
        )
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_import_roots = {
            "http",
            "logging",
            "os",
            "random",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        forbidden_calls = {
            "now",
            "popen",
            "sleep",
            "system",
            "time",
            "urlopen",
            "utcnow",
        }
        imported_roots: set[str] = set()
        called_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id.lower())
                elif isinstance(node.func, ast.Attribute):
                    called_names.add(node.func.attr.lower())
        self.assertTrue(forbidden_import_roots.isdisjoint(imported_roots))
        self.assertTrue(forbidden_calls.isdisjoint(called_names))
        lowered = source.lower()
        for forbidden_text in (
            "results_private",
            "keeperhub",
            "wallet",
            "blockchain",
            "signing",
            "broadcast",
            "deployment",
        ):
            self.assertNotIn(forbidden_text, lowered)

    def test_public_module_exports_only_accepted_classes(self) -> None:
        import nexus_vector.persistence as persistence

        self.assertEqual(
            {
                "SQLiteMissionStore",
                "SQLiteMissionStoreError",
                "StoredMission",
            },
            set(persistence.__all__),
        )


if __name__ == "__main__":
    unittest.main()
