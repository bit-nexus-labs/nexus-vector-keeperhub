"""Durable one-shot ledger for KeeperHub action-specific authorizations."""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MILLISECONDS = 5_000
_JOURNAL_MODE_RETRY_ATTEMPTS = 10
_JOURNAL_MODE_RETRY_DELAY_SECONDS = 0.02
_MAX_TEXT_LENGTH = 256
_T = TypeVar("_T")

_TABLE_SQL = """
CREATE TABLE keeperhub_authorization_ledger (
    approval_reference TEXT PRIMARY KEY,
    phase TEXT NOT NULL CHECK (
        phase IN ('SIMULATION', 'BROADCAST')
    ),
    action_sheet_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    body_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'CLAIMED',
            'ELIGIBLE_FOR_BROADCAST_APPROVAL',
            'REJECTED_FINAL',
            'OUTCOME_UNKNOWN',
            'ACCEPTED'
        )
    ),
    claimed_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(phase, attempt_id)
)
"""

_COLUMNS = (
    ("approval_reference", "TEXT", 0, 1),
    ("phase", "TEXT", 1, 0),
    ("action_sheet_id", "TEXT", 1, 0),
    ("attempt_id", "TEXT", 1, 0),
    ("request_fingerprint", "TEXT", 1, 0),
    ("body_fingerprint", "TEXT", 1, 0),
    ("state", "TEXT", 1, 0),
    ("claimed_at_utc", "TEXT", 1, 0),
    ("updated_at_utc", "TEXT", 1, 0),
)


class KeeperHubAuthorizationPhase(str, Enum):
    SIMULATION = "SIMULATION"
    BROADCAST = "BROADCAST"


class KeeperHubAuthorizationState(str, Enum):
    CLAIMED = "CLAIMED"
    ELIGIBLE_FOR_BROADCAST_APPROVAL = "ELIGIBLE_FOR_BROADCAST_APPROVAL"
    REJECTED_FINAL = "REJECTED_FINAL"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    ACCEPTED = "ACCEPTED"


_ALLOWED_TRANSITIONS = {
    KeeperHubAuthorizationPhase.SIMULATION: frozenset(
        {
            KeeperHubAuthorizationState.ELIGIBLE_FOR_BROADCAST_APPROVAL,
            KeeperHubAuthorizationState.REJECTED_FINAL,
            KeeperHubAuthorizationState.OUTCOME_UNKNOWN,
        }
    ),
    KeeperHubAuthorizationPhase.BROADCAST: frozenset(
        {
            KeeperHubAuthorizationState.ACCEPTED,
            KeeperHubAuthorizationState.REJECTED_FINAL,
            KeeperHubAuthorizationState.OUTCOME_UNKNOWN,
        }
    ),
}


class SQLiteKeeperHubAuthorizationLedgerError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise SQLiteKeeperHubAuthorizationLedgerError(code)


def _required_text(value: Any, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > _MAX_TEXT_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(code)
    return value


def _body_fingerprint(value: Any) -> str:
    fingerprint = _required_text(value, "INVALID_BODY_FINGERPRINT")
    if (
        not fingerprint.startswith("khb_")
        or len(fingerprint) != 68
        or any(character not in "0123456789abcdef" for character in fingerprint[4:])
    ):
        _fail("INVALID_BODY_FINGERPRINT")
    return fingerprint


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


@dataclass(frozen=True)
class KeeperHubAuthorizationRecord:
    approval_reference: str
    phase: KeeperHubAuthorizationPhase
    action_sheet_id: str
    attempt_id: str
    request_fingerprint: str
    body_fingerprint: str
    state: KeeperHubAuthorizationState
    claimed_at_utc: datetime
    updated_at_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "approval_reference",
            _required_text(self.approval_reference, "INVALID_APPROVAL_REFERENCE"),
        )
        if not isinstance(self.phase, KeeperHubAuthorizationPhase):
            _fail("INVALID_AUTHORIZATION_PHASE")
        object.__setattr__(
            self,
            "action_sheet_id",
            _required_text(self.action_sheet_id, "INVALID_ACTION_SHEET_ID"),
        )
        object.__setattr__(
            self,
            "attempt_id",
            _required_text(self.attempt_id, "INVALID_ATTEMPT_ID"),
        )
        object.__setattr__(
            self,
            "request_fingerprint",
            _required_text(
                self.request_fingerprint,
                "INVALID_REQUEST_FINGERPRINT",
            ),
        )
        object.__setattr__(
            self,
            "body_fingerprint",
            _body_fingerprint(self.body_fingerprint),
        )
        if not isinstance(self.state, KeeperHubAuthorizationState):
            _fail("INVALID_AUTHORIZATION_STATE")
        claimed = _parse_timestamp(_serialize_timestamp(self.claimed_at_utc))
        updated = _parse_timestamp(_serialize_timestamp(self.updated_at_utc))
        if updated < claimed:
            _fail("REVERSED_TIMESTAMP")
        if (
            self.state is KeeperHubAuthorizationState.CLAIMED
            and updated != claimed
        ):
            _fail("INVALID_CLAIM_TIMESTAMP")


class SQLiteKeeperHubAuthorizationLedger:
    """Atomic cross-process consumption ledger and sanitized receipt journal."""

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
        except SQLiteKeeperHubAuthorizationLedgerError:
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
        except SQLiteKeeperHubAuthorizationLedgerError:
            connection.rollback()
            raise
        except sqlite3.IntegrityError:
            connection.rollback()
            _fail("AUTHORIZATION_ALREADY_CONSUMED")
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
            "AND name = 'keeperhub_authorization_ledger'"
        ).fetchone()
        tables = {
            str(item[0])
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        if row is None or tables != {"keeperhub_authorization_ledger"}:
            _fail("INCOMPATIBLE_SCHEMA")
        if _normalize_schema_sql(row[0]) != _normalize_schema_sql(_TABLE_SQL):
            _fail("INCOMPATIBLE_SCHEMA")
        columns = tuple(
            (item[1], str(item[2]).upper(), item[3], item[5])
            for item in connection.execute(
                "PRAGMA table_info(keeperhub_authorization_ledger)"
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
                "PRAGMA index_list(keeperhub_authorization_ledger)"
            )
            if index_row[2]
        }
        if ("phase", "attempt_id") not in unique_sets:
            _fail("INCOMPATIBLE_SCHEMA")

    def claim(
        self,
        record: KeeperHubAuthorizationRecord,
    ) -> KeeperHubAuthorizationRecord:
        if not isinstance(record, KeeperHubAuthorizationRecord):
            _fail("INVALID_AUTHORIZATION_RECORD")
        if record.state is not KeeperHubAuthorizationState.CLAIMED:
            _fail("CLAIM_STATE_REQUIRED")

        def operation(
            connection: sqlite3.Connection,
        ) -> KeeperHubAuthorizationRecord:
            existing_reference = self._load(
                connection,
                record.approval_reference,
            )
            existing_attempt = self._load_for_attempt(
                connection,
                record.phase,
                record.attempt_id,
            )
            if existing_reference is not None or existing_attempt is not None:
                _fail("AUTHORIZATION_ALREADY_CONSUMED")
            connection.execute(
                "INSERT INTO keeperhub_authorization_ledger ("
                "approval_reference, phase, action_sheet_id, attempt_id, "
                "request_fingerprint, body_fingerprint, state, "
                "claimed_at_utc, updated_at_utc"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.approval_reference,
                    record.phase.value,
                    record.action_sheet_id,
                    record.attempt_id,
                    record.request_fingerprint,
                    record.body_fingerprint,
                    record.state.value,
                    _serialize_timestamp(record.claimed_at_utc),
                    _serialize_timestamp(record.updated_at_utc),
                ),
            )
            stored = self._load(connection, record.approval_reference)
            if stored is None:
                _fail("DATABASE_ERROR")
            return stored

        return self._write(operation)

    def transition(
        self,
        approval_reference: str,
        target_state: KeeperHubAuthorizationState,
        updated_at_utc: datetime,
    ) -> KeeperHubAuthorizationRecord:
        reference = _required_text(
            approval_reference,
            "INVALID_APPROVAL_REFERENCE",
        )
        if not isinstance(target_state, KeeperHubAuthorizationState):
            _fail("INVALID_AUTHORIZATION_STATE")
        timestamp = _parse_timestamp(_serialize_timestamp(updated_at_utc))

        def operation(
            connection: sqlite3.Connection,
        ) -> KeeperHubAuthorizationRecord:
            current = self._load(connection, reference)
            if current is None:
                _fail("AUTHORIZATION_NOT_FOUND")
            if current.state is not KeeperHubAuthorizationState.CLAIMED:
                _fail("AUTHORIZATION_ALREADY_FINALIZED")
            if target_state not in _ALLOWED_TRANSITIONS[current.phase]:
                _fail("AUTHORIZATION_TRANSITION_NOT_ALLOWED")
            if timestamp < current.updated_at_utc:
                _fail("TIMESTAMP_BEFORE_CURRENT")
            cursor = connection.execute(
                "UPDATE keeperhub_authorization_ledger "
                "SET state = ?, updated_at_utc = ? "
                "WHERE approval_reference = ? AND state = 'CLAIMED'",
                (
                    target_state.value,
                    _serialize_timestamp(timestamp),
                    reference,
                ),
            )
            if cursor.rowcount != 1:
                _fail("AUTHORIZATION_ALREADY_FINALIZED")
            stored = self._load(connection, reference)
            if stored is None:
                _fail("DATABASE_ERROR")
            return stored

        return self._write(operation)

    def get(
        self,
        approval_reference: str,
    ) -> KeeperHubAuthorizationRecord | None:
        reference = _required_text(
            approval_reference,
            "INVALID_APPROVAL_REFERENCE",
        )
        return self._read(lambda connection: self._load(connection, reference))

    def get_for_attempt(
        self,
        phase: KeeperHubAuthorizationPhase,
        attempt_id: str,
    ) -> KeeperHubAuthorizationRecord | None:
        if not isinstance(phase, KeeperHubAuthorizationPhase):
            _fail("INVALID_AUTHORIZATION_PHASE")
        canonical_attempt_id = _required_text(attempt_id, "INVALID_ATTEMPT_ID")
        return self._read(
            lambda connection: self._load_for_attempt(
                connection,
                phase,
                canonical_attempt_id,
            )
        )

    def _load(
        self,
        connection: sqlite3.Connection,
        approval_reference: str,
    ) -> KeeperHubAuthorizationRecord | None:
        row = connection.execute(
            "SELECT approval_reference, phase, action_sheet_id, attempt_id, "
            "request_fingerprint, body_fingerprint, state, claimed_at_utc, "
            "updated_at_utc FROM keeperhub_authorization_ledger "
            "WHERE approval_reference = ?",
            (approval_reference,),
        ).fetchone()
        return self._decode(row)

    def _load_for_attempt(
        self,
        connection: sqlite3.Connection,
        phase: KeeperHubAuthorizationPhase,
        attempt_id: str,
    ) -> KeeperHubAuthorizationRecord | None:
        row = connection.execute(
            "SELECT approval_reference, phase, action_sheet_id, attempt_id, "
            "request_fingerprint, body_fingerprint, state, claimed_at_utc, "
            "updated_at_utc FROM keeperhub_authorization_ledger "
            "WHERE phase = ? AND attempt_id = ?",
            (phase.value, attempt_id),
        ).fetchone()
        return self._decode(row)

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> KeeperHubAuthorizationRecord | None:
        if row is None:
            return None
        try:
            return KeeperHubAuthorizationRecord(
                approval_reference=row["approval_reference"],
                phase=KeeperHubAuthorizationPhase(row["phase"]),
                action_sheet_id=row["action_sheet_id"],
                attempt_id=row["attempt_id"],
                request_fingerprint=row["request_fingerprint"],
                body_fingerprint=row["body_fingerprint"],
                state=KeeperHubAuthorizationState(row["state"]),
                claimed_at_utc=_parse_timestamp(row["claimed_at_utc"]),
                updated_at_utc=_parse_timestamp(row["updated_at_utc"]),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            SQLiteKeeperHubAuthorizationLedgerError,
        ):
            _fail("CORRUPT_RECORD")
