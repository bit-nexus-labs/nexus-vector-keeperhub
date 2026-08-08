#!/usr/bin/env python3
"""Prepare and execute one isolated simulation for a second read-only KeeperHub key.

This diagnostic is intentionally simulation-only. It consumes a fresh Mission/effect
namespace, binds itself to a previously captured successful GET-only key preflight,
claims simulation authorization durably before transport, never retries a claimed
provider call, and exposes no broadcast command or broadcast-capable port.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexus_vector.application.continuation_planner import (  # noqa: E402
    ContinuationAction,
    ContinuationPlanner,
)
from nexus_vector.application.mission_admission import MissionAdmissionService  # noqa: E402
from nexus_vector.domain.execution_attempts import (  # noqa: E402
    ExecutionAttemptPlan,
    build_execution_attempt_plan,
)
from nexus_vector.domain.mission_identity import SCHEMA_VERSION  # noqa: E402
from nexus_vector.domain.mission_models import (  # noqa: E402
    AssetSpec,
    EffectRequest,
    MissionRequest,
    MissionState,
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
    amount_base_units_to_decimal_string,
)
from nexus_vector.integrations.keeperhub_http_transport import (  # noqa: E402
    KeeperHubHttpTransport,
    KeeperHubHttpTransportError,
)
from nexus_vector.integrations.keeperhub_request_key import (  # noqa: E402
    derive_keeperhub_request_key,
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

_SCHEMA = "nexus-vector.second-readonly-key-validation.v1"
_PROBE = "KEEPERHUB_SECOND_READONLY_SIMULATION_V1"
_PURPOSE = "READ_ONLY_SECOND_KEY_PERMISSION_VALIDATION_V1"
_MISSION_REF = "readonly-key2-validation-20260807-v1"
_EFFECT_REF = "readonly-key2-simulation-v1"
_API_KEY_ENV = "KEEPERHUB_API_KEY"
_APPROVAL_ENV = "NEXUS_VECTOR_SECOND_READONLY_APPROVAL"
_BASE_SEPOLIA_CHAIN_ID = 84532
_USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
_USDC_DECIMALS = 6
_AMOUNT_BASE_UNITS = 1
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
_MAX_PREFLIGHT_BYTES = 64_000


class SecondReadOnlyValidationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise SecondReadOnlyValidationError(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("INVALID_ACTION_SHEET_TIMESTAMP")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
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
    return _default_local_root() / "SecondReadOnlyValidation" / "RuntimeV1"


def _default_preflight_evidence() -> Path:
    root = _default_local_root() / "SecondReadOnlyValidation" / "Evidence"
    candidates = sorted(root.glob("second-readonly-key-preflight-*.sanitized.json"))
    if not candidates:
        _fail("PREFLIGHT_EVIDENCE_NOT_FOUND")
    if len(candidates) != 1:
        _fail("PREFLIGHT_EVIDENCE_AMBIGUOUS")
    return candidates[0]


def _paths(runtime_root: Path) -> dict[str, Path]:
    return {
        "root": runtime_root,
        "action_sheet": runtime_root / "key2.private-action-sheet.json",
        "missions": runtime_root / "key2-missions.sqlite3",
        "attempts": runtime_root / "key2-execution-attempts.sqlite3",
        "authorizations": runtime_root / "key2-simulation-authorizations.sqlite3",
    }


def _load_json_file(path: Path, max_bytes: int, missing_code: str, invalid_code: str) -> Any:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        _fail(missing_code)
    except OSError:
        _fail(invalid_code)
    if len(raw) > max_bytes:
        _fail(invalid_code)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(invalid_code)


def _validated_preflight(path: Path) -> tuple[dict[str, Any], str]:
    payload = _load_json_file(
        path,
        _MAX_PREFLIGHT_BYTES,
        "PREFLIGHT_EVIDENCE_NOT_FOUND",
        "PREFLIGHT_EVIDENCE_INVALID",
    )
    if not isinstance(payload, dict):
        _fail("PREFLIGHT_EVIDENCE_INVALID")

    required = {
        "probe": "KEEPERHUB_KEY_IDENTITY_SURFACE_V1",
        "endpoint": "GET /api/keys",
        "status": "PASS",
        "reason": "ORGANIZATION_KEY_VISIBLE_TO_BACKEND",
        "http_status": 200,
        "response_surface": "APPLICATION_JSON",
        "organization_key_match": "MATCH",
        "get_requests": 1,
        "post_requests": 0,
        "simulation_posts": 0,
        "broadcast_posts": 0,
        "funds_moved": False,
        "retry": "NOT_REQUIRED",
    }
    for key, expected in required.items():
        if payload.get(key) != expected:
            _fail("PREFLIGHT_EVIDENCE_NOT_PASS")
    if payload.get("request_id_reflection") not in {"NOT_PRESENT", "MATCH"}:
        _fail("PREFLIGHT_EVIDENCE_REQUEST_ID_INVALID")

    support_request_id = payload.get("support_request_id")
    if (
        not isinstance(support_request_id, str)
        or not support_request_id.startswith("nv-key-surface-")
        or len(support_request_id) > 128
        or any(ord(ch) <= 32 or ord(ch) >= 127 for ch in support_request_id)
    ):
        _fail("PREFLIGHT_EVIDENCE_REQUEST_ID_INVALID")

    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return payload, digest


def _load_recipient(registry_path: Path) -> str:
    payload = _load_json_file(
        registry_path,
        64_000,
        "LOCAL_WALLET_REGISTRY_NOT_FOUND",
        "LOCAL_WALLET_REGISTRY_INVALID",
    )
    if not isinstance(payload, dict):
        _fail("LOCAL_WALLET_REGISTRY_INVALID")
    network = payload.get("network")
    wallets = payload.get("wallets")
    tokens = payload.get("tokens")
    safety = payload.get("safety")
    if (
        not isinstance(network, dict)
        or network.get("name") != "Base Sepolia"
        or network.get("chain_id") != _BASE_SEPOLIA_CHAIN_ID
        or network.get("environment") != "testnet"
        or not isinstance(wallets, dict)
        or not isinstance(tokens, dict)
        or not isinstance(safety, dict)
        or safety.get("mainnet_blocked") is not True
        or safety.get("contains_seed_phrase") is not False
        or safety.get("contains_wallet_private_key") is not False
        or safety.get("contains_turnkey_signing_key") is not False
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
        or token.get("symbol") != "USDC"
        or token.get("decimals") != _USDC_DECIMALS
        or str(token.get("contract_address", "")).casefold() != _USDC_ADDRESS.casefold()
        or recipient.casefold() in {sender.casefold(), _USDC_ADDRESS.casefold()}
    ):
        _fail("LOCAL_WALLET_REGISTRY_BINDING_INVALID")
    return recipient.casefold()


def _mission_request(recipient: str) -> MissionRequest:
    return MissionRequest(
        schema_version=SCHEMA_VERSION,
        mission_namespace="nexus-vector.keeperhub",
        mission_ref=_MISSION_REF,
        mission_type="mission-safe-agent-payment",
        chain_id=_BASE_SEPOLIA_CHAIN_ID,
        asset=AssetSpec(token_address=_USDC_ADDRESS, decimals=_USDC_DECIMALS),
        effects=(
            EffectRequest(
                effect_ref=_EFFECT_REF,
                recipient=recipient,
                amount_base_units=_AMOUNT_BASE_UNITS,
            ),
        ),
    )


def _admit_and_prepare(request: MissionRequest, store: SQLiteMissionStore, now: datetime):
    current = MissionAdmissionService(store).admit(request, now)
    for expected, target in (
        (MissionState.PERSISTED, MissionState.RECONCILING),
        (MissionState.RECONCILING, MissionState.READY_FOR_EXECUTION),
    ):
        if current.record.state is target:
            continue
        if current.record.state is not expected:
            if current.record.state is MissionState.READY_FOR_EXECUTION:
                break
            _fail("MISSION_NOT_PREPARABLE")
        current = store.transition_mission(
            current.record.mission_key,
            current.revision,
            target,
            max(now, current.record.updated_at_utc),
        )
    final = store.get(current.record.mission_key)
    if final is None or final.record.state is not MissionState.READY_FOR_EXECUTION:
        _fail("MISSION_NOT_READY")
    return final


@dataclass(frozen=True)
class _Selection:
    mission_key: str
    effect_id: str
    intent: KeeperHubTransferIntent
    attempt_plan: ExecutionAttemptPlan

    @property
    def amount_decimal(self) -> str:
        return amount_base_units_to_decimal_string(_AMOUNT_BASE_UNITS, _USDC_DECIMALS)


def _select(mission: Any, attempts: SQLiteExecutionAttemptStore) -> _Selection:
    continuation = ContinuationPlanner(attempts).plan(mission)
    decision = next(
        (item for item in continuation.decisions if item.effect_ref == _EFFECT_REF),
        None,
    )
    if decision is None or decision.action is not ContinuationAction.EXECUTE_MISSING:
        _fail("EFFECT_NOT_EXECUTABLE")
    effect = next(
        (item for item in mission.record.effects if item.effect_id == decision.effect_id),
        None,
    )
    if effect is None:
        _fail("EFFECT_NOT_FOUND")
    intent = KeeperHubTransferIntent(
        chain_id=effect.chain_id,
        recipient_address=effect.recipient,
        amount_base_units=effect.amount_base_units,
        token_address=effect.token_address,
        token_decimals=effect.token_decimals,
    )
    request_key = derive_keeperhub_request_key(effect.effect_id)
    attempt_plan = build_execution_attempt_plan(
        mission_key=mission.record.mission_key,
        effect_id=effect.effect_id,
        provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
        request_key=request_key,
        request_material=intent.request_material,
    )
    return _Selection(
        mission_key=mission.record.mission_key,
        effect_id=effect.effect_id,
        intent=intent,
        attempt_plan=attempt_plan,
    )


def _selection(recipient: str, paths: Mapping[str, Path], prepared_at: datetime) -> _Selection:
    mission_store = SQLiteMissionStore(paths["missions"])
    attempts = SQLiteExecutionAttemptStore(paths["attempts"])
    attempts.initialize()
    mission = _admit_and_prepare(_mission_request(recipient), mission_store, prepared_at)
    return _select(mission, attempts)


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


def _action_sheet(
    recipient: str,
    paths: Mapping[str, Path],
    prepared_at: datetime,
    preflight_digest: str,
) -> dict[str, Any]:
    selection = _selection(recipient, paths, prepared_at)
    immutable = {
        "schema": _SCHEMA,
        "purpose": _PURPOSE,
        "mission_ref": _MISSION_REF,
        "effect_ref": _EFFECT_REF,
        "chain_id": _BASE_SEPOLIA_CHAIN_ID,
        "token_address": selection.intent.token_address,
        "token_decimals": _USDC_DECIMALS,
        "recipient_address": selection.intent.recipient_address,
        "amount_base_units": _AMOUNT_BASE_UNITS,
        "amount_decimal": selection.amount_decimal,
        "mission_key": selection.mission_key,
        "effect_id": selection.effect_id,
        "attempt_id": selection.attempt_plan.attempt_id,
        "request_key": selection.attempt_plan.request_key,
        "request_fingerprint": selection.attempt_plan.request_fingerprint,
        "preflight_evidence_sha256": preflight_digest,
        "maximum_simulation_posts": 1,
        "maximum_broadcast_posts": 0,
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
        "SIMULATE-KEY2-"
        + hashlib.sha256(
            (action_sheet_id + selection.attempt_plan.request_fingerprint).encode("ascii")
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
        "preflight_evidence_sha256",
        "maximum_simulation_posts",
        "maximum_broadcast_posts",
        "action_sheet_id",
        "approval_reference",
        "approval_challenge",
        "prepared_at_utc",
    }
)


def _read_action_sheet(path: Path) -> dict[str, Any]:
    value = _load_json_file(
        path,
        _MAX_ACTION_SHEET_BYTES,
        "ACTION_SHEET_NOT_FOUND",
        "ACTION_SHEET_CORRUPT",
    )
    if not isinstance(value, dict):
        _fail("ACTION_SHEET_CORRUPT")
    return value


def _validate_action_sheet(
    value: Mapping[str, Any],
    paths: Mapping[str, Path],
    preflight_digest: str,
) -> _Selection:
    if frozenset(value) != _EXPECTED_FIELDS:
        _fail("ACTION_SHEET_FIELD_MISMATCH")
    if value.get("schema") != _SCHEMA or value.get("purpose") != _PURPOSE:
        _fail("ACTION_SHEET_SCHEMA_MISMATCH")
    if value.get("mission_ref") != _MISSION_REF or value.get("effect_ref") != _EFFECT_REF:
        _fail("ACTION_SHEET_IDENTITY_MISMATCH")
    if value.get("preflight_evidence_sha256") != preflight_digest:
        _fail("PREFLIGHT_EVIDENCE_BINDING_MISMATCH")
    if value.get("maximum_simulation_posts") != 1 or value.get("maximum_broadcast_posts") != 0:
        _fail("ACTION_SHEET_BUDGET_MISMATCH")
    recipient = value.get("recipient_address")
    if not isinstance(recipient, str) or _EVM_ADDRESS.fullmatch(recipient) is None:
        _fail("INVALID_RECIPIENT_ADDRESS")
    prepared_at = _parse_timestamp(value.get("prepared_at_utc"))
    regenerated = _action_sheet(recipient, paths, prepared_at, preflight_digest)
    for field in _EXPECTED_FIELDS - {"prepared_at_utc"}:
        if regenerated[field] != value[field]:
            _fail("ACTION_SHEET_BINDING_MISMATCH")
    return _selection(recipient, paths, prepared_at)


def _safe_provider_code(value: Any) -> str | None:
    return value if isinstance(value, str) and value in _SAFE_PROVIDER_CODES else None


def _sanitize_transport_error(error: KeeperHubHttpTransportError) -> dict[str, Any] | None:
    summary: dict[str, Any] = {}
    if type(error.http_status) is int and 100 <= error.http_status <= 599:
        summary["http_status"] = error.http_status
    code = _safe_provider_code(error.provider_error_code)
    if code is not None:
        summary["provider_error_code"] = code
    return summary or None


def _sanitize_response(response: KeeperHubTransportResponse) -> dict[str, Any]:
    summary: dict[str, Any] = {"http_status": response.status_code}
    body = response.body
    for source, target in (
        ("success", "success"),
        ("status", "provider_status"),
        ("wouldRevert", "would_revert"),
        ("gasEstimate", "gas_estimate"),
    ):
        value = body.get(source)
        if type(value) in {bool, int}:
            summary[target] = value
        elif isinstance(value, str) and len(value) <= 128 and all(31 < ord(ch) < 127 for ch in value):
            if target == "gas_estimate" and _SAFE_DIGITS.fullmatch(value) is None:
                continue
            summary[target] = value
    code = _safe_provider_code(body.get("error"))
    if code is not None:
        summary["provider_error_code"] = code
    summary["simulated_return_present"] = "simulatedReturnValue" in body
    return summary


class _CapturingSimulationTransport:
    def __init__(self, delegate: Any) -> None:
        if not callable(getattr(delegate, "post_transfer", None)):
            _fail("INVALID_SIMULATION_TRANSPORT")
        self._delegate = delegate
        self.calls = 0
        self.summary: dict[str, Any] | None = None

    def post_transfer(self, body: Mapping[str, Any], *, idempotency_key: str | None):
        self.calls += 1
        if self.calls != 1:
            _fail("MULTIPLE_SIMULATION_CALLS_BLOCKED")
        try:
            response = self._delegate.post_transfer(body, idempotency_key=idempotency_key)
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
        "purpose": _PURPOSE,
        "mission_ref": _MISSION_REF,
        "effect_ref": _EFFECT_REF,
        "chain": "Base Sepolia",
        "chain_id": _BASE_SEPOLIA_CHAIN_ID,
        "asset": "USDC",
        "amount": sheet["amount_decimal"],
        "preflight_binding": "MATCH",
        "credential_binding": "OPERATOR_REENTRY_REQUIRED",
        "maximum_simulation_posts": 1,
        "maximum_broadcast_posts": 0,
        "broadcast_authorized": False,
        "funds_moved": False,
        "claim_boundary": "SIMULATION_ONLY_NOT_TRANSACTION_EVIDENCE",
        "private_values": "REDACTED_BY_CONSTRUCTION",
    }


def prepare(*, registry_path: Path, runtime_root: Path, preflight_evidence: Path) -> dict[str, Any]:
    os.environ.pop(_API_KEY_ENV, None)
    os.environ.pop(_APPROVAL_ENV, None)
    _, preflight_digest = _validated_preflight(preflight_evidence)
    paths = _paths(runtime_root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    recipient = _load_recipient(registry_path)
    sheet = _action_sheet(recipient, paths, _utc_now(), preflight_digest)
    try:
        _exclusive_json_write(paths["action_sheet"], sheet)
    except FileExistsError:
        _fail("ACTION_SHEET_ALREADY_EXISTS")
    return {
        **_preview(sheet, status="PREPARED"),
        "network_calls_performed": 0,
        "approval_challenge": sheet["approval_challenge"],
        "next_action": "REVIEW_AND_AUTHORIZE_EXACT_SECOND_READONLY_SIMULATION",
    }


def execute(
    *,
    api_key: str,
    approval: str,
    runtime_root: Path,
    preflight_evidence: Path,
    simulation_transport_factory: Callable[[str], Any] = _default_simulation_transport,
) -> dict[str, Any]:
    _, preflight_digest = _validated_preflight(preflight_evidence)
    paths = _paths(runtime_root)
    sheet = _read_action_sheet(paths["action_sheet"])
    selection = _validate_action_sheet(sheet, paths, preflight_digest)
    if approval != sheet["approval_challenge"]:
        _fail("SIMULATION_APPROVAL_MISMATCH")
    if (
        not isinstance(api_key, str)
        or not api_key.startswith("kh_")
        or len(api_key) <= 3
        or len(api_key) > 512
        or api_key.strip() != api_key
        or not api_key.isascii()
        or any(not (ch.isalnum() or ch in {"_", "-"}) for ch in api_key)
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
    capturing = _CapturingSimulationTransport(simulation_transport_factory(api_key))
    service = KeeperHubControlledSimulationService(
        capturing,
        selection.intent,
        attempts,
        ledger,
    )
    try:
        receipt = service.simulate(selection.attempt_plan, authorization, now)
        record = ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.SIMULATION,
            selection.attempt_plan.attempt_id,
        )
        passed = receipt.decision.value == "ELIGIBLE_FOR_BROADCAST_APPROVAL"
        return {
            **_preview(sheet, status="PASS" if passed else "STOP"),
            "decision": receipt.decision.value,
            "retry": "NOT_REQUIRED" if passed else "FORBIDDEN",
            "simulation_posts": capturing.calls,
            "broadcast_posts": 0,
            "authorization_state": record.state.value if record is not None else "UNKNOWN",
            "provider_summary": capturing.summary,
            "action_sheet_binding": "MATCH",
            "request_fingerprint_binding": "MATCH",
        }
    except (
        KeeperHubControlledExecutionError,
        KeeperHubHttpTransportError,
        KeeperHubSimulationRuntimeError,
        SecondReadOnlyValidationError,
    ) as error:
        try:
            record = ledger.get_for_attempt(
                KeeperHubAuthorizationPhase.SIMULATION,
                selection.attempt_plan.attempt_id,
            )
            state = record.state.value if record is not None else "NOT_CLAIMED"
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


def status(*, runtime_root: Path, preflight_evidence: Path) -> dict[str, Any]:
    _, preflight_digest = _validated_preflight(preflight_evidence)
    paths = _paths(runtime_root)
    sheet = _read_action_sheet(paths["action_sheet"])
    selection = _validate_action_sheet(sheet, paths, preflight_digest)
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


def _resolve_preflight(argument: Path | None) -> Path:
    return argument if argument is not None else _default_preflight_evidence()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare, execute, or inspect one isolated second-read-only-key "
            "simulation. No broadcast command exists."
        )
    )
    parser.add_argument("command", choices=("prepare", "execute", "status"))
    parser.add_argument("--wallet-registry", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--preflight-evidence", type=Path)
    return parser


def _local_failure(code: str) -> dict[str, Any]:
    return {
        "probe": _PROBE,
        "status": "STOP",
        "reason": code,
        "retry": "LOCAL_INPUT_CORRECTION_ALLOWED",
        "network_calls_performed": 0,
        "broadcast_authorized": False,
        "broadcast_posts": 0,
        "funds_moved": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        runtime_root = args.runtime_root or _default_runtime_root()
        registry = args.wallet_registry or _default_wallet_registry()
        preflight = _resolve_preflight(args.preflight_evidence)
        if args.command == "prepare":
            result = prepare(
                registry_path=registry,
                runtime_root=runtime_root,
                preflight_evidence=preflight,
            )
            exit_code = 0
        elif args.command == "execute":
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
                preflight_evidence=preflight,
            )
            exit_code = 0 if result["status"] == "PASS" else 2
        else:
            os.environ.pop(_API_KEY_ENV, None)
            os.environ.pop(_APPROVAL_ENV, None)
            result = status(runtime_root=runtime_root, preflight_evidence=preflight)
            exit_code = 0
    except SecondReadOnlyValidationError as error:
        result = _local_failure(error.code)
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
