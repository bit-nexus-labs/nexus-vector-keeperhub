"""Atomic SQLite persistence for immutable Mission aggregates."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from nexus_vector.domain.mission_models import (
    EffectRecord,
    EffectState,
    MissionModelValidationError,
    MissionRecord,
    MissionRequest,
    MissionState,
)
from nexus_vector.domain.mission_transitions import (
    MissionTransitionError,
    transition_mission as apply_mission_transition,
    transition_mission_effect as apply_effect_transition,
)


_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MILLISECONDS = 5_000
_TERMINAL_MISSION_STATES = frozenset(
    {
        MissionState.COMPLETED,
        MissionState.MISSION_CONFLICT,
        MissionState.BLOCKED,
        MissionState.MANUAL_REVIEW_REQUIRED,
    }
)
_T = TypeVar("_T")


@dataclass(frozen=True)
class StoredMission:
    """One reconstructed Mission aggregate and its CAS revision."""

    record: MissionRecord
    revision: int


class SQLiteMissionStoreError(RuntimeError):
    """Machine-classifiable persistence error with no input echo."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise SQLiteMissionStoreError(code)


def _serialize_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        _fail("INVALID_TIMESTAMP")
    if offset != timedelta(0):
        _fail("NON_UTC_TIMESTAMP")
    normalized = value.astimezone(timezone.utc)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("CORRUPT_RECORD")
    try:
        parsed = datetime.strptime(
            value,
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        _fail("CORRUPT_RECORD")
    if _serialize_timestamp(parsed) != value:
        _fail("CORRUPT_RECORD")
    return parsed


def _request_mapping(request: MissionRequest) -> dict[str, Any]:
    document = request.to_identity_document()
    return {
        "schema_version": request.schema_version,
        "mission_namespace": document["mission_namespace"],
        "mission_ref": document["mission_ref"],
        "mission_type": document["mission_type"],
        "chain_id": document["chain_id"],
        "asset": document["asset"],
        "effects": document["effects"],
    }


def _serialize_request(request: MissionRequest) -> str:
    return json.dumps(
        _request_mapping(request),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _parse_request(value: Any) -> MissionRequest:
    if not isinstance(value, str):
        _fail("CORRUPT_RECORD")
    try:
        mapping = json.loads(value)
        request = MissionRequest.from_mapping(mapping)
    except (
        json.JSONDecodeError,
        MissionModelValidationError,
        TypeError,
        ValueError,
    ):
        _fail("CORRUPT_RECORD")
    if _serialize_request(request) != value:
        _fail("CORRUPT_RECORD")
    return request


_MISSIONS_TABLE_SQL = """
CREATE TABLE missions (
    mission_key TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    request_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0)
)
"""

_EFFECTS_TABLE_SQL = """
CREATE TABLE effects (
    effect_id TEXT PRIMARY KEY,
    mission_key TEXT NOT NULL,
    effect_ref TEXT NOT NULL,
    chain_id INTEGER NOT NULL CHECK (chain_id > 0),
    token_address TEXT NOT NULL,
    token_decimals INTEGER NOT NULL CHECK (token_decimals >= 0),
    recipient TEXT NOT NULL,
    amount_base_units INTEGER NOT NULL CHECK (amount_base_units > 0),
    state TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (mission_key)
        REFERENCES missions(mission_key)
        ON DELETE CASCADE,
    UNIQUE (mission_key, effect_ref)
)
"""

_CREATE_SCHEMA = f"{_MISSIONS_TABLE_SQL};\n{_EFFECTS_TABLE_SQL};\n"

_CANONICAL_TABLE_SQL = {
    "missions": _MISSIONS_TABLE_SQL,
    "effects": _EFFECTS_TABLE_SQL,
}


_MISSION_COLUMNS = (
    ("mission_key", "TEXT", 0, 1),
    ("schema_version", "TEXT", 1, 0),
    ("content_fingerprint", "TEXT", 1, 0),
    ("request_json", "TEXT", 1, 0),
    ("state", "TEXT", 1, 0),
    ("created_at_utc", "TEXT", 1, 0),
    ("updated_at_utc", "TEXT", 1, 0),
    ("revision", "INTEGER", 1, 0),
)
_EFFECT_COLUMNS = (
    ("effect_id", "TEXT", 0, 1),
    ("mission_key", "TEXT", 1, 0),
    ("effect_ref", "TEXT", 1, 0),
    ("chain_id", "INTEGER", 1, 0),
    ("token_address", "TEXT", 1, 0),
    ("token_decimals", "INTEGER", 1, 0),
    ("recipient", "TEXT", 1, 0),
    ("amount_base_units", "INTEGER", 1, 0),
    ("state", "TEXT", 1, 0),
    ("created_at_utc", "TEXT", 1, 0),
    ("updated_at_utc", "TEXT", 1, 0),
)


def _normalize_schema_sql(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("INCOMPATIBLE_SCHEMA")
    normalized = value.strip().rstrip(";").casefold()
    normalized = normalized.translate(
        str.maketrans("", "", '"`[]')
    )
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*([(),])\s*", r"\1", normalized)
    normalized = re.sub(
        r"\s*(>=|<=|<>|!=|=|>|<)\s*",
        r"\1",
        normalized,
    )
    return normalized


class SQLiteMissionStore:
    """SQLite-backed durable source of truth for Mission state only."""

    def __init__(self, database_path: str | Path) -> None:
        if not isinstance(database_path, (str, Path)):
            _fail("INVALID_DATABASE_PATH")
        path_text = str(database_path)
        if not path_text:
            _fail("INVALID_DATABASE_PATH")
        self._database_path = path_text

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=_BUSY_TIMEOUT_MILLISECONDS / 1_000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(
                f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MILLISECONDS}"
            )
            connection.execute("PRAGMA foreign_keys = ON")
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
        except SQLiteMissionStoreError:
            connection.rollback()
            raise
        except sqlite3.DatabaseError:
            connection.rollback()
            _fail("DATABASE_ERROR")
        except Exception:
            connection.rollback()
            raise
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
        except SQLiteMissionStoreError:
            connection.rollback()
            raise
        except MissionTransitionError as error:
            connection.rollback()
            _fail(error.code)
        except sqlite3.DatabaseError:
            connection.rollback()
            _fail("DATABASE_ERROR")
        finally:
            connection.close()

    def initialize(self) -> None:
        """Atomically create or validate the accepted version-1 schema."""

        def operation(connection: sqlite3.Connection) -> None:
            version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if version > _SCHEMA_VERSION:
                _fail("UNSUPPORTED_SCHEMA_VERSION")
            if version == 0:
                existing_tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if existing_tables:
                    _fail("INCOMPATIBLE_SCHEMA")
                for statement in _CREATE_SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(
                    f"PRAGMA user_version = {_SCHEMA_VERSION}"
                )
            elif version != _SCHEMA_VERSION:
                _fail("UNSUPPORTED_SCHEMA_VERSION")
            self._validate_schema(connection)

        self._write(operation)

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        table_rows = tuple(
            connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        tables = {str(row[0]) for row in table_rows}
        if tables != {"missions", "effects"}:
            _fail("INCOMPATIBLE_SCHEMA")

        actual_table_sql = {
            str(row[0]): row[1]
            for row in table_rows
        }
        for table_name, canonical_sql in _CANONICAL_TABLE_SQL.items():
            if _normalize_schema_sql(
                actual_table_sql.get(table_name)
            ) != _normalize_schema_sql(canonical_sql):
                _fail("INCOMPATIBLE_SCHEMA")

        def columns(table: str) -> tuple[tuple[Any, ...], ...]:
            return tuple(
                (row[1], str(row[2]).upper(), row[3], row[5])
                for row in connection.execute(f"PRAGMA table_info({table})")
            )

        if columns("missions") != _MISSION_COLUMNS:
            _fail("INCOMPATIBLE_SCHEMA")
        if columns("effects") != _EFFECT_COLUMNS:
            _fail("INCOMPATIBLE_SCHEMA")

        foreign_keys = tuple(connection.execute("PRAGMA foreign_key_list(effects)"))
        if len(foreign_keys) != 1:
            _fail("INCOMPATIBLE_SCHEMA")
        foreign_key = foreign_keys[0]
        if (
            foreign_key[2],
            foreign_key[3],
            foreign_key[4],
            str(foreign_key[6]).upper(),
        ) != ("missions", "mission_key", "mission_key", "CASCADE"):
            _fail("INCOMPATIBLE_SCHEMA")

        unique_column_sets = {
            tuple(
                row[2]
                for row in connection.execute(
                    f"PRAGMA index_info('{index_row[1]}')"
                )
            )
            for index_row in connection.execute("PRAGMA index_list(effects)")
            if index_row[2]
        }
        if ("mission_key", "effect_ref") not in unique_column_sets:
            _fail("INCOMPATIBLE_SCHEMA")

    def create(self, record: MissionRecord) -> StoredMission:
        """Atomically insert one initial aggregate or return its duplicate."""

        if not isinstance(record, MissionRecord):
            _fail("INVALID_MISSION_RECORD")
        if record.state is not MissionState.RECEIVED or any(
            effect.state is not EffectState.PLANNED
            for effect in record.effects
        ):
            _fail("INVALID_INITIAL_AGGREGATE")

        def operation(connection: sqlite3.Connection) -> StoredMission:
            existing = self._load(connection, record.mission_key)
            if existing is not None:
                if (
                    existing.record.content_fingerprint
                    == record.content_fingerprint
                ):
                    return existing
                _fail("MISSION_CONFLICT")

            connection.execute(
                "INSERT INTO missions ("
                "mission_key, schema_version, content_fingerprint, "
                "request_json, state, created_at_utc, updated_at_utc, revision"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.mission_key,
                    record.schema_version,
                    record.content_fingerprint,
                    _serialize_request(record.request),
                    record.state.value,
                    _serialize_timestamp(record.created_at_utc),
                    _serialize_timestamp(record.updated_at_utc),
                    1,
                ),
            )
            for effect in record.effects:
                self._insert_effect(connection, effect)
            stored = self._load(connection, record.mission_key)
            if stored is None:
                _fail("DATABASE_ERROR")
            return stored

        return self._write(operation)

    def _insert_effect(
        self,
        connection: sqlite3.Connection,
        effect: EffectRecord,
    ) -> None:
        connection.execute(
            "INSERT INTO effects ("
            "effect_id, mission_key, effect_ref, chain_id, token_address, "
            "token_decimals, recipient, amount_base_units, state, "
            "created_at_utc, updated_at_utc"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                effect.effect_id,
                effect.mission_key,
                effect.effect_ref,
                effect.chain_id,
                effect.token_address,
                effect.token_decimals,
                effect.recipient,
                effect.amount_base_units,
                effect.state.value,
                _serialize_timestamp(effect.created_at_utc),
                _serialize_timestamp(effect.updated_at_utc),
            ),
        )

    def get(self, mission_key: str) -> StoredMission | None:
        """Return a validated aggregate without mutating durable state."""

        if not isinstance(mission_key, str):
            _fail("INVALID_MISSION_KEY")
        return self._read(
            lambda connection: self._load(connection, mission_key)
        )

    def transition_mission(
        self,
        mission_key: str,
        expected_revision: int,
        target_state: MissionState,
        updated_at_utc: datetime,
    ) -> StoredMission:
        """Apply the accepted Mission state machine under revision CAS."""

        return self._transition(
            mission_key,
            expected_revision,
            lambda record: apply_mission_transition(
                record,
                target_state,
                updated_at_utc,
            ),
        )

    def transition_effect(
        self,
        mission_key: str,
        effect_ref: str,
        expected_revision: int,
        target_state: EffectState,
        updated_at_utc: datetime,
    ) -> StoredMission:
        """Apply one canonical Effect transition under revision CAS."""

        return self._transition(
            mission_key,
            expected_revision,
            lambda record: apply_effect_transition(
                record,
                effect_ref,
                target_state,
                updated_at_utc,
            ),
        )

    def _transition(
        self,
        mission_key: str,
        expected_revision: int,
        operation: Callable[[MissionRecord], MissionRecord],
    ) -> StoredMission:
        if not isinstance(mission_key, str):
            _fail("INVALID_MISSION_KEY")

        def write(connection: sqlite3.Connection) -> StoredMission:
            current = self._load(connection, mission_key)
            if current is None:
                _fail("MISSION_NOT_FOUND")
            if (
                type(expected_revision) is not int
                or current.revision != expected_revision
            ):
                _fail("STALE_REVISION")
            transitioned = operation(current.record)
            return self._rewrite(
                connection,
                transitioned,
                expected_revision,
            )

        return self._write(write)

    def _rewrite(
        self,
        connection: sqlite3.Connection,
        record: MissionRecord,
        expected_revision: int,
    ) -> StoredMission:
        mission_update = connection.execute(
            "UPDATE missions SET state = ?, updated_at_utc = ?, "
            "revision = revision + 1 "
            "WHERE mission_key = ? AND revision = ?",
            (
                record.state.value,
                _serialize_timestamp(record.updated_at_utc),
                record.mission_key,
                expected_revision,
            ),
        )
        if mission_update.rowcount != 1:
            _fail("STALE_REVISION")
        for effect in record.effects:
            effect_update = connection.execute(
                "UPDATE effects SET state = ?, updated_at_utc = ? "
                "WHERE effect_id = ? AND mission_key = ?",
                (
                    effect.state.value,
                    _serialize_timestamp(effect.updated_at_utc),
                    effect.effect_id,
                    record.mission_key,
                ),
            )
            if effect_update.rowcount != 1:
                _fail("CORRUPT_RECORD")
        stored = self._load(connection, record.mission_key)
        if stored is None or stored.revision != expected_revision + 1:
            _fail("DATABASE_ERROR")
        return stored

    def list_restart_candidates(self) -> tuple[StoredMission, ...]:
        """List Missions requiring reconciliation, never permission to resend."""

        def operation(
            connection: sqlite3.Connection,
        ) -> tuple[StoredMission, ...]:
            terminal_values = tuple(
                state.value
                for state in sorted(
                    _TERMINAL_MISSION_STATES,
                    key=lambda state: state.value,
                )
            )
            placeholders = ", ".join("?" for _ in terminal_values)
            keys = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT mission_key FROM missions "
                    f"WHERE state NOT IN ({placeholders}) "
                    "ORDER BY mission_key",
                    terminal_values,
                )
            )
            candidates: list[StoredMission] = []
            for mission_key in keys:
                stored = self._load(connection, mission_key)
                if stored is None:
                    _fail("CORRUPT_RECORD")
                candidates.append(stored)
            return tuple(candidates)

        return self._read(operation)

    def _load(
        self,
        connection: sqlite3.Connection,
        mission_key: str,
    ) -> StoredMission | None:
        mission_row = connection.execute(
            "SELECT mission_key, schema_version, content_fingerprint, "
            "request_json, state, created_at_utc, updated_at_utc, revision "
            "FROM missions WHERE mission_key = ?",
            (mission_key,),
        ).fetchone()
        if mission_row is None:
            return None
        effect_rows = tuple(
            connection.execute(
                "SELECT effect_id, mission_key, effect_ref, chain_id, "
                "token_address, token_decimals, recipient, "
                "amount_base_units, state, created_at_utc, updated_at_utc "
                "FROM effects WHERE mission_key = ? ORDER BY effect_ref",
                (mission_key,),
            )
        )
        return self._reconstruct(mission_row, effect_rows)

    def _reconstruct(
        self,
        mission_row: sqlite3.Row,
        effect_rows: Sequence[sqlite3.Row],
    ) -> StoredMission:
        try:
            request = _parse_request(mission_row["request_json"])
            effects = tuple(
                EffectRecord(
                    mission_key=row["mission_key"],
                    effect_ref=row["effect_ref"],
                    effect_id=row["effect_id"],
                    chain_id=row["chain_id"],
                    token_address=row["token_address"],
                    token_decimals=row["token_decimals"],
                    recipient=row["recipient"],
                    amount_base_units=row["amount_base_units"],
                    state=EffectState(row["state"]),
                    created_at_utc=_parse_timestamp(row["created_at_utc"]),
                    updated_at_utc=_parse_timestamp(row["updated_at_utc"]),
                )
                for row in effect_rows
            )
            record = MissionRecord(
                schema_version=mission_row["schema_version"],
                mission_key=mission_row["mission_key"],
                content_fingerprint=mission_row["content_fingerprint"],
                request=request,
                state=MissionState(mission_row["state"]),
                effects=effects,
                created_at_utc=_parse_timestamp(
                    mission_row["created_at_utc"]
                ),
                updated_at_utc=_parse_timestamp(
                    mission_row["updated_at_utc"]
                ),
            )
            revision = mission_row["revision"]
            if type(revision) is not int or revision < 1:
                _fail("CORRUPT_RECORD")
        except SQLiteMissionStoreError:
            raise
        except (
            KeyError,
            MissionModelValidationError,
            TypeError,
            ValueError,
        ):
            _fail("CORRUPT_RECORD")
        return StoredMission(record=record, revision=revision)
