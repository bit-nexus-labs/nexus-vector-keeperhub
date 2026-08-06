#!/usr/bin/env python3
"""Prepare and execute one isolated KeeperHub simulation-only canary.

The tool is intentionally incapable of broadcast. It binds the durable canary
Mission prepared by ``runtime_evidence_plan`` to one exact simulation
authorization, claims that authorization before the POST, emits sanitized JSON,
and never retries after a claimed provider call.
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

from nexus_vector.application.runtime_evidence_plan import (  # noqa: E402
    RuntimeEvidencePlanError,
    admit_and_prepare_mission,
    build_simulation_canary_request,
    select_simulation_canary,
)
from nexus_vector.integrations.keeperhub_controlled_execution import (  # noqa: E402
    KeeperHubControlledExecutionError,
    KeeperHubControlledSimulationService,
    KeeperHubSimulationAuthorization,
)
from nexus_vector.integrations.keeperhub_direct_execution import (  # noqa: E402
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
from nexus_vector.persistence.sqlite_mission_store import (  # noqa: E402
    SQLiteMissionStore,
)

_SCHEMA = "nexus-vector.runtime-evidence-canary.v1"
_PROBE = "KEEPERHUB_RUNTIME_EVIDENCE_CANARY_V1"
_PURPOSE = "POST_FIX_PROVIDER_REGRESSION_VALIDATION_V1"
_API_KEY_ENV = "KEEPERHUB_API_KEY"
_APPROVAL_ENV = "NEXUS_VECTOR_CANARY_APPROVAL"
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_SAFE_DIGITS = re.compile(r"[0-9]{1,32}")
_SAFE_PROVIDER_CODES = frozenset(
    {
        "unauthorized",
        "insufficient_scope",
        "not_found",
        "invalid_input",
        "conflict",
        "rate_limited",
        "internal_error",
    }
)
_MAX_ACTION_SHEET_BYTES = 32_768


class RuntimeEvidenceCanaryError(RuntimeError):
    """Machine-classifiable local failure without private-value echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RuntimeEvidenceCanaryError(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _hash(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(_canonical_json(value)).hexdigest()


def _default_local_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        _fail("LOCALAPPDATA_NOT_AVAILABLE")
    return Path(local_app_data) / "NexusVector"


def _default_wallet_registry() -> Path:
    return _default_local_root() / "Config" / "wallets.private-local.json"


def _default_runtime_root() -> Path:
    return _default_local_root() / "RuntimeEvidence"


def _paths(runtime_root: Path) -> dict[str, Path]:
    return {
        "root": runtime_root,
        "action_sheet": runtime_root / "canary.private-action-sheet.json",
        "missions": runtime_root / "missions.sqlite3",
        "attempts": runtime_root / "execution-attempts.sqlite3",
        "authorizations": runtime_root / "canary-authorizations.sqlite3",
    }


def _load_recipient(registry_path: Path) -> str:
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        _fail("LOCAL_WALLET_REGISTRY_NOT_FOUND")
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("LOCAL_WALLET_REGISTRY_INVALID")

    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "updated_at_utc",
        "network",
        "wallets",
        "tokens",
        "safety",
    }:
        _fail("LOCAL_WALLET_REGISTRY_INVALID")
    network = payload.get("network")
    wallets = payload.get("wallets")
    tokens = payload.get("tokens")
    safety = payload.get("safety")
    if (
        not isinstance(network, dict)
        or set(network) != {"name", "chain_id", "environment"}
        or network.get("name") != "Base Sepolia"
        or network.get("chain_id") != 84532
        or network.get("environment") != "testnet"
        or not isinstance(wallets, dict)
        or set(wallets)
        != {"keeperhub_organization_wallet", "personal_recipient_wallet"}
        or not isinstance(tokens, dict)
        or set(tokens) != {"base_sepolia_usdc"}
        or not isinstance(safety, dict)
        or safety.get("mainnet_blocked") is not True
        or safety.get("contains_seed_phrase") is not False
        or safety.get("contains_wallet_private_key") is not False
        or safety.get("contains_turnkey_signing_key") is not False
        or safety.get("api_key_storage") != "WINDOWS_DPAPI_CLIXML"
    ):
        _fail("LOCAL_WALLET_REGISTRY_BINDING_INVALID")

    recipient = wallets.get("personal_recipient_wallet")
    sender = wallets.get("keeperhub_organization_wallet")
    token = tokens.get("base_sepolia_usdc")
    if (
        not isinstance(recipient, str)
        or _EVM_ADDRESS.fullmatch(recipient) is None
        or not isinstance(sender, str)
        or _EVM_ADDRESS.fullmatch(sender) is None
        or not isinstance(token, dict)
        or set(token) != {"role", "symbol", "decimals", "contract_address"}
        or token.get("role") != "TOKEN_CONTRACT_NOT_WALLET"
        or token.get("symbol") != "USDC"
        or token.get("decimals") != 6
        or not isinstance(token.get("contract_address"), str)
        or _EVM_ADDRESS.fullmatch(token["contract_address"]) is None
        or token["contract_address"].casefold()
        != "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
        or recipient.casefold()
        in {sender.casefold(), token["contract_address"].casefold()}
    ):
        _fail("LOCAL_WALLET_REGISTRY_BINDING_INVALID")
    return recipient.casefold()


def _exclusive_json_write(path: Path, value: Mapping[str, Any]) -> None:
    payload = _canonical_json(dict(value)) + b"\n"
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
    if len(raw) > _MAX_ACTION_SHEET_BYTES:
        _fail("ACTION_SHEET_TOO_LARGE")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ACTION_SHEET_CORRUPT")
    if not isinstance(value, dict):
        _fail("ACTION_SHEET_CORRUPT")
    return value


def _selection(
    recipient: str,
    paths: Mapping[str, Path],
    prepared_at: datetime,
):
    mission_store = SQLiteMissionStore(paths["missions"])
    attempt_store = SQLiteExecutionAttemptStore(paths["attempts"])
    attempt_store.initialize()
    request = build_simulation_canary_request(recipient)
    mission = admit_and_prepare_mission(request, mission_store, prepared_at)
    selection = select_simulation_canary(mission, attempt_store)
    return mission, selection


def _action_sheet(
    recipient: str,
    paths: Mapping[str, Path],
    prepared_at: datetime,
) -> dict[str, Any]:
    mission, selection = _selection(recipient, paths, prepared_at)
    immutable = {
        "schema": _SCHEMA,
        "purpose": _PURPOSE,
        "mission_ref": mission.record.request.mission_ref,
        "effect_ref": selection.effect_ref,
        "chain_id": selection.intent.chain_id,
        "token_address": selection.intent.token_address,
        "token_decimals": selection.intent.token_decimals,
        "recipient_address": selection.intent.recipient_address,
        "amount_base_units": selection.amount_base_units,
        "amount_decimal": selection.amount_decimal_string,
        "mission_key": selection.mission_key,
        "effect_id": selection.effect_id,
        "attempt_id": selection.attempt_plan.attempt_id,
        "request_key": selection.attempt_plan.request_key,
        "request_fingerprint": selection.attempt_plan.request_fingerprint,
        "maximum_simulation_posts": selection.maximum_simulation_posts,
        "maximum_broadcast_posts": selection.maximum_broadcast_posts,
    }
    action_sheet_id = _hash("sheet_", immutable)
    approval_reference = _hash(
        "simappr_",
        {
            "action_sheet_id": action_sheet_id,
            "attempt_id": selection.attempt_plan.attempt_id,
            "request_fingerprint": selection.attempt_plan.request_fingerprint,
        },
    )
    approval_challenge = (
        "SIMULATE-CANARY-"
        + hashlib.sha256(
            (action_sheet_id + selection.attempt_plan.request_fingerprint).encode(
                "ascii"
            )
        ).hexdigest()[:20]
    )
    return {
        **immutable,
        "action_sheet_id": action_sheet_id,
        "approval_reference": approval_reference,
        "approval_challenge": approval_challenge,
        "prepared_at_utc": _timestamp(prepared_at),
    }


_EXPECTED_FIELDS = frozenset(
    {
        "schema",
        "purpose",
        "mission_ref",
        "effect_ref",
        "chain_id",
        "token_address",
        "token_decimals",
        "recipient_address",
        "amount_base_units",
        "amount_decimal",
        "mission_key",
        "effect_id",
        "attempt_id",
        "request_key",
        "request_fingerprint",
        "maximum_simulation_posts",
        "maximum_broadcast_posts",
        "action_sheet_id",
        "approval_reference",
        "approval_challenge",
        "prepared_at_utc",
    }
)


def _validate_action_sheet(
    value: Mapping[str, Any],
    paths: Mapping[str, Path],
):
    if frozenset(value) != _EXPECTED_FIELDS:
        _fail("ACTION_SHEET_FIELD_MISMATCH")
    if value.get("schema") != _SCHEMA:
        _fail("ACTION_SHEET_SCHEMA_MISMATCH")
    if value.get("purpose") != _PURPOSE:
        _fail("ACTION_SHEET_PURPOSE_MISMATCH")
    if (
        value.get("maximum_simulation_posts") != 1
        or value.get("maximum_broadcast_posts") != 0
    ):
        _fail("ACTION_SHEET_BUDGET_MISMATCH")
    recipient = value.get("recipient_address")
    if not isinstance(recipient, str) or _EVM_ADDRESS.fullmatch(recipient) is None:
        _fail("INVALID_RECIPIENT_ADDRESS")
    prepared_at = _parse_timestamp(value.get("prepared_at_utc"))
    regenerated = _action_sheet(recipient, paths, prepared_at)
    for field in _EXPECTED_FIELDS - {"prepared_at_utc"}:
        if regenerated[field] != value[field]:
            _fail("ACTION_SHEET_BINDING_MISMATCH")
    _, selection = _selection(recipient, paths, prepared_at)
    return selection


def _safe_provider_code(value: Any) -> str | None:
    if isinstance(value, str) and value in _SAFE_PROVIDER_CODES:
        return value
    return None


def _sanitize_transport_error(
    error: KeeperHubHttpTransportError,
) -> dict[str, Any] | None:
    summary: dict[str, Any] = {}
    if type(error.http_status) is int and 100 <= error.http_status <= 599:
        summary["http_status"] = error.http_status
    provider_code = _safe_provider_code(error.provider_error_code)
    if provider_code is not None:
        summary["provider_error_code"] = provider_code
    return summary or None


def _sanitize_response(response: KeeperHubTransportResponse) -> dict[str, Any]:
    summary: dict[str, Any] = {"http_status": response.status_code}
    body = response.body
    for source, target in (
        ("success", "success"),
        ("status", "provider_status"),
        ("wouldRevert", "would_revert"),
        ("value", "value"),
        ("gasEstimate", "gas_estimate"),
    ):
        value = body.get(source)
        if type(value) in {bool, int}:
            summary[target] = value
        elif (
            isinstance(value, str)
            and len(value) <= 128
            and all(31 < ord(character) < 127 for character in value)
        ):
            if target == "gas_estimate" and _SAFE_DIGITS.fullmatch(value) is None:
                continue
            summary[target] = value
    provider_code = _safe_provider_code(body.get("error"))
    if provider_code is not None:
        summary["provider_error_code"] = provider_code
    summary["simulated_return_present"] = "simulatedReturnValue" in body
    return summary


class _CapturingSimulationTransport:
    def __init__(self, delegate: Any) -> None:
        if not callable(getattr(delegate, "post_transfer", None)):
            _fail("INVALID_SIMULATION_TRANSPORT")
        self._delegate = delegate
        self.calls = 0
        self.summary: dict[str, Any] | None = None

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


def _default_simulation_transport(api_key: str):
    return KeeperHubSimulationOnlyTransport(KeeperHubHttpTransport(api_key))


def _preview(sheet: Mapping[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "probe": _PROBE,
        "status": status,
        "purpose": sheet["purpose"],
        "mission_ref": sheet["mission_ref"],
        "effect_ref": sheet["effect_ref"],
        "chain": "Base Sepolia",
        "chain_id": sheet["chain_id"],
        "asset": "USDC",
        "amount": sheet["amount_decimal"],
        "maximum_simulation_posts": 1,
        "maximum_broadcast_posts": 0,
        "broadcast_authorized": False,
        "funds_moved": False,
        "private_values": "REDACTED_BY_CONSTRUCTION",
    }


def prepare(
    *,
    registry_path: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    os.environ.pop(_API_KEY_ENV, None)
    os.environ.pop(_APPROVAL_ENV, None)
    paths = _paths(runtime_root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    recipient = _load_recipient(registry_path)
    sheet = _action_sheet(recipient, paths, _utc_now())
    try:
        _exclusive_json_write(paths["action_sheet"], sheet)
    except FileExistsError:
        _fail("ACTION_SHEET_ALREADY_EXISTS")
    return {
        **_preview(sheet, status="PREPARED"),
        "network_calls_performed": 0,
        "approval_challenge": sheet["approval_challenge"],
        "next_action": "REVIEW_AND_AUTHORIZE_EXACT_CANARY_SIMULATION",
    }


def execute(
    *,
    api_key: str,
    approval: str,
    runtime_root: Path,
    simulation_transport_factory: Callable[[str], Any] = _default_simulation_transport,
) -> dict[str, Any]:
    paths = _paths(runtime_root)
    sheet = _read_action_sheet(paths["action_sheet"])
    selection = _validate_action_sheet(sheet, paths)
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

    ledger = SQLiteKeeperHubAuthorizationLedger(paths["authorizations"])
    attempts = SQLiteExecutionAttemptStore(paths["attempts"])
    now = _utc_now()
    authorization = KeeperHubSimulationAuthorization(
        action_sheet_id=sheet["action_sheet_id"],
        approval_reference=sheet["approval_reference"],
        attempt_id=selection.attempt_plan.attempt_id,
        request_fingerprint=selection.attempt_plan.request_fingerprint,
        authorized_at_utc=now,
        expires_at_utc=now + timedelta(minutes=2),
    )
    capturing = _CapturingSimulationTransport(
        simulation_transport_factory(api_key)
    )
    service = KeeperHubControlledSimulationService(
        capturing,
        selection.intent,
        attempts,
        ledger,
    )
    try:
        receipt = service.simulate(
            selection.attempt_plan,
            authorization,
            now,
        )
        record = ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.SIMULATION,
            selection.attempt_plan.attempt_id,
        )
        passed = (
            receipt.decision.value
            == "ELIGIBLE_FOR_BROADCAST_APPROVAL"
        )
        return {
            **_preview(sheet, status="PASS" if passed else "STOP"),
            "decision": receipt.decision.value,
            "retry": "NOT_REQUIRED" if passed else "FORBIDDEN",
            "simulation_posts": capturing.calls,
            "broadcast_posts": 0,
            "authorization_state": (
                record.state.value if record is not None else "UNKNOWN"
            ),
            "provider_summary": capturing.summary,
            "action_sheet_binding": "MATCH",
            "request_fingerprint_binding": "MATCH",
        }
    except (
        KeeperHubControlledExecutionError,
        KeeperHubHttpTransportError,
        KeeperHubSimulationRuntimeError,
        RuntimeEvidenceCanaryError,
    ) as error:
        try:
            record = ledger.get_for_attempt(
                KeeperHubAuthorizationPhase.SIMULATION,
                selection.attempt_plan.attempt_id,
            )
            state = (
                record.state.value if record is not None else "NOT_CLAIMED"
            )
        except Exception:
            state = "UNKNOWN"
        return {
            **_preview(sheet, status="STOP"),
            "reason": error.code,
            "retry": "FORBIDDEN",
            "simulation_posts": capturing.calls,
            "broadcast_posts": 0,
            "authorization_state": state,
            "provider_summary": capturing.summary,
        }
    finally:
        api_key = ""


def status(*, runtime_root: Path) -> dict[str, Any]:
    paths = _paths(runtime_root)
    sheet = _read_action_sheet(paths["action_sheet"])
    selection = _validate_action_sheet(sheet, paths)
    authorization_state = "NOT_CLAIMED"
    if paths["authorizations"].exists():
        ledger = SQLiteKeeperHubAuthorizationLedger(paths["authorizations"])
        ledger.initialize()
        record = ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.SIMULATION,
            selection.attempt_plan.attempt_id,
        )
        if record is not None:
            authorization_state = record.state.value
    return {
        **_preview(sheet, status="STATUS"),
        "network_calls_performed": 0,
        "authorization_state": authorization_state,
    }


_LOCAL_CORRECTABLE = frozenset(
    {
        "LOCALAPPDATA_NOT_AVAILABLE",
        "LOCAL_WALLET_REGISTRY_NOT_FOUND",
        "LOCAL_WALLET_REGISTRY_INVALID",
        "LOCAL_WALLET_REGISTRY_BINDING_INVALID",
        "LOCAL_API_KEY_NOT_SET",
        "LOCAL_SIMULATION_APPROVAL_NOT_SET",
        "INVALID_LOCAL_API_KEY",
        "SIMULATION_APPROVAL_MISMATCH",
    }
)
_LOCAL_REVIEW_REQUIRED = frozenset(
    {
        "ACTION_SHEET_ALREADY_EXISTS",
        "ACTION_SHEET_NOT_FOUND",
        "ACTION_SHEET_READ_FAILED",
        "ACTION_SHEET_TOO_LARGE",
        "ACTION_SHEET_CORRUPT",
        "ACTION_SHEET_FIELD_MISMATCH",
        "ACTION_SHEET_SCHEMA_MISMATCH",
        "ACTION_SHEET_PURPOSE_MISMATCH",
        "ACTION_SHEET_BUDGET_MISMATCH",
        "INVALID_ACTION_SHEET_TIMESTAMP",
        "ACTION_SHEET_BINDING_MISMATCH",
    }
)


def _local_failure(command: str, code: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "probe": _PROBE,
        "status": "STOP",
        "reason": code,
        "broadcast_authorized": False,
        "broadcast_posts": 0,
        "funds_moved": False,
    }
    if code in _LOCAL_CORRECTABLE:
        result.update(
            {
                "retry": "LOCAL_INPUT_CORRECTION_ALLOWED",
                "network_calls_performed": 0,
                "next_action": "CORRECT_LOCAL_INPUT",
            }
        )
    elif code in _LOCAL_REVIEW_REQUIRED:
        result.update(
            {
                "retry": "MANUAL_LOCAL_RECOVERY_REQUIRED",
                "network_calls_performed": 0,
                "next_action": "PRESERVE_STATE_AND_REVIEW",
            }
        )
    else:
        result["retry"] = "FORBIDDEN"
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare, execute, or inspect one simulation-only KeeperHub "
            "runtime-evidence canary. No broadcast command exists."
        )
    )
    parser.add_argument("command", choices=("prepare", "execute", "status"))
    parser.add_argument("--wallet-registry", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        runtime_root = arguments.runtime_root or _default_runtime_root()
        registry = arguments.wallet_registry or _default_wallet_registry()
        if arguments.command == "prepare":
            result = prepare(
                registry_path=registry,
                runtime_root=runtime_root,
            )
            exit_code = 0
        elif arguments.command == "execute":
            api_key = os.environ.pop(_API_KEY_ENV, None)
            approval = os.environ.pop(_APPROVAL_ENV, None)
            if api_key is None:
                _fail("LOCAL_API_KEY_NOT_SET")
            if approval is None:
                _fail("LOCAL_SIMULATION_APPROVAL_NOT_SET")
            result = execute(
                api_key=api_key,
                approval=approval,
                runtime_root=runtime_root,
            )
            exit_code = 0 if result["status"] == "PASS" else 2
        else:
            os.environ.pop(_API_KEY_ENV, None)
            os.environ.pop(_APPROVAL_ENV, None)
            result = status(runtime_root=runtime_root)
            exit_code = 0
    except (
        RuntimeEvidenceCanaryError,
        RuntimeEvidencePlanError,
    ) as error:
        result = _local_failure(arguments.command, error.code)
        exit_code = 2
    except Exception:
        result = {
            "probe": _PROBE,
            "status": "STOP",
            "reason": "UNEXPECTED_LOCAL_FAILURE",
            "retry": "FORBIDDEN",
            "broadcast_authorized": False,
            "broadcast_posts": 0,
            "funds_moved": False,
        }
        exit_code = 2
    finally:
        os.environ.pop(_API_KEY_ENV, None)
        os.environ.pop(_APPROVAL_ENV, None)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
