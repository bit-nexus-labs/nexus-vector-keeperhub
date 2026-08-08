"""Capture an immutable provider transaction binding for one rehearsal attempt.

This helper performs at most one read-only KeeperHub execution-status GET. It
never signs, broadcasts, or retries execution. A completed provider observation
is bound to the durable attempt/provider-reference identity and stored locally
for later independent chain verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexus_vector.integrations.keeperhub_execution_status import (  # noqa: E402
    KeeperHubExecutionStatus,
    KeeperHubExecutionStatusObserver,
)
from nexus_vector.integrations.keeperhub_http_transport import (  # noqa: E402
    KeeperHubHttpTransport,
    KeeperHubHttpTransportError,
)
from nexus_vector.persistence.sqlite_provider_execution_reference_store import (  # noqa: E402
    SQLiteProviderExecutionReferenceStore,
)

_TOOL_SCHEMA = "nexus-vector.keeperhub-provider-transaction-binding.v1"
_REHEARSAL_SCHEMA = "nexus-vector.keeperhub-rehearsal-execution.v1"
_API_KEY_ENV = "KEEPERHUB_API_KEY"
_RUN_REF_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_HASH_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")
_MAX_JSON_BYTES = 65_536


class ProviderTransactionBindingError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ProviderTransactionBindingError(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        _fail("NON_UTC_TIMESTAMP")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_run_ref(value: Any) -> str:
    if not isinstance(value, str) or _RUN_REF_PATTERN.fullmatch(value) is None:
        _fail("INVALID_RUN_REF")
    return value


def _validate_hash(value: Any) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        _fail("INVALID_TRANSACTION_HASH")
    return value.lower()


def _read_json(path: Path, *, missing_code: str, corrupt_code: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        _fail(missing_code)
    except OSError:
        _fail(corrupt_code)
    if len(raw) > _MAX_JSON_BYTES:
        _fail(corrupt_code)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(corrupt_code)
    if not isinstance(value, dict):
        _fail(corrupt_code)
    return value


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _provider_reference_fingerprint(value: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("INVALID_PROVIDER_REFERENCE")
    return "khref_sha256_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _run_root(run_ref: str, base_root: Path | None = None) -> Path:
    root = base_root or (
        Path.home() / ".nexus-vector" / "keeperhub-rehearsal-execution-v1"
    )
    return root / _validate_run_ref(run_ref)


def _paths(run_ref: str, base_root: Path | None = None) -> dict[str, Path]:
    root = _run_root(run_ref, base_root)
    return {
        "root": root,
        "action_sheet": root / "private_action_sheet.json",
        "provider_references": root / "provider_references.sqlite3",
        "binding": root / "provider_transaction_binding.json",
    }


def _load_identity(run_ref: str, base_root: Path | None = None):
    paths = _paths(run_ref, base_root)
    sheet = _read_json(
        paths["action_sheet"],
        missing_code="ACTION_SHEET_NOT_FOUND",
        corrupt_code="ACTION_SHEET_INVALID",
    )
    if sheet.get("schema") != _REHEARSAL_SCHEMA:
        _fail("ACTION_SHEET_SCHEMA_MISMATCH")
    if sheet.get("run_ref") != run_ref:
        _fail("ACTION_SHEET_RUN_REF_MISMATCH")
    attempt_id = sheet.get("attempt_id")
    mission_key = sheet.get("mission_key")
    effect_id = sheet.get("effect_id")
    request_fingerprint = sheet.get("request_fingerprint")
    if not all(
        isinstance(item, str) and item
        for item in (attempt_id, mission_key, effect_id, request_fingerprint)
    ):
        _fail("ACTION_SHEET_IDENTITY_INVALID")
    reference = SQLiteProviderExecutionReferenceStore(
        paths["provider_references"]
    ).get(attempt_id)
    if reference is None:
        _fail("PROVIDER_REFERENCE_NOT_FOUND")
    if reference.request_fingerprint != request_fingerprint:
        _fail("PROVIDER_REFERENCE_REQUEST_MISMATCH")
    return paths, sheet, reference


def _stable_binding_material(
    *,
    run_ref: str,
    sheet: Mapping[str, Any],
    reference: Any,
    transaction_hash: str,
    transaction_link: str | None,
) -> dict[str, Any]:
    return {
        "schema": _TOOL_SCHEMA,
        "run_ref": run_ref,
        "mission_key": sheet["mission_key"],
        "effect_id": sheet["effect_id"],
        "attempt_id": sheet["attempt_id"],
        "request_fingerprint": sheet["request_fingerprint"],
        "provider_namespace": reference.provider_namespace,
        "provider_reference_fingerprint": _provider_reference_fingerprint(
            reference.provider_reference
        ),
        "provider_status": "completed",
        "transaction_hash": _validate_hash(transaction_hash),
        "transaction_link": transaction_link,
    }


def _validate_existing_binding(
    value: Mapping[str, Any],
    *,
    run_ref: str,
    sheet: Mapping[str, Any],
    reference: Any,
) -> dict[str, Any]:
    if value.get("schema") != _TOOL_SCHEMA:
        _fail("BINDING_SCHEMA_MISMATCH")
    required = {
        "schema",
        "run_ref",
        "mission_key",
        "effect_id",
        "attempt_id",
        "request_fingerprint",
        "provider_namespace",
        "provider_reference_fingerprint",
        "provider_status",
        "transaction_hash",
        "transaction_link",
        "bound_at_utc",
    }
    if set(value.keys()) != required:
        _fail("BINDING_FIELD_MISMATCH")
    expected_identity = {
        "run_ref": run_ref,
        "mission_key": sheet["mission_key"],
        "effect_id": sheet["effect_id"],
        "attempt_id": sheet["attempt_id"],
        "request_fingerprint": sheet["request_fingerprint"],
        "provider_namespace": reference.provider_namespace,
        "provider_reference_fingerprint": _provider_reference_fingerprint(
            reference.provider_reference
        ),
        "provider_status": "completed",
    }
    for field, expected in expected_identity.items():
        if value.get(field) != expected:
            _fail("BINDING_IDENTITY_MISMATCH")
    _validate_hash(value.get("transaction_hash"))
    if value.get("transaction_link") is not None and not isinstance(
        value.get("transaction_link"), str
    ):
        _fail("BINDING_TRANSACTION_LINK_INVALID")
    if not isinstance(value.get("bound_at_utc"), str):
        _fail("BINDING_TIMESTAMP_INVALID")
    return dict(value)


def _exclusive_write(path: Path, document: Mapping[str, Any]) -> None:
    payload = _canonical_json(document) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _fail("BINDING_ALREADY_EXISTS")
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def capture_provider_transaction_binding(
    *,
    api_key: str,
    run_ref: str,
    base_root: Path | None = None,
    observed_at: datetime | None = None,
    http_transport_factory: Any = KeeperHubHttpTransport,
) -> dict[str, Any]:
    checked_ref = _validate_run_ref(run_ref)
    paths, sheet, reference = _load_identity(checked_ref, base_root)

    if paths["binding"].exists():
        existing = _validate_existing_binding(
            _read_json(
                paths["binding"],
                missing_code="BINDING_NOT_FOUND",
                corrupt_code="BINDING_INVALID",
            ),
            run_ref=checked_ref,
            sheet=sheet,
            reference=reference,
        )
        return {
            "schema": _TOOL_SCHEMA,
            "status": "PASS",
            "decision": "ALREADY_BOUND",
            "run_ref": checked_ref,
            "status_gets": 0,
            "keeperhub_mutating_calls": 0,
            "broadcast_posts": 0,
            "transaction_hash": existing["transaction_hash"],
            "transaction_link": existing["transaction_link"],
            "provider_reference": existing["provider_reference_fingerprint"],
            "retry_broadcast": False,
        }

    if not isinstance(api_key, str) or not api_key:
        _fail("LOCAL_API_KEY_NOT_SET")
    transport = http_transport_factory(api_key)
    try:
        observation = KeeperHubExecutionStatusObserver(transport).observe(reference)
    except Exception as error:
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": checked_ref,
            "reason": getattr(error, "code", "STATUS_OUTCOME_UNKNOWN"),
            "status_gets": 1,
            "keeperhub_mutating_calls": 0,
            "broadcast_posts": 0,
            "retry_broadcast": False,
        }

    if observation.status is not KeeperHubExecutionStatus.COMPLETED:
        return {
            "schema": _TOOL_SCHEMA,
            "status": "WAIT",
            "run_ref": checked_ref,
            "provider_status": observation.status.value,
            "status_gets": 1,
            "poll_after_seconds": observation.poll_after_seconds,
            "keeperhub_mutating_calls": 0,
            "broadcast_posts": 0,
            "retry_read_only_status": True,
            "retry_broadcast": False,
        }
    if observation.transaction_hash is None:
        _fail("COMPLETED_STATUS_MISSING_TRANSACTION_HASH")
    transaction_hash = _validate_hash(observation.transaction_hash)
    material = _stable_binding_material(
        run_ref=checked_ref,
        sheet=sheet,
        reference=reference,
        transaction_hash=transaction_hash,
        transaction_link=observation.transaction_link,
    )
    document = {
        **material,
        "bound_at_utc": _timestamp(observed_at or _utc_now()),
    }
    _exclusive_write(paths["binding"], document)
    return {
        "schema": _TOOL_SCHEMA,
        "status": "PASS",
        "decision": "BOUND_TO_DURABLE_ATTEMPT",
        "run_ref": checked_ref,
        "provider_status": "completed",
        "status_gets": 1,
        "keeperhub_mutating_calls": 0,
        "broadcast_posts": 0,
        "transaction_hash": transaction_hash,
        "transaction_link": observation.transaction_link,
        "provider_reference": material["provider_reference_fingerprint"],
        "binding_persisted": True,
        "retry_broadcast": False,
    }


def _required_api_key() -> str:
    value = os.environ.get(_API_KEY_ENV)
    if not isinstance(value, str) or not value:
        _fail("LOCAL_API_KEY_NOT_SET")
    return value


def _emit(value: Mapping[str, Any]) -> int:
    print(json.dumps(dict(value), ensure_ascii=False, sort_keys=True))
    if value.get("status") == "PASS":
        return 0
    if value.get("status") == "WAIT":
        return 3
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture one immutable KeeperHub provider transaction binding."
    )
    parser.add_argument("--run-ref", required=True)
    args = parser.parse_args()
    try:
        return _emit(
            capture_provider_transaction_binding(
                api_key=_required_api_key(),
                run_ref=args.run_ref,
            )
        )
    except (ProviderTransactionBindingError, KeeperHubHttpTransportError) as error:
        return _emit(
            {
                "schema": _TOOL_SCHEMA,
                "status": "STOP",
                "run_ref": args.run_ref,
                "reason": error.code,
                "keeperhub_mutating_calls": 0,
                "broadcast_posts": 0,
                "retry_broadcast": False,
            }
        )
    except Exception:
        return _emit(
            {
                "schema": _TOOL_SCHEMA,
                "status": "STOP",
                "run_ref": args.run_ref,
                "reason": "UNEXPECTED_LOCAL_FAILURE",
                "keeperhub_mutating_calls": 0,
                "broadcast_posts": 0,
                "retry_broadcast": False,
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
