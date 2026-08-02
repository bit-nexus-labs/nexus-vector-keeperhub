"""Append-only SQLite journal for provider execution references."""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from nexus_vector.domain.provider_execution_references import (
    ProviderExecutionReference,
    ProviderExecutionReferenceError,
)

_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MILLISECONDS = 5_000
_JOURNAL_MODE_RETRY_ATTEMPTS = 10
_JOURNAL_MODE_RETRY_DELAY_SECONDS = 0.02
_T = TypeVar("_T")

_TABLE_SQL = """
CREATE TABLE provider_execution_references (
    attempt_id TEXT PRIMARY KEY,
    provider_namespace TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    provider_reference TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE(provider_namespace, provider_reference)
)
"""

_COLUMNS = (
    ("attempt_id", "TEXT", 0, 1),
    ("provider_namespace", "TEXT", 1, 0),
    ("request_fingerprint", "TEXT", 1, 0),
    ("provider_reference", "TEXT", 1, 0),
    ("created_at_utc", "TEXT", 1, 0),
)


class SQLiteProviderExecutionReferenceStoreError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise SQLiteProviderExecutionReferenceStoreError(code)


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


class SQLiteProviderExecutionReferenceStore:
    """Durable immutable mapping from attempt ID to provider reference."""

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
        except SQLiteProviderExecutionReferenceStoreError:
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
        except SQLiteProviderExecutionReferenceStoreError:
            connection.rollback()
            raise
        except ProviderExecutionReferenceError as error:
            connection.rollback()
            _fail(error.code)
        except sqlite3.IntegrityError:
            connection.rollback()
            _fail("PROVIDER_REFERENCE_CONFLICT")
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
            "AND name = 'provider_execution_references'"
        ).fetchone()
        tables = {
            str(item[0])
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        if row is None or tables != {"provider_execution_references"}:
            _fail("INCOMPATIBLE_SCHEMA")
        if _normalize_schema_sql(row[0]) != _normalize_schema_sql(_TABLE_SQL):
            _fail("INCOMPATIBLE_SCHEMA")
        columns = tuple(
            (item[1], str(item[2]).upper(), item[3], item[5])
            for item in connection.execute(
                "PRAGMA table_info(provider_execution_references)"
            )
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
                "PRAGMA index_list(provider_execution_references)"
            )
            if index_row[2]
        }
        if ("provider_namespace", "provider_reference") not in unique_sets:
            _fail("INCOMPATIBLE_SCHEMA")

    def create(self, record: ProviderExecutionReference) -> ProviderExecutionReference:
        if not isinstance(record, ProviderExecutionReference):
            _fail("INVALID_PROVIDER_REFERENCE_RECORD")

        def operation(connection: sqlite3.Connection) -> ProviderExecutionReference:
            existing = self._load(connection, record.attempt_id)
            if existing is not None:
                if existing == record:
                    return existing
                _fail("PROVIDER_REFERENCE_CONFLICT")
            duplicate = connection.execute(
                "SELECT attempt_id FROM provider_execution_references "
                "WHERE provider_namespace = ? AND provider_reference = ?",
                (record.provider_namespace, record.provider_reference),
            ).fetchone()
            if duplicate is not None:
                _fail("PROVIDER_REFERENCE_CONFLICT")
            connection.execute(
                "INSERT INTO provider_execution_references ("
                "attempt_id, provider_namespace, request_fingerprint, "
                "provider_reference, created_at_utc"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    record.attempt_id,
                    record.provider_namespace,
                    record.request_fingerprint,
                    record.provider_reference,
                    _serialize_timestamp(record.created_at_utc),
                ),
            )
            stored = self._load(connection, record.attempt_id)
            if stored is None:
                _fail("DATABASE_ERROR")
            return stored

        return self._write(operation)

    def get(self, attempt_id: str) -> ProviderExecutionReference | None:
        if not isinstance(attempt_id, str):
            _fail("INVALID_ATTEMPT_ID")
        return self._read(lambda connection: self._load(connection, attempt_id))

    def get_by_provider_reference(
        self,
        provider_namespace: str,
        provider_reference: str,
    ) -> ProviderExecutionReference | None:
        if not isinstance(provider_namespace, str) or not provider_namespace:
            _fail("INVALID_PROVIDER_NAMESPACE")
        if not isinstance(provider_reference, str) or not provider_reference:
            _fail("INVALID_PROVIDER_REFERENCE")

        def operation(connection: sqlite3.Connection) -> ProviderExecutionReference | None:
            row = connection.execute(
                "SELECT attempt_id FROM provider_execution_references "
                "WHERE provider_namespace = ? AND provider_reference = ?",
                (provider_namespace, provider_reference),
            ).fetchone()
            return None if row is None else self._load(connection, str(row[0]))

        return self._read(operation)

    def _load(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
    ) -> ProviderExecutionReference | None:
        row = connection.execute(
            "SELECT attempt_id, provider_namespace, request_fingerprint, "
            "provider_reference, created_at_utc "
            "FROM provider_execution_references WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return ProviderExecutionReference(
                attempt_id=row["attempt_id"],
                provider_namespace=row["provider_namespace"],
                request_fingerprint=row["request_fingerprint"],
                provider_reference=row["provider_reference"],
                created_at_utc=_parse_timestamp(row["created_at_utc"]),
            )
        except (ProviderExecutionReferenceError, KeyError, TypeError, ValueError):
            _fail("CORRUPT_RECORD")
