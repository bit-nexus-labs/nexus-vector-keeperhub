"""Atomic SQLite journal for one canonical execution attempt per effect."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptError,
    ExecutionAttemptPlan,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    transition_execution_attempt,
)

_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MILLISECONDS = 5_000
_RECOVERY_STATES = frozenset(
    {
        ExecutionAttemptState.PREPARED,
        ExecutionAttemptState.IN_FLIGHT,
        ExecutionAttemptState.PROVIDER_ACKNOWLEDGED,
        ExecutionAttemptState.EXECUTION_UNKNOWN,
    }
)
_T = TypeVar("_T")

_TABLE_SQL = """
CREATE TABLE execution_attempts (
    attempt_id TEXT PRIMARY KEY,
    mission_key TEXT NOT NULL,
    effect_id TEXT NOT NULL UNIQUE,
    provider_namespace TEXT NOT NULL,
    request_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0)
)
"""

_COLUMNS = (
    ("attempt_id", "TEXT", 0, 1),
    ("mission_key", "TEXT", 1, 0),
    ("effect_id", "TEXT", 1, 0),
    ("provider_namespace", "TEXT", 1, 0),
    ("request_key", "TEXT", 1, 0),
    ("request_fingerprint", "TEXT", 1, 0),
    ("state", "TEXT", 1, 0),
    ("created_at_utc", "TEXT", 1, 0),
    ("updated_at_utc", "TEXT", 1, 0),
    ("revision", "INTEGER", 1, 0),
)


@dataclass(frozen=True)
class StoredExecutionAttempt:
    record: ExecutionAttemptRecord
    revision: int


class SQLiteExecutionAttemptStoreError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise SQLiteExecutionAttemptStoreError(code)


def _serialize_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        _fail("INVALID_TIMESTAMP")
    if offset != timedelta(0):
        _fail("NON_UTC_TIMESTAMP")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("CORRUPT_RECORD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        _fail("CORRUPT_RECORD")
    if _serialize_timestamp(parsed) != value:
        _fail("CORRUPT_RECORD")
    return parsed


def _normalize_schema_sql(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("INCOMPATIBLE_SCHEMA")
    normalized = value.strip().rstrip(";").casefold()
    normalized = normalized.translate(str.maketrans("", "", '"`[]'))
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*([(),])\s*", r"\1", normalized)
    normalized = re.sub(r"\s*(>=|<=|<>|!=|=|>|<)\s*", r"\1", normalized)
    return normalized


class SQLiteExecutionAttemptStore:
    """Durable execution journal; it never performs provider actions."""

    def __init__(self, database_path: str | Path) -> None:
        if not isinstance(database_path, (str, Path)) or not str(database_path):
            _fail("INVALID_DATABASE_PATH")
        self._database_path = str(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=_BUSY_TIMEOUT_MILLISECONDS / 1_000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MILLISECONDS}")
            if self._database_path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except sqlite3.DatabaseError:
            connection.close()
            raise

    def _read(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            connection = self._connect()
        except sqlite3.DatabaseError:
            _fail("DATABASE_ERROR")
        try:
            connection.execute("BEGIN")
            result = operation(connection)
            connection.commit()
            return result
        except SQLiteExecutionAttemptStoreError:
            connection.rollback()
            raise
        except sqlite3.DatabaseError:
            connection.rollback()
            _fail("DATABASE_ERROR")
        finally:
            connection.close()

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            connection = self._connect()
        except sqlite3.DatabaseError:
            _fail("DATABASE_ERROR")
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.commit()
            return result
        except SQLiteExecutionAttemptStoreError:
            connection.rollback()
            raise
        except ExecutionAttemptError as error:
            connection.rollback()
            _fail(error.code)
        except sqlite3.DatabaseError:
            connection.rollback()
            _fail("DATABASE_ERROR")
        finally:
            connection.close()

    def initialize(self) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version == 0:
                existing = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if existing:
                    _fail("INCOMPATIBLE_SCHEMA")
                connection.execute(_TABLE_SQL)
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            elif version != _SCHEMA_VERSION:
                _fail("UNSUPPORTED_SCHEMA_VERSION")
            self._validate_schema(connection)

        self._write(operation)

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'execution_attempts'"
        ).fetchone()
        other_tables = {
            str(item[0])
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        if row is None or other_tables != {"execution_attempts"}:
            _fail("INCOMPATIBLE_SCHEMA")
        if _normalize_schema_sql(row[0]) != _normalize_schema_sql(_TABLE_SQL):
            _fail("INCOMPATIBLE_SCHEMA")
        columns = tuple(
            (item[1], str(item[2]).upper(), item[3], item[5])
            for item in connection.execute("PRAGMA table_info(execution_attempts)")
        )
        if columns != _COLUMNS:
            _fail("INCOMPATIBLE_SCHEMA")
        unique_sets = {
            tuple(
                index_col[2]
                for index_col in connection.execute(
                    f"PRAGMA index_info('{index_row[1]}')"
                )
            )
            for index_row in connection.execute(
                "PRAGMA index_list(execution_attempts)"
            )
            if index_row[2]
        }
        if ("effect_id",) not in unique_sets:
            _fail("INCOMPATIBLE_SCHEMA")

    def create(self, record: ExecutionAttemptRecord) -> StoredExecutionAttempt:
        if not isinstance(record, ExecutionAttemptRecord):
            _fail("INVALID_ATTEMPT_RECORD")
        if record.state is not ExecutionAttemptState.PREPARED:
            _fail("INVALID_INITIAL_ATTEMPT")

        def operation(connection: sqlite3.Connection) -> StoredExecutionAttempt:
            existing = self._load(connection, record.attempt_id)
            if existing is not None:
                if existing.record.plan == record.plan:
                    return existing
                _fail("EXECUTION_ATTEMPT_CONFLICT")
            effect_row = connection.execute(
                "SELECT attempt_id FROM execution_attempts WHERE effect_id = ?",
                (record.plan.effect_id,),
            ).fetchone()
            if effect_row is not None:
                _fail("EXECUTION_ATTEMPT_CONFLICT")
            connection.execute(
                "INSERT INTO execution_attempts ("
                "attempt_id, mission_key, effect_id, provider_namespace, "
                "request_key, request_fingerprint, state, created_at_utc, "
                "updated_at_utc, revision"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.attempt_id,
                    record.plan.mission_key,
                    record.plan.effect_id,
                    record.plan.provider_namespace,
                    record.plan.request_key,
                    record.plan.request_fingerprint,
                    record.state.value,
                    _serialize_timestamp(record.created_at_utc),
                    _serialize_timestamp(record.updated_at_utc),
                    1,
                ),
            )
            stored = self._load(connection, record.attempt_id)
            if stored is None:
                _fail("DATABASE_ERROR")
            return stored

        return self._write(operation)

    def get(self, attempt_id: str) -> StoredExecutionAttempt | None:
        if not isinstance(attempt_id, str):
            _fail("INVALID_ATTEMPT_ID")
        return self._read(lambda connection: self._load(connection, attempt_id))

    def transition(
        self,
        attempt_id: str,
        expected_revision: int,
        target_state: ExecutionAttemptState,
        updated_at_utc: datetime,
    ) -> StoredExecutionAttempt:
        if not isinstance(attempt_id, str):
            _fail("INVALID_ATTEMPT_ID")

        def operation(connection: sqlite3.Connection) -> StoredExecutionAttempt:
            current = self._load(connection, attempt_id)
            if current is None:
                _fail("ATTEMPT_NOT_FOUND")
            if type(expected_revision) is not int or current.revision != expected_revision:
                _fail("STALE_REVISION")
            transitioned = transition_execution_attempt(
                current.record,
                target_state,
                updated_at_utc,
            )
            updated = connection.execute(
                "UPDATE execution_attempts SET state = ?, updated_at_utc = ?, "
                "revision = revision + 1 WHERE attempt_id = ? AND revision = ?",
                (
                    transitioned.state.value,
                    _serialize_timestamp(transitioned.updated_at_utc),
                    attempt_id,
                    expected_revision,
                ),
            )
            if updated.rowcount != 1:
                _fail("STALE_REVISION")
            stored = self._load(connection, attempt_id)
            if stored is None or stored.revision != expected_revision + 1:
                _fail("DATABASE_ERROR")
            return stored

        return self._write(operation)

    def list_recovery_candidates(self) -> tuple[StoredExecutionAttempt, ...]:
        def operation(connection: sqlite3.Connection) -> tuple[StoredExecutionAttempt, ...]:
            values = tuple(state.value for state in sorted(_RECOVERY_STATES, key=lambda x: x.value))
            placeholders = ",".join("?" for _ in values)
            ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT attempt_id FROM execution_attempts "
                    f"WHERE state IN ({placeholders}) ORDER BY attempt_id",
                    values,
                )
            )
            result = []
            for attempt_id in ids:
                stored = self._load(connection, attempt_id)
                if stored is None:
                    _fail("CORRUPT_RECORD")
                result.append(stored)
            return tuple(result)

        return self._read(operation)

    def _load(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
    ) -> StoredExecutionAttempt | None:
        row = connection.execute(
            "SELECT attempt_id, mission_key, effect_id, provider_namespace, "
            "request_key, request_fingerprint, state, created_at_utc, "
            "updated_at_utc, revision FROM execution_attempts "
            "WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            plan = ExecutionAttemptPlan(
                mission_key=row["mission_key"],
                effect_id=row["effect_id"],
                provider_namespace=row["provider_namespace"],
                request_key=row["request_key"],
                request_fingerprint=row["request_fingerprint"],
            )
            record = ExecutionAttemptRecord(
                attempt_id=row["attempt_id"],
                plan=plan,
                state=ExecutionAttemptState(row["state"]),
                created_at_utc=_parse_timestamp(row["created_at_utc"]),
                updated_at_utc=_parse_timestamp(row["updated_at_utc"]),
            )
            revision = row["revision"]
            if type(revision) is not int or revision < 1:
                _fail("CORRUPT_RECORD")
        except SQLiteExecutionAttemptStoreError:
            raise
        except (ExecutionAttemptError, KeyError, TypeError, ValueError):
            _fail("CORRUPT_RECORD")
        return StoredExecutionAttempt(record=record, revision=revision)
