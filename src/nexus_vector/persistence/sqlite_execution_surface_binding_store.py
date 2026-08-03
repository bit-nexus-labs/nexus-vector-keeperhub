"""Fail-closed SQLite store for one provider surface per economic effect."""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from nexus_vector.domain.execution_surfaces import (
    ExecutionSurface,
    ExecutionSurfaceBinding,
    ExecutionSurfaceBindingError,
)

_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MILLISECONDS = 5_000
_JOURNAL_MODE_RETRY_ATTEMPTS = 10
_JOURNAL_MODE_RETRY_DELAY_SECONDS = 0.02
_T = TypeVar("_T")

_TABLE_SQL = """
CREATE TABLE execution_surface_bindings (
    effect_id TEXT PRIMARY KEY,
    mission_key TEXT NOT NULL,
    surface TEXT NOT NULL CHECK (
        surface IN ('DIRECT_EXECUTION', 'WORKFLOW', 'MCP')
    ),
    binding_reference TEXT NOT NULL UNIQUE,
    bound_at_utc TEXT NOT NULL
)
"""

_COLUMNS = (
    ("effect_id", "TEXT", 0, 1),
    ("mission_key", "TEXT", 1, 0),
    ("surface", "TEXT", 1, 0),
    ("binding_reference", "TEXT", 1, 0),
    ("bound_at_utc", "TEXT", 1, 0),
)


class SQLiteExecutionSurfaceBindingStoreError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise SQLiteExecutionSurfaceBindingStoreError(code)


def _serialize_timestamp(value: Any) -> str:
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


class SQLiteExecutionSurfaceBindingStore:
    """Persist an immutable execution-surface choice before provider mutation."""

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
                self._enable_wal_with_bounded_retry(connection)
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except sqlite3.DatabaseError:
            connection.close()
            raise

    @staticmethod
    def _enable_wal_with_bounded_retry(connection: sqlite3.Connection) -> None:
        for attempt in range(_JOURNAL_MODE_RETRY_ATTEMPTS):
            try:
                row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                if row is None or str(row[0]).casefold() != "wal":
                    raise sqlite3.OperationalError("journal mode unavailable")
                return
            except sqlite3.OperationalError as error:
                message = str(error).casefold()
                retryable = "locked" in message or "busy" in message
                if not retryable or attempt + 1 == _JOURNAL_MODE_RETRY_ATTEMPTS:
                    raise
                time.sleep(_JOURNAL_MODE_RETRY_DELAY_SECONDS * (attempt + 1))

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
        except SQLiteExecutionSurfaceBindingStoreError:
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
        except SQLiteExecutionSurfaceBindingStoreError:
            connection.rollback()
            raise
        except (ExecutionSurfaceBindingError, sqlite3.IntegrityError):
            connection.rollback()
            _fail("SURFACE_BINDING_CONFLICT")
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
            "AND name = 'execution_surface_bindings'"
        ).fetchone()
        tables = {
            str(item[0])
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        if row is None or tables != {"execution_surface_bindings"}:
            _fail("INCOMPATIBLE_SCHEMA")
        if _normalize_schema_sql(row[0]) != _normalize_schema_sql(_TABLE_SQL):
            _fail("INCOMPATIBLE_SCHEMA")
        columns = tuple(
            (item[1], str(item[2]).upper(), item[3], item[5])
            for item in connection.execute(
                "PRAGMA table_info(execution_surface_bindings)"
            )
        )
        if columns != _COLUMNS:
            _fail("INCOMPATIBLE_SCHEMA")
        unique_sets = {
            tuple(
                column[2]
                for column in connection.execute(
                    f"PRAGMA index_info('{index[1]}')"
                )
            )
            for index in connection.execute(
                "PRAGMA index_list(execution_surface_bindings)"
            )
            if index[2]
        }
        if ("binding_reference",) not in unique_sets:
            _fail("INCOMPATIBLE_SCHEMA")

    def bind(
        self,
        requested: ExecutionSurfaceBinding,
    ) -> ExecutionSurfaceBinding:
        if not isinstance(requested, ExecutionSurfaceBinding):
            _fail("INVALID_SURFACE_BINDING")

        def operation(connection: sqlite3.Connection) -> ExecutionSurfaceBinding:
            existing = self._load_by_effect(connection, requested.effect_id)
            if existing is not None:
                if existing.mission_key != requested.mission_key:
                    _fail("SURFACE_BINDING_CONFLICT")
                if existing.surface is not requested.surface:
                    _fail("SURFACE_BINDING_CONFLICT")
                return existing

            reference_owner = self._load_by_reference(
                connection,
                requested.binding_reference,
            )
            if reference_owner is not None:
                _fail("BINDING_REFERENCE_CONFLICT")

            connection.execute(
                "INSERT INTO execution_surface_bindings ("
                "effect_id, mission_key, surface, binding_reference, bound_at_utc"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    requested.effect_id,
                    requested.mission_key,
                    requested.surface.value,
                    requested.binding_reference,
                    _serialize_timestamp(requested.bound_at_utc),
                ),
            )
            stored = self._load_by_effect(connection, requested.effect_id)
            if stored is None:
                _fail("DATABASE_ERROR")
            return stored

        return self._write(operation)

    def get(self, effect_id: str) -> ExecutionSurfaceBinding | None:
        if not isinstance(effect_id, str):
            _fail("INVALID_EFFECT_ID")
        return self._read(
            lambda connection: self._load_by_effect(connection, effect_id)
        )

    def _load_by_effect(
        self,
        connection: sqlite3.Connection,
        effect_id: str,
    ) -> ExecutionSurfaceBinding | None:
        row = connection.execute(
            "SELECT effect_id, mission_key, surface, binding_reference, "
            "bound_at_utc FROM execution_surface_bindings WHERE effect_id = ?",
            (effect_id,),
        ).fetchone()
        return self._decode(row)

    def _load_by_reference(
        self,
        connection: sqlite3.Connection,
        binding_reference: str,
    ) -> ExecutionSurfaceBinding | None:
        row = connection.execute(
            "SELECT effect_id, mission_key, surface, binding_reference, "
            "bound_at_utc FROM execution_surface_bindings "
            "WHERE binding_reference = ?",
            (binding_reference,),
        ).fetchone()
        return self._decode(row)

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> ExecutionSurfaceBinding | None:
        if row is None:
            return None
        try:
            return ExecutionSurfaceBinding(
                mission_key=row["mission_key"],
                effect_id=row["effect_id"],
                surface=ExecutionSurface(row["surface"]),
                binding_reference=row["binding_reference"],
                bound_at_utc=_parse_timestamp(row["bound_at_utc"]),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ExecutionSurfaceBindingError,
        ):
            _fail("CORRUPT_RECORD")
