"""Provider-neutral durable execution-attempt identity and state rules."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

_ATTEMPT_ID_DOMAIN = b"nexus-vector:execution-attempt:v1\x00"
_REQUEST_FINGERPRINT_DOMAIN = b"nexus-vector:execution-request:v1\x00"
_MISSION_KEY_PATTERN = re.compile(r"msn_[0-9a-f]{64}")
_EFFECT_ID_PATTERN = re.compile(r"eff_[0-9a-f]{64}")
_ATTEMPT_ID_PATTERN = re.compile(r"att_[0-9a-f]{64}")
_REQUEST_FINGERPRINT_PATTERN = re.compile(r"xrf_[0-9a-f]{64}")


class ExecutionAttemptState(str, Enum):
    PREPARED = "PREPARED"
    IN_FLIGHT = "IN_FLIGHT"
    PROVIDER_ACKNOWLEDGED = "PROVIDER_ACKNOWLEDGED"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    VERIFIED = "VERIFIED"
    FAILED_FINAL = "FAILED_FINAL"
    BLOCKED = "BLOCKED"


_TRANSITIONS = {
    ExecutionAttemptState.PREPARED: frozenset(
        {
            ExecutionAttemptState.IN_FLIGHT,
            ExecutionAttemptState.VERIFIED,
            ExecutionAttemptState.BLOCKED,
        }
    ),
    ExecutionAttemptState.IN_FLIGHT: frozenset(
        {
            ExecutionAttemptState.PROVIDER_ACKNOWLEDGED,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
            ExecutionAttemptState.FAILED_FINAL,
            ExecutionAttemptState.VERIFIED,
            ExecutionAttemptState.BLOCKED,
        }
    ),
    ExecutionAttemptState.PROVIDER_ACKNOWLEDGED: frozenset(
        {
            ExecutionAttemptState.VERIFIED,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
            ExecutionAttemptState.FAILED_FINAL,
            ExecutionAttemptState.BLOCKED,
        }
    ),
    ExecutionAttemptState.EXECUTION_UNKNOWN: frozenset(
        {
            ExecutionAttemptState.VERIFIED,
            ExecutionAttemptState.FAILED_FINAL,
            ExecutionAttemptState.BLOCKED,
        }
    ),
    ExecutionAttemptState.VERIFIED: frozenset(),
    ExecutionAttemptState.FAILED_FINAL: frozenset(),
    ExecutionAttemptState.BLOCKED: frozenset(),
}


class ExecutionAttemptError(ValueError):
    """Machine-classifiable execution-journal error without payload echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ExecutionAttemptError(code)


def _required_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    normalized = unicodedata.normalize("NFC", value)
    if not normalized or len(normalized) > 256:
        _fail(code)
    return normalized


def _utc(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        _fail("INVALID_TIMESTAMP")
    if offset != timedelta(0):
        _fail("NON_UTC_TIMESTAMP")
    return value


def _normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return unicodedata.normalize("NFC", value) if isinstance(value, str) else value
    if type(value) is int:
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            _fail("INVALID_REQUEST_MATERIAL")
        normalized_items: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized_items:
                _fail("DUPLICATE_NORMALIZED_KEY")
            normalized_items[normalized_key] = _normalize_json(item)
        return {
            key: normalized_items[key]
            for key in sorted(normalized_items)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_json(item) for item in value]
    _fail("INVALID_REQUEST_MATERIAL")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _normalize_json(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def derive_attempt_id(effect_id: str) -> str:
    if not isinstance(effect_id, str) or _EFFECT_ID_PATTERN.fullmatch(effect_id) is None:
        _fail("INVALID_EFFECT_ID")
    digest = hashlib.sha256(_ATTEMPT_ID_DOMAIN + effect_id.encode("ascii")).hexdigest()
    return f"att_{digest}"


def derive_request_fingerprint(
    provider_namespace: str,
    request_key: str,
    request_material: Any,
) -> str:
    namespace = _required_string(provider_namespace, "INVALID_PROVIDER_NAMESPACE")
    key = _required_string(request_key, "INVALID_REQUEST_KEY")
    material = _canonical_json(
        {
            "provider_namespace": namespace,
            "request_key": key,
            "request_material": request_material,
        }
    )
    digest = hashlib.sha256(_REQUEST_FINGERPRINT_DOMAIN + material).hexdigest()
    return f"xrf_{digest}"


@dataclass(frozen=True)
class ExecutionAttemptPlan:
    mission_key: str
    effect_id: str
    provider_namespace: str
    request_key: str
    request_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.mission_key, str) or _MISSION_KEY_PATTERN.fullmatch(self.mission_key) is None:
            _fail("INVALID_MISSION_KEY")
        if not isinstance(self.effect_id, str) or _EFFECT_ID_PATTERN.fullmatch(self.effect_id) is None:
            _fail("INVALID_EFFECT_ID")
        object.__setattr__(
            self,
            "provider_namespace",
            _required_string(self.provider_namespace, "INVALID_PROVIDER_NAMESPACE"),
        )
        object.__setattr__(
            self,
            "request_key",
            _required_string(self.request_key, "INVALID_REQUEST_KEY"),
        )
        if (
            not isinstance(self.request_fingerprint, str)
            or _REQUEST_FINGERPRINT_PATTERN.fullmatch(self.request_fingerprint) is None
        ):
            _fail("INVALID_REQUEST_FINGERPRINT")

    @property
    def attempt_id(self) -> str:
        return derive_attempt_id(self.effect_id)


@dataclass(frozen=True)
class ExecutionAttemptRecord:
    attempt_id: str
    plan: ExecutionAttemptPlan
    state: ExecutionAttemptState
    created_at_utc: datetime
    updated_at_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ExecutionAttemptPlan):
            _fail("INVALID_ATTEMPT_PLAN")
        if not isinstance(self.attempt_id, str) or _ATTEMPT_ID_PATTERN.fullmatch(self.attempt_id) is None:
            _fail("INVALID_ATTEMPT_ID")
        if self.attempt_id != self.plan.attempt_id:
            _fail("ATTEMPT_ID_MISMATCH")
        if not isinstance(self.state, ExecutionAttemptState):
            _fail("INVALID_ATTEMPT_STATE")
        created = _utc(self.created_at_utc)
        updated = _utc(self.updated_at_utc)
        if updated < created:
            _fail("REVERSED_TIMESTAMP")


def build_execution_attempt_plan(
    *,
    mission_key: str,
    effect_id: str,
    provider_namespace: str,
    request_key: str,
    request_material: Any,
) -> ExecutionAttemptPlan:
    return ExecutionAttemptPlan(
        mission_key=mission_key,
        effect_id=effect_id,
        provider_namespace=provider_namespace,
        request_key=request_key,
        request_fingerprint=derive_request_fingerprint(
            provider_namespace,
            request_key,
            request_material,
        ),
    )


def create_initial_execution_attempt(
    plan: ExecutionAttemptPlan,
    created_at_utc: datetime,
) -> ExecutionAttemptRecord:
    if not isinstance(plan, ExecutionAttemptPlan):
        _fail("INVALID_ATTEMPT_PLAN")
    timestamp = _utc(created_at_utc)
    return ExecutionAttemptRecord(
        attempt_id=plan.attempt_id,
        plan=plan,
        state=ExecutionAttemptState.PREPARED,
        created_at_utc=timestamp,
        updated_at_utc=timestamp,
    )


def transition_execution_attempt(
    record: ExecutionAttemptRecord,
    target_state: ExecutionAttemptState,
    updated_at_utc: datetime,
) -> ExecutionAttemptRecord:
    if not isinstance(record, ExecutionAttemptRecord):
        _fail("INVALID_ATTEMPT_RECORD")
    if not isinstance(target_state, ExecutionAttemptState):
        _fail("INVALID_ATTEMPT_TARGET_STATE")
    timestamp = _utc(updated_at_utc)
    if timestamp < record.updated_at_utc:
        _fail("TIMESTAMP_BEFORE_CURRENT")
    if target_state not in _TRANSITIONS[record.state]:
        _fail("ATTEMPT_TRANSITION_NOT_ALLOWED")
    return replace(record, state=target_state, updated_at_utc=timestamp)
