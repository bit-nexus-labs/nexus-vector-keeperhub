"""Prepare and execute one durable KeeperHub simulation-only effect.

This operator tool has no broadcast command or broadcast-capable flag. It uses a
fixed Base Sepolia USDC diagnostic effect, consumes one durable SIMULATION slot
before the single POST, emits sanitized JSON only, and never retries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexus_vector.domain.execution_attempts import (  # noqa: E402
    ExecutionAttemptPlan,
    build_execution_attempt_plan,
)
from nexus_vector.domain.mission_identity import (  # noqa: E402
    SCHEMA_VERSION,
    build_mission_identity,
)
from nexus_vector.integrations.keeperhub_controlled_execution import (  # noqa: E402
    KeeperHubControlledExecutionError,
    KeeperHubControlledSimulationService,
    KeeperHubSimulationAuthorization,
)
from nexus_vector.integrations.keeperhub_direct_execution import (  # noqa: E402
    KEEPERHUB_PROVIDER_NAMESPACE,
    KeeperHubTransferIntent,
    KeeperHubTransportResponse,
)
from nexus_vector.integrations.keeperhub_http_transport import (  # noqa: E402
    KeeperHubHttpTransport,
    KeeperHubHttpTransportError,
)
from nexus_vector.integrations.keeperhub_simulation_runtime import (  # noqa: E402
    KeeperHubSimulationOnlyTransport,
    KeeperHubSimulationRuntimeError,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (  # noqa: E402
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_keeperhub_authorization_ledger import (  # noqa: E402
    KeeperHubAuthorizationPhase,
    SQLiteKeeperHubAuthorizationLedger,
)

_TOOL_SCHEMA = "nexus-vector.keeperhub-one-shot-simulation.v1"
_PROBE_NAME = "KEEPERHUB_ONE_SHOT_SIMULATION_V1"
_RECIPIENT_ENV = "NEXUS_VECTOR_SIMULATION_RECIPIENT"
_APPROVAL_ENV = "NEXUS_VECTOR_SIMULATION_APPROVAL"
_API_KEY_ENV = "KEEPERHUB_API_KEY"
_BASE_SEPOLIA_CHAIN_ID = 84532
_BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
_TOKEN_DECIMALS = 6
_AMOUNT_BASE_UNITS = 1
_AMOUNT_DECIMAL = "0.000001"
_MISSION_NAMESPACE = "nexus-vector.keeperhub"
_MISSION_REF = "keeperhub-first-simulation-20260803"
_MISSION_TYPE = "DIAGNOSTIC_SIMULATION_ONLY"
_EFFECT_REF = "effect-01-wallet-balance-gas-readiness"
_EXECUTION_SURFACE = "DIRECT_EXECUTION"
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_SAFE_DIGITS = re.compile(r"[0-9]{1,32}")

# Only provider codes already observed and reviewed may cross the sanitized
# operator boundary. Unknown strings, messages, nested objects, and headers are
# intentionally discarded.
_SAFE_PROVIDER_ERROR_CODES = frozenset({"insufficient_scope"})

_PREPARE_INPUT_CORRECTABLE = frozenset(
    {
        "LOCAL_RECIPIENT_NOT_SET",
        "INVALID_RECIPIENT_ADDRESS",
    }
)
_EXECUTE_INPUT_CORRECTABLE = frozenset(
    {
        "LOCAL_API_KEY_NOT_SET",
        "LOCAL_SIMULATION_APPROVAL_NOT_SET",
        "INVALID_LOCAL_API_KEY",
        "SIMULATION_APPROVAL_MISMATCH",
    }
)
_LOCAL_STATE_REVIEW_REQUIRED = frozenset(
    {
        "ACTION_SHEET_ALREADY_EXISTS",
        "ACTION_SHEET_NOT_FOUND",
        "ACTION_SHEET_READ_FAILED",
        "ACTION_SHEET_TOO_LARGE",
        "ACTION_SHEET_CORRUPT",
        "ACTION_SHEET_FIELD_MISMATCH",
        "ACTION_SHEET_SCHEMA_MISMATCH",
        "ACTION_SHEET_PURPOSE_MISMATCH",
        "ACTION_SHEET_SURFACE_MISMATCH",
        "ACTION_SHEET_BUDGET_MISMATCH",
        "INVALID_ACTION_SHEET_TIMESTAMP",
        "ACTION_SHEET_FINGERPRINT_MISMATCH",
        "MISSION_FINGERPRINT_MISMATCH",
    }
)


class OneShotSimulationError(RuntimeError):
    """Machine-classifiable local operator failure without raw-value echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise OneShotSimulationError(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(_canonical_json(value)).hexdigest()


def _mask_address(value: str) -> str:
    if _EVM_ADDRESS.fullmatch(value) is None:
        return "<redacted>"
    return f"{value[:8]}…{value[-6:]}"


def _state_root() -> Path:
    return Path.home() / ".nexus-vector" / "keeperhub-one-shot-simulation-v1"


def _paths(state_root: Path) -> dict[str, Path]:
    return {
        "root": state_root,
        "action_sheet": state_root / "private_action_sheet.json",
        "attempts": state_root / "execution_attempts.sqlite3",
        "authorizations": state_root / "keeperhub_authorizations.sqlite3",
    }


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _exclusive_json_write(path: Path, value: Mapping[str, Any]) -> None:
    payload = _canonical_json(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
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


def _read_action_sheet(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        _fail("ACTION_SHEET_NOT_FOUND")
    except OSError:
        _fail("ACTION_SHEET_READ_FAILED")
    if len(raw) > 32_768:
        _fail("ACTION_SHEET_TOO_LARGE")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ACTION_SHEET_CORRUPT")
    if not isinstance(value, dict):
        _fail("ACTION_SHEET_CORRUPT")
    return value


def _mission_document(recipient: str) -> dict[str, Any]:
    return {
        "mission_namespace": _MISSION_NAMESPACE,
        "mission_ref": _MISSION_REF,
        "mission_type": _MISSION_TYPE,
        "chain_id": _BASE_SEPOLIA_CHAIN_ID,
        "asset": {
            "token_address": _BASE_SEPOLIA_USDC,
            "decimals": _TOKEN_DECIMALS,
        },
        "effects": [
            {
                "effect_ref": _EFFECT_REF,
                "recipient": recipient,
                "amount_base_units": _AMOUNT_BASE_UNITS,
            }
        ],
    }


def _intent(recipient: str) -> KeeperHubTransferIntent:
    return KeeperHubTransferIntent(
        chain_id=_BASE_SEPOLIA_CHAIN_ID,
        recipient_address=recipient,
        amount_base_units=_AMOUNT_BASE_UNITS,
        token_address=_BASE_SEPOLIA_USDC,
        token_decimals=_TOKEN_DECIMALS,
    )


def _request_key(mission_key: str, effect_id: str) -> str:
    return _sha256(
        "simreq_",
        {
            "mission_key": mission_key,
            "effect_id": effect_id,
            "surface": _EXECUTION_SURFACE,
            "schema": _TOOL_SCHEMA,
        },
    )


def _build_plan(
    recipient: str,
) -> tuple[KeeperHubTransferIntent, ExecutionAttemptPlan, dict[str, Any]]:
    selected_intent = _intent(recipient)
    identity = build_mission_identity(
        _mission_document(recipient),
        schema_version=SCHEMA_VERSION,
    )
    if len(identity.effect_ids) != 1:
        _fail("INVALID_EFFECT_COUNT")
    plan = build_execution_attempt_plan(
        mission_key=identity.mission_key,
        effect_id=identity.effect_ids[0],
        provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
        request_key=_request_key(identity.mission_key, identity.effect_ids[0]),
        request_material=selected_intent.request_material,
    )
    return selected_intent, plan, {
        "mission_content_fingerprint": identity.content_fingerprint,
    }


def _action_sheet(recipient: str, prepared_at: datetime) -> dict[str, Any]:
    selected_intent, plan, identity_values = _build_plan(recipient)
    immutable = {
        "schema": _TOOL_SCHEMA,
        "purpose": "DIAGNOSTIC_SIMULATION_ONLY_NEVER_BROADCAST",
        "execution_surface": _EXECUTION_SURFACE,
        "chain_id": selected_intent.chain_id,
        "token_address": selected_intent.token_address,
        "token_decimals": selected_intent.token_decimals,
        "amount_base_units": selected_intent.amount_base_units,
        "amount_decimal": selected_intent.amount_decimal_string,
        "recipient_address": selected_intent.recipient_address,
        "mission_key": plan.mission_key,
        "effect_id": plan.effect_id,
        "attempt_id": plan.attempt_id,
        "request_key": plan.request_key,
        "request_fingerprint": plan.request_fingerprint,
        **identity_values,
    }
    action_sheet_id = _sha256("sheet_", immutable)
    approval_reference = _sha256(
        "simappr_",
        {
            "action_sheet_id": action_sheet_id,
            "attempt_id": plan.attempt_id,
            "request_fingerprint": plan.request_fingerprint,
        },
    )
    approval_challenge = (
        "SIMULATE-"
        + hashlib.sha256(
            (action_sheet_id + plan.request_fingerprint).encode("ascii")
        ).hexdigest()[:20]
    )
    return {
        **immutable,
        "action_sheet_id": action_sheet_id,
        "approval_reference": approval_reference,
        "approval_challenge": approval_challenge,
        "prepared_at_utc": _timestamp(prepared_at),
        "maximum_simulation_posts": 1,
        "maximum_broadcast_posts": 0,
    }


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("INVALID_ACTION_SHEET_TIMESTAMP")
    try:
        parsed = datetime.strptime(
            value,
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        _fail("INVALID_ACTION_SHEET_TIMESTAMP")
    if _timestamp(parsed) != value:
        _fail("INVALID_ACTION_SHEET_TIMESTAMP")
    return parsed


def _validate_action_sheet(
    value: Mapping[str, Any],
) -> tuple[KeeperHubTransferIntent, ExecutionAttemptPlan]:
    expected_fields = {
        "schema",
        "purpose",
        "execution_surface",
        "chain_id",
        "token_address",
        "token_decimals",
        "amount_base_units",
        "amount_decimal",
        "recipient_address",
        "mission_key",
        "effect_id",
        "attempt_id",
        "request_key",
        "request_fingerprint",
        "mission_content_fingerprint",
        "action_sheet_id",
        "approval_reference",
        "approval_challenge",
        "prepared_at_utc",
        "maximum_simulation_posts",
        "maximum_broadcast_posts",
    }
    if set(value.keys()) != expected_fields:
        _fail("ACTION_SHEET_FIELD_MISMATCH")
    if value.get("schema") != _TOOL_SCHEMA:
        _fail("ACTION_SHEET_SCHEMA_MISMATCH")
    if value.get("purpose") != "DIAGNOSTIC_SIMULATION_ONLY_NEVER_BROADCAST":
        _fail("ACTION_SHEET_PURPOSE_MISMATCH")
    if value.get("execution_surface") != _EXECUTION_SURFACE:
        _fail("ACTION_SHEET_SURFACE_MISMATCH")
    if (
        value.get("maximum_simulation_posts") != 1
        or value.get("maximum_broadcast_posts") != 0
    ):
        _fail("ACTION_SHEET_BUDGET_MISMATCH")
    recipient = value.get("recipient_address")
    if not isinstance(recipient, str) or _EVM_ADDRESS.fullmatch(recipient) is None:
        _fail("INVALID_RECIPIENT_ADDRESS")
    selected_intent, plan, identity_values = _build_plan(recipient)
    regenerated = _action_sheet(
        recipient,
        _parse_timestamp(value.get("prepared_at_utc")),
    )
    for field in expected_fields - {"prepared_at_utc"}:
        if regenerated[field] != value[field]:
            _fail("ACTION_SHEET_FINGERPRINT_MISMATCH")
    if (
        identity_values["mission_content_fingerprint"]
        != value["mission_content_fingerprint"]
    ):
        _fail("MISSION_FINGERPRINT_MISMATCH")
    return selected_intent, plan


def _safe_scalar(value: Any) -> str | int | bool | None:
    if value is None or type(value) in {bool, int}:
        return value
    if (
        isinstance(value, str)
        and len(value) <= 128
        and all(31 < ord(character) < 127 for character in value)
    ):
        return value
    return None


def _safe_provider_error_code(value: Any) -> str | None:
    if isinstance(value, str) and value in _SAFE_PROVIDER_ERROR_CODES:
        return value
    return None


def _sanitize_transport_error(
    error: KeeperHubHttpTransportError,
) -> dict[str, Any] | None:
    summary: dict[str, Any] = {}
    if type(error.http_status) is int and 100 <= error.http_status <= 599:
        summary["http_status"] = error.http_status
    provider_code = _safe_provider_error_code(error.provider_error_code)
    if provider_code is not None:
        summary["provider_error_code"] = provider_code
    return summary or None


def _sanitize_response(response: KeeperHubTransportResponse) -> dict[str, Any]:
    body = response.body
    result: dict[str, Any] = {"http_status": response.status_code}
    for source, target in (
        ("success", "success"),
        ("status", "provider_status"),
        ("wouldRevert", "would_revert"),
        ("value", "value"),
        ("gasEstimate", "gas_estimate"),
    ):
        safe = _safe_scalar(body.get(source))
        if safe is not None:
            if (
                target == "gas_estimate"
                and isinstance(safe, str)
                and _SAFE_DIGITS.fullmatch(safe) is None
            ):
                continue
            result[target] = safe
    provider_code = _safe_provider_error_code(body.get("error"))
    if provider_code is not None:
        result["provider_error_code"] = provider_code
    for source, target in (
        ("from", "from_masked"),
        ("to", "to_masked"),
    ):
        value = body.get(source)
        if isinstance(value, str) and _EVM_ADDRESS.fullmatch(value) is not None:
            result[target] = _mask_address(value)
    result["simulated_return_present"] = "simulatedReturnValue" in body
    return result


class _CapturingSimulationTransport:
    """Capture only a bounded summary after simulation-only validation succeeds."""

    def __init__(self, delegate: KeeperHubSimulationOnlyTransport) -> None:
        self._delegate = delegate
        self.summary: dict[str, Any] | None = None
        self.calls = 0

    def post_transfer(
        self,
        body: Mapping[str, Any],
        *,
        idempotency_key: str | None,
    ) -> KeeperHubTransportResponse:
        self.calls += 1
        if self.calls != 1:
            _fail("MULTIPLE_SIMULATION_CALLS_BLOCKED")
        try:
            response = self._delegate.post_transfer(
                body,
                idempotency_key=idempotency_key,
            )
        except KeeperHubHttpTransportError as error:
            self.summary = _sanitize_transport_error(error)
            raise
        self.summary = _sanitize_response(response)
        return response


def _preview(sheet: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "probe": _PROBE_NAME,
        "status": "PREPARED",
        "network_calls": 0,
        "purpose": sheet["purpose"],
        "execution_surface": sheet["execution_surface"],
        "chain_id": sheet["chain_id"],
        "asset": "USDC",
        "token_address": sheet["token_address"],
        "token_decimals": sheet["token_decimals"],
        "amount": sheet["amount_decimal"],
        "recipient_masked": _mask_address(str(sheet["recipient_address"])),
        "mission_key": sheet["mission_key"],
        "effect_id": sheet["effect_id"],
        "attempt_id": sheet["attempt_id"],
        "request_fingerprint": sheet["request_fingerprint"],
        "action_sheet_id": sheet["action_sheet_id"],
        "approval_challenge": sheet["approval_challenge"],
        "maximum_simulation_posts": 1,
        "maximum_broadcast_posts": 0,
        "next_action": "REVIEW_PREVIEW_BEFORE_EXECUTE",
    }


def prepare_action_sheet(recipient: str, state_root: Path) -> dict[str, Any]:
    if not isinstance(recipient, str) or _EVM_ADDRESS.fullmatch(recipient) is None:
        _fail("INVALID_RECIPIENT_ADDRESS")
    paths = _paths(state_root)
    _ensure_private_directory(paths["root"])
    sheet = _action_sheet(recipient.casefold(), _utc_now())
    try:
        _exclusive_json_write(paths["action_sheet"], sheet)
    except FileExistsError:
        _fail("ACTION_SHEET_ALREADY_EXISTS")
    return _preview(sheet)


def execute_action_sheet(
    *,
    api_key: str,
    approval: str,
    state_root: Path,
    http_transport_factory: Callable[[str], Any] = KeeperHubHttpTransport,
    simulation_transport_factory: Callable[
        [Any],
        KeeperHubSimulationOnlyTransport,
    ] = KeeperHubSimulationOnlyTransport,
) -> dict[str, Any]:
    paths = _paths(state_root)
    sheet = _read_action_sheet(paths["action_sheet"])
    selected_intent, plan = _validate_action_sheet(sheet)
    if approval != sheet["approval_challenge"]:
        _fail("SIMULATION_APPROVAL_MISMATCH")
    if (
        not isinstance(api_key, str)
        or not api_key.startswith("kh_")
        or len(api_key) <= 3
        or len(api_key) > 512
        or api_key.strip() != api_key
        or not api_key.isascii()
        or any(
            not (character.isalnum() or character in {"_", "-"})
            for character in api_key
        )
    ):
        _fail("INVALID_LOCAL_API_KEY")

    attempts = SQLiteExecutionAttemptStore(paths["attempts"])
    ledger = SQLiteKeeperHubAuthorizationLedger(paths["authorizations"])
    now = _utc_now()
    authorization = KeeperHubSimulationAuthorization(
        action_sheet_id=sheet["action_sheet_id"],
        approval_reference=sheet["approval_reference"],
        attempt_id=plan.attempt_id,
        request_fingerprint=plan.request_fingerprint,
        authorized_at_utc=now,
        expires_at_utc=now + timedelta(minutes=2),
    )
    http_transport = http_transport_factory(api_key)
    simulation_only = simulation_transport_factory(http_transport)
    capturing = _CapturingSimulationTransport(simulation_only)
    service = KeeperHubControlledSimulationService(
        capturing,
        selected_intent,
        attempts,
        ledger,
    )
    try:
        receipt = service.simulate(plan, authorization, now)
        record = ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.SIMULATION,
            plan.attempt_id,
        )
        return {
            "probe": _PROBE_NAME,
            "status": (
                "PASS"
                if receipt.decision.value == "ELIGIBLE_FOR_BROADCAST_APPROVAL"
                else "STOP"
            ),
            "decision": receipt.decision.value,
            "retry": "FORBIDDEN",
            "simulation_posts": capturing.calls,
            "authorization_state": (
                record.state.value if record is not None else "UNKNOWN"
            ),
            "action_sheet_id": receipt.action_sheet_id,
            "attempt_id": receipt.attempt_id,
            "request_fingerprint": receipt.request_fingerprint,
            "simulation_body_fingerprint": receipt.simulation_body_fingerprint,
            "provider_summary": capturing.summary,
            "broadcast_authorized": False,
            "funds_moved": False,
        }
    except (
        KeeperHubControlledExecutionError,
        KeeperHubHttpTransportError,
        KeeperHubSimulationRuntimeError,
    ) as error:
        try:
            record = ledger.get_for_attempt(
                KeeperHubAuthorizationPhase.SIMULATION,
                plan.attempt_id,
            )
            state = record.state.value if record is not None else "NOT_CLAIMED"
        except Exception:
            state = "UNKNOWN"
        return {
            "probe": _PROBE_NAME,
            "status": "STOP",
            "reason": error.code,
            "retry": "FORBIDDEN",
            "simulation_posts": capturing.calls,
            "authorization_state": state,
            "provider_summary": capturing.summary,
            "broadcast_authorized": False,
            "funds_moved": False,
        }
    finally:
        api_key = ""


def status_action_sheet(state_root: Path) -> dict[str, Any]:
    paths = _paths(state_root)
    sheet = _read_action_sheet(paths["action_sheet"])
    _, plan = _validate_action_sheet(sheet)
    state = "NOT_CLAIMED"
    if paths["authorizations"].exists():
        ledger = SQLiteKeeperHubAuthorizationLedger(paths["authorizations"])
        ledger.initialize()
        record = ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.SIMULATION,
            plan.attempt_id,
        )
        if record is not None:
            state = record.state.value
    return {
        **_preview(sheet),
        "status": "STATUS",
        "authorization_state": state,
        "network_calls": 0,
    }


def _local_failure_result(command: str, code: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "probe": _PROBE_NAME,
        "status": "STOP",
        "reason": code,
        "broadcast_authorized": False,
        "funds_moved": False,
    }
    if command == "prepare" and code in _PREPARE_INPUT_CORRECTABLE:
        result.update(
            {
                "retry": "LOCAL_INPUT_CORRECTION_ALLOWED",
                "network_calls": 0,
                "next_action": "CORRECT_LOCAL_INPUT_AND_RERUN_PREPARE",
            }
        )
    elif command == "execute" and code in _EXECUTE_INPUT_CORRECTABLE:
        result.update(
            {
                "retry": "LOCAL_INPUT_CORRECTION_ALLOWED",
                "network_calls": 0,
                "next_action": "CORRECT_LOCAL_INPUT_AND_RERUN_EXECUTE",
            }
        )
    elif code in _LOCAL_STATE_REVIEW_REQUIRED or (
        command != "prepare" and code == "INVALID_RECIPIENT_ADDRESS"
    ):
        result.update(
            {
                "retry": "MANUAL_LOCAL_RECOVERY_REQUIRED",
                "network_calls": 0,
                "next_action": "PRESERVE_LOCAL_STATE_AND_REVIEW",
            }
        )
    else:
        result["retry"] = "FORBIDDEN"
    return result


def _print(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("prepare", "execute", "status"),
    )
    args = parser.parse_args(argv)
    root = _state_root()
    try:
        if args.command == "prepare":
            recipient = os.environ.pop(_RECIPIENT_ENV, None)
            if recipient is None:
                _fail("LOCAL_RECIPIENT_NOT_SET")
            result = prepare_action_sheet(recipient, root)
            exit_code = 0
        elif args.command == "execute":
            api_key = os.environ.pop(_API_KEY_ENV, None)
            approval = os.environ.pop(_APPROVAL_ENV, None)
            if api_key is None:
                _fail("LOCAL_API_KEY_NOT_SET")
            if approval is None:
                _fail("LOCAL_SIMULATION_APPROVAL_NOT_SET")
            result = execute_action_sheet(
                api_key=api_key,
                approval=approval,
                state_root=root,
            )
            exit_code = 0 if result["status"] == "PASS" else 2
        else:
            result = status_action_sheet(root)
            exit_code = 0
    except OneShotSimulationError as error:
        result = _local_failure_result(args.command, error.code)
        exit_code = 2
    except Exception:
        result = {
            "probe": _PROBE_NAME,
            "status": "STOP",
            "reason": "UNEXPECTED_LOCAL_FAILURE",
            "retry": "FORBIDDEN",
            "broadcast_authorized": False,
            "funds_moved": False,
        }
        exit_code = 2
    finally:
        os.environ.pop(_RECIPIENT_ENV, None)
        os.environ.pop(_APPROVAL_ENV, None)
        os.environ.pop(_API_KEY_ENV, None)
    _print(result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
