"""One-shot KeeperHub rehearsal execution runner for Base Sepolia USDC.

The runner separates local preparation, one simulation POST, one separately
approved broadcast POST, and read-only provider-status observation. It reuses
the project's durable Mission, attempt, provider-reference, and authorization
state machines. It never retries a simulation or broadcast after ambiguity.
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

from nexus_vector.application.execution_dispatch import (  # noqa: E402
    ExecutionDispatchError,
    ExecutionDispatchService,
)
from nexus_vector.application.mission_admission import (  # noqa: E402
    MissionAdmissionService,
)
from nexus_vector.application.provider_reference_port import (  # noqa: E402
    ProviderReferencePersistingPort,
)
from nexus_vector.domain.execution_attempts import (  # noqa: E402
    ExecutionAttemptPlan,
    ExecutionAttemptState,
    build_execution_attempt_plan,
)
from nexus_vector.domain.mission_identity import SCHEMA_VERSION  # noqa: E402
from nexus_vector.domain.mission_models import (  # noqa: E402
    EffectState,
    MissionRequest,
    MissionState,
)
from nexus_vector.integrations.keeperhub_controlled_execution import (  # noqa: E402
    KeeperHubApprovedBroadcastPort,
    KeeperHubBroadcastAuthorization,
    KeeperHubControlledExecutionError,
    KeeperHubControlledSimulationService,
    KeeperHubSimulationAuthorization,
    KeeperHubSimulationDecision,
    KeeperHubSimulationReceipt,
    load_keeperhub_simulation_receipt,
)
from nexus_vector.integrations.keeperhub_direct_execution import (  # noqa: E402
    KEEPERHUB_PROVIDER_NAMESPACE,
    KeeperHubTransferIntent,
    KeeperHubTransportResponse,
)
from nexus_vector.integrations.keeperhub_execution_status import (  # noqa: E402
    KeeperHubExecutionStatusObserver,
)
from nexus_vector.integrations.keeperhub_http_transport import (  # noqa: E402
    KeeperHubHttpTransport,
    KeeperHubHttpTransportError,
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
    SQLiteMissionStoreError,
)
from nexus_vector.persistence.sqlite_provider_execution_reference_store import (  # noqa: E402
    SQLiteProviderExecutionReferenceStore,
    SQLiteProviderExecutionReferenceStoreError,
)

_TOOL_SCHEMA = "nexus-vector.keeperhub-rehearsal-execution.v1"
_PURPOSE = "KEEPERHUB_REHEARSAL_TESTNET_EXECUTION"
_EXECUTION_SURFACE = "DIRECT_EXECUTION"
_MISSION_NAMESPACE = "nexus-vector.keeperhub"
_MISSION_TYPE = "KEEPERHUB_REHEARSAL_TESTNET"
_EFFECT_REF = "rehearsal-transfer-01"

_BASE_SEPOLIA_CHAIN_ID = 84532
_BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
_TOKEN_DECIMALS = 6
_AMOUNT_BASE_UNITS = 1
_AMOUNT_DECIMAL = "0.000001"

_API_KEY_ENV = "KEEPERHUB_API_KEY"
_RECIPIENT_ENV = "NEXUS_VECTOR_REHEARSAL_RECIPIENT"
_SIM_APPROVAL_ENV = "NEXUS_VECTOR_REHEARSAL_SIM_APPROVAL"
_BROADCAST_APPROVAL_ENV = "NEXUS_VECTOR_REHEARSAL_BROADCAST_APPROVAL"
_REQUIRED_BROADCAST_FLAG = "--approve-testnet-write"

_RUN_REF_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_SAFE_DIGITS = re.compile(r"[0-9]{1,32}")


class RehearsalExecutionError(RuntimeError):
    """Machine-classifiable operator failure without raw-value echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RehearsalExecutionError(code)


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


def _short(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) <= 18:
        return value
    return f"{value[:10]}…{value[-6:]}"


def _validate_run_ref(value: Any) -> str:
    if not isinstance(value, str) or _RUN_REF_PATTERN.fullmatch(value) is None:
        _fail("INVALID_RUN_REF")
    return value


def _validate_recipient(value: Any) -> str:
    if not isinstance(value, str) or _EVM_ADDRESS.fullmatch(value) is None:
        _fail("INVALID_RECIPIENT_ADDRESS")
    return value.casefold()


def _base_root() -> Path:
    return Path.home() / ".nexus-vector" / "keeperhub-rehearsal-execution-v1"


def _run_root(run_ref: str, base_root: Path | None = None) -> Path:
    return (base_root or _base_root()) / _validate_run_ref(run_ref)


def _paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "action_sheet": root / "private_action_sheet.json",
        "missions": root / "missions.sqlite3",
        "attempts": root / "execution_attempts.sqlite3",
        "authorizations": root / "keeperhub_authorizations.sqlite3",
        "provider_references": root / "provider_references.sqlite3",
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
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _fail("ACTION_SHEET_ALREADY_EXISTS")
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


def _mission_mapping(run_ref: str, recipient: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mission_namespace": _MISSION_NAMESPACE,
        "mission_ref": f"keeperhub-rehearsal-{run_ref}",
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


def _request_key(run_ref: str, mission_key: str, effect_id: str) -> str:
    return _sha256(
        "khreq_",
        {
            "schema": _TOOL_SCHEMA,
            "run_ref": run_ref,
            "mission_key": mission_key,
            "effect_id": effect_id,
        },
    )


def _build_plan(
    run_ref: str,
    recipient: str,
) -> tuple[MissionRequest, KeeperHubTransferIntent, ExecutionAttemptPlan]:
    request = MissionRequest.from_mapping(_mission_mapping(run_ref, recipient))
    identity = request.build_identity()
    if len(identity.effect_ids) != 1:
        _fail("INVALID_EFFECT_COUNT")
    selected_intent = _intent(recipient)
    plan = build_execution_attempt_plan(
        mission_key=identity.mission_key,
        effect_id=identity.effect_ids[0],
        provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
        request_key=_request_key(run_ref, identity.mission_key, identity.effect_ids[0]),
        request_material=selected_intent.request_material,
    )
    return request, selected_intent, plan


def _simulation_approval_reference(
    action_sheet_id: str,
    attempt_id: str,
    request_fingerprint: str,
) -> str:
    return _sha256(
        "simappr_",
        {
            "action_sheet_id": action_sheet_id,
            "attempt_id": attempt_id,
            "request_fingerprint": request_fingerprint,
        },
    )


def _simulation_challenge(
    action_sheet_id: str,
    request_fingerprint: str,
) -> str:
    digest = hashlib.sha256(
        (action_sheet_id + request_fingerprint).encode("ascii")
    ).hexdigest()[:20]
    return f"SIMULATE-{digest}"


def _broadcast_approval_reference(
    action_sheet_id: str,
    receipt: KeeperHubSimulationReceipt,
) -> str:
    return _sha256(
        "bcastappr_",
        {
            "action_sheet_id": action_sheet_id,
            "attempt_id": receipt.attempt_id,
            "request_fingerprint": receipt.request_fingerprint,
            "simulation_body_fingerprint": receipt.simulation_body_fingerprint,
        },
    )


def _broadcast_challenge(
    action_sheet_id: str,
    receipt: KeeperHubSimulationReceipt,
) -> str:
    digest = hashlib.sha256(
        (
            action_sheet_id
            + receipt.request_fingerprint
            + receipt.simulation_body_fingerprint
        ).encode("ascii")
    ).hexdigest()[:20]
    return f"BROADCAST-{digest}"


def _action_sheet(run_ref: str, recipient: str, prepared_at: datetime) -> dict[str, Any]:
    request, selected_intent, plan = _build_plan(run_ref, recipient)
    identity = request.build_identity()
    immutable = {
        "schema": _TOOL_SCHEMA,
        "purpose": _PURPOSE,
        "run_ref": run_ref,
        "execution_surface": _EXECUTION_SURFACE,
        "chain_id": selected_intent.chain_id,
        "token_address": selected_intent.token_address,
        "token_decimals": selected_intent.token_decimals,
        "amount_base_units": selected_intent.amount_base_units,
        "amount_decimal": selected_intent.amount_decimal_string,
        "recipient_address": selected_intent.recipient_address,
        "mission_key": plan.mission_key,
        "mission_content_fingerprint": identity.content_fingerprint,
        "effect_ref": _EFFECT_REF,
        "effect_id": plan.effect_id,
        "attempt_id": plan.attempt_id,
        "request_key": plan.request_key,
        "request_fingerprint": plan.request_fingerprint,
    }
    action_sheet_id = _sha256("sheet_", immutable)
    sim_reference = _simulation_approval_reference(
        action_sheet_id,
        plan.attempt_id,
        plan.request_fingerprint,
    )
    return {
        **immutable,
        "action_sheet_id": action_sheet_id,
        "simulation_approval_reference": sim_reference,
        "simulation_approval_challenge": _simulation_challenge(
            action_sheet_id,
            plan.request_fingerprint,
        ),
        "prepared_at_utc": _timestamp(prepared_at),
        "maximum_simulation_posts": 1,
        "maximum_broadcast_posts": 1,
        "maximum_mutating_calls": 1,
        "same_key_recovery_posts_after_ambiguity": 0,
        "new_request_keys_after_ambiguity": 0,
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
) -> tuple[MissionRequest, KeeperHubTransferIntent, ExecutionAttemptPlan]:
    expected_fields = {
        "schema",
        "purpose",
        "run_ref",
        "execution_surface",
        "chain_id",
        "token_address",
        "token_decimals",
        "amount_base_units",
        "amount_decimal",
        "recipient_address",
        "mission_key",
        "mission_content_fingerprint",
        "effect_ref",
        "effect_id",
        "attempt_id",
        "request_key",
        "request_fingerprint",
        "action_sheet_id",
        "simulation_approval_reference",
        "simulation_approval_challenge",
        "prepared_at_utc",
        "maximum_simulation_posts",
        "maximum_broadcast_posts",
        "maximum_mutating_calls",
        "same_key_recovery_posts_after_ambiguity",
        "new_request_keys_after_ambiguity",
    }
    if set(value.keys()) != expected_fields:
        _fail("ACTION_SHEET_FIELD_MISMATCH")
    if value.get("schema") != _TOOL_SCHEMA:
        _fail("ACTION_SHEET_SCHEMA_MISMATCH")
    if value.get("purpose") != _PURPOSE:
        _fail("ACTION_SHEET_PURPOSE_MISMATCH")
    if value.get("execution_surface") != _EXECUTION_SURFACE:
        _fail("ACTION_SHEET_SURFACE_MISMATCH")
    if (
        value.get("maximum_simulation_posts") != 1
        or value.get("maximum_broadcast_posts") != 1
        or value.get("maximum_mutating_calls") != 1
        or value.get("same_key_recovery_posts_after_ambiguity") != 0
        or value.get("new_request_keys_after_ambiguity") != 0
    ):
        _fail("ACTION_SHEET_BUDGET_MISMATCH")
    run_ref = _validate_run_ref(value.get("run_ref"))
    recipient = _validate_recipient(value.get("recipient_address"))
    request, selected_intent, plan = _build_plan(run_ref, recipient)
    regenerated = _action_sheet(
        run_ref,
        recipient,
        _parse_timestamp(value.get("prepared_at_utc")),
    )
    for field in expected_fields - {"prepared_at_utc"}:
        if regenerated[field] != value[field]:
            _fail("ACTION_SHEET_FINGERPRINT_MISMATCH")
    return request, selected_intent, plan


def _admit_ready_mission(
    request: MissionRequest,
    paths: Mapping[str, Path],
    observed_at: datetime,
) -> None:
    store = SQLiteMissionStore(paths["missions"])
    current = MissionAdmissionService(store).admit(request, observed_at)
    if current.record.state is MissionState.PERSISTED:
        current = store.transition_mission(
            current.record.mission_key,
            current.revision,
            MissionState.RECONCILING,
            observed_at,
        )
    if current.record.state is MissionState.RECONCILING:
        current = store.transition_mission(
            current.record.mission_key,
            current.revision,
            MissionState.READY_FOR_EXECUTION,
            observed_at,
        )
    if current.record.state is not MissionState.READY_FOR_EXECUTION:
        _fail("MISSION_NOT_READY_FOR_REHEARSAL")
    if len(current.record.effects) != 1:
        _fail("INVALID_EFFECT_COUNT")
    effect = current.record.effects[0]
    if effect.effect_ref != _EFFECT_REF or effect.state is not EffectState.PLANNED:
        _fail("EFFECT_NOT_READY_FOR_REHEARSAL")


def _preview(sheet: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": sheet["schema"],
        "status": "PREPARED",
        "run_ref": sheet["run_ref"],
        "network_calls": 0,
        "chain_id": sheet["chain_id"],
        "token_symbol": "USDC",
        "amount": sheet["amount_decimal"],
        "recipient_masked": _mask_address(sheet["recipient_address"]),
        "mission_key": _short(sheet["mission_key"]),
        "effect_id": _short(sheet["effect_id"]),
        "attempt_id": _short(sheet["attempt_id"]),
        "request_fingerprint": _short(sheet["request_fingerprint"]),
        "simulation_approval_challenge": sheet["simulation_approval_challenge"],
        "maximum_simulation_posts": 1,
        "maximum_broadcast_posts": 1,
        "maximum_mutating_calls": 1,
        "broadcast_authorized": False,
        "mainnet_allowed": False,
    }


def prepare_action_sheet(
    recipient: str,
    run_ref: str,
    *,
    base_root: Path | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    checked_ref = _validate_run_ref(run_ref)
    checked_recipient = _validate_recipient(recipient)
    now = observed_at or _utc_now()
    root = _run_root(checked_ref, base_root)
    paths = _paths(root)
    _ensure_private_directory(root)
    if paths["action_sheet"].exists():
        _fail("ACTION_SHEET_ALREADY_EXISTS")
    sheet = _action_sheet(checked_ref, checked_recipient, now)
    request, _, _ = _validate_action_sheet(sheet)
    _exclusive_json_write(paths["action_sheet"], sheet)
    try:
        _admit_ready_mission(request, paths, now)
    except Exception:
        _fail("MISSION_PREPARATION_FAILED")
    return _preview(sheet)


class _CapturingTransferTransport:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0
        self.last_response: KeeperHubTransportResponse | None = None

    def post_transfer(
        self,
        body: Mapping[str, Any],
        *,
        idempotency_key: str | None,
    ) -> KeeperHubTransportResponse:
        self.calls += 1
        response = self._inner.post_transfer(
            body,
            idempotency_key=idempotency_key,
        )
        self.last_response = response
        return response


def _safe_provider_summary(
    response: KeeperHubTransportResponse | None,
) -> dict[str, Any] | None:
    if response is None:
        return None
    result: dict[str, Any] = {"http_status": response.status_code}
    for source, target in (
        ("success", "success"),
        ("status", "provider_status"),
        ("wouldRevert", "would_revert"),
        ("gasEstimate", "gas_estimate"),
    ):
        value = response.body.get(source)
        if type(value) in {bool, int}:
            result[target] = value
        elif isinstance(value, str) and len(value) <= 128:
            if target == "gas_estimate" and _SAFE_DIGITS.fullmatch(value) is None:
                continue
            if all(31 < ord(character) < 127 for character in value):
                result[target] = value
    return result


def _transport_error_summary(
    error: KeeperHubHttpTransportError,
) -> dict[str, Any] | None:
    result: dict[str, Any] = {}
    if type(error.http_status) is int and 100 <= error.http_status <= 599:
        result["http_status"] = error.http_status
    if isinstance(error.provider_error_code, str):
        result["provider_error_code"] = error.provider_error_code
    return result or None


def _load_context(
    run_ref: str,
    *,
    base_root: Path | None = None,
) -> tuple[
    dict[str, Any],
    MissionRequest,
    KeeperHubTransferIntent,
    ExecutionAttemptPlan,
    dict[str, Path],
]:
    root = _run_root(run_ref, base_root)
    paths = _paths(root)
    sheet = _read_action_sheet(paths["action_sheet"])
    request, selected_intent, plan = _validate_action_sheet(sheet)
    return sheet, request, selected_intent, plan, paths


def execute_simulation(
    *,
    api_key: str,
    approval: str,
    run_ref: str,
    base_root: Path | None = None,
    observed_at: datetime | None = None,
    http_transport_factory: Callable[[str], Any] = KeeperHubHttpTransport,
) -> dict[str, Any]:
    sheet, _, selected_intent, plan, paths = _load_context(
        run_ref,
        base_root=base_root,
    )
    if approval != sheet["simulation_approval_challenge"]:
        _fail("SIMULATION_APPROVAL_MISMATCH")
    now = observed_at or _utc_now()
    transport = _CapturingTransferTransport(http_transport_factory(api_key))
    service = KeeperHubControlledSimulationService(
        transport,
        selected_intent,
        SQLiteExecutionAttemptStore(paths["attempts"]),
        SQLiteKeeperHubAuthorizationLedger(paths["authorizations"]),
    )
    authorization = KeeperHubSimulationAuthorization(
        action_sheet_id=sheet["action_sheet_id"],
        approval_reference=sheet["simulation_approval_reference"],
        attempt_id=plan.attempt_id,
        request_fingerprint=plan.request_fingerprint,
        authorized_at_utc=now,
        expires_at_utc=now + timedelta(minutes=5),
    )
    try:
        receipt = service.simulate(plan, authorization, now)
    except KeeperHubControlledExecutionError as error:
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": sheet["run_ref"],
            "reason": error.code,
            "simulation_posts": transport.calls,
            "broadcast_posts": 0,
            "provider_summary": _safe_provider_summary(transport.last_response),
            "broadcast_authorized": False,
            "funds_movement": "NONE_FROM_SIMULATION",
            "retry_same_effect": False,
        }
    if receipt.decision is not KeeperHubSimulationDecision.ELIGIBLE_FOR_BROADCAST_APPROVAL:
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": sheet["run_ref"],
            "reason": receipt.decision.value,
            "simulation_posts": transport.calls,
            "broadcast_posts": 0,
            "provider_summary": _safe_provider_summary(transport.last_response),
            "broadcast_authorized": False,
            "funds_movement": "NONE_FROM_SIMULATION",
            "retry_same_effect": False,
        }
    return {
        "schema": _TOOL_SCHEMA,
        "status": "PASS",
        "run_ref": sheet["run_ref"],
        "decision": "ELIGIBLE_FOR_SEPARATE_BROADCAST_APPROVAL",
        "simulation_posts": transport.calls,
        "broadcast_posts": 0,
        "provider_summary": _safe_provider_summary(transport.last_response),
        "broadcast_approval_challenge": _broadcast_challenge(
            sheet["action_sheet_id"],
            receipt,
        ),
        "broadcast_authorized": False,
        "funds_movement": "NONE_FROM_SIMULATION",
        "retry_same_effect": False,
    }


def _advance_after_ack(
    mission_store: SQLiteMissionStore,
    mission_key: str,
    effect_ref: str,
    observed_at: datetime,
) -> bool:
    try:
        current = mission_store.get(mission_key)
        if current is None:
            return False
        if current.record.state is MissionState.READY_FOR_EXECUTION:
            current = mission_store.transition_mission(
                mission_key,
                current.revision,
                MissionState.EXECUTING,
                observed_at,
            )
        effect = next(
            (item for item in current.record.effects if item.effect_ref == effect_ref),
            None,
        )
        if effect is None:
            return False
        if effect.state is EffectState.PLANNED:
            current = mission_store.transition_effect(
                mission_key,
                effect_ref,
                current.revision,
                EffectState.RESERVED,
                observed_at,
            )
            effect = next(
                item for item in current.record.effects if item.effect_ref == effect_ref
            )
        if effect.state is EffectState.RESERVED:
            current = mission_store.transition_effect(
                mission_key,
                effect_ref,
                current.revision,
                EffectState.SUBMITTED,
                observed_at,
            )
        if current.record.state is MissionState.EXECUTING:
            current = mission_store.transition_mission(
                mission_key,
                current.revision,
                MissionState.VERIFYING,
                observed_at,
            )
        final_effect = next(
            item for item in current.record.effects if item.effect_ref == effect_ref
        )
        return (
            current.record.state is MissionState.VERIFYING
            and final_effect.state is EffectState.SUBMITTED
        )
    except (SQLiteMissionStoreError, StopIteration):
        return False


def execute_broadcast(
    *,
    api_key: str,
    approval: str,
    run_ref: str,
    approve_testnet_write: bool,
    base_root: Path | None = None,
    observed_at: datetime | None = None,
    http_transport_factory: Callable[[str], Any] = KeeperHubHttpTransport,
) -> dict[str, Any]:
    if not approve_testnet_write:
        _fail("BROADCAST_RUNTIME_FLAG_REQUIRED")
    sheet, _, selected_intent, plan, paths = _load_context(
        run_ref,
        base_root=base_root,
    )
    ledger = SQLiteKeeperHubAuthorizationLedger(paths["authorizations"])
    receipt = load_keeperhub_simulation_receipt(
        ledger,
        sheet["simulation_approval_reference"],
    )
    expected_challenge = _broadcast_challenge(
        sheet["action_sheet_id"],
        receipt,
    )
    if approval != expected_challenge:
        _fail("BROADCAST_APPROVAL_MISMATCH")
    if receipt.decision is not KeeperHubSimulationDecision.ELIGIBLE_FOR_BROADCAST_APPROVAL:
        _fail("SIMULATION_NOT_ELIGIBLE")
    now = observed_at or _utc_now()
    transport = _CapturingTransferTransport(http_transport_factory(api_key))
    authorization = KeeperHubBroadcastAuthorization(
        action_sheet_id=sheet["action_sheet_id"],
        approval_reference=_broadcast_approval_reference(
            sheet["action_sheet_id"],
            receipt,
        ),
        attempt_id=plan.attempt_id,
        request_fingerprint=plan.request_fingerprint,
        simulation_body_fingerprint=receipt.simulation_body_fingerprint,
        approved_at_utc=now,
        expires_at_utc=now + timedelta(minutes=5),
        runtime_flag=_REQUIRED_BROADCAST_FLAG,
    )
    direct = KeeperHubApprovedBroadcastPort(
        transport,
        selected_intent,
        receipt,
        authorization,
        ledger,
    )
    references = SQLiteProviderExecutionReferenceStore(paths["provider_references"])
    wrapped = ProviderReferencePersistingPort(
        direct,
        references,
        provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
    )
    mission_store = SQLiteMissionStore(paths["missions"])
    try:
        stored_attempt = ExecutionDispatchService(
            mission_store,
            SQLiteExecutionAttemptStore(paths["attempts"]),
        ).dispatch(
            plan,
            wrapped,
            now,
        )
    except (
        ExecutionDispatchError,
        KeeperHubControlledExecutionError,
        SQLiteProviderExecutionReferenceStoreError,
    ) as error:
        code = getattr(error, "code", "BROADCAST_OUTCOME_UNKNOWN")
        try:
            reference = references.get(plan.attempt_id)
        except Exception:
            reference = None
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": sheet["run_ref"],
            "reason": code,
            "simulation_posts": 0,
            "broadcast_posts": transport.calls,
            "provider_reference_present": reference is not None,
            "provider_summary": _safe_provider_summary(transport.last_response),
            "funds_movement": (
                "UNKNOWN_AFTER_BROADCAST_ATTEMPT"
                if transport.calls
                else "NO_BROADCAST_SENT"
            ),
            "retry_same_effect": False,
        }
    reference = references.get(plan.attempt_id)
    if (
        stored_attempt.record.state is not ExecutionAttemptState.PROVIDER_ACKNOWLEDGED
        or reference is None
    ):
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": sheet["run_ref"],
            "reason": "PROVIDER_ACK_PERSISTENCE_INCOMPLETE",
            "simulation_posts": 0,
            "broadcast_posts": transport.calls,
            "provider_reference_present": reference is not None,
            "funds_movement": "UNKNOWN_PENDING_RECONCILIATION",
            "retry_same_effect": False,
        }
    mission_advanced = _advance_after_ack(
        mission_store,
        plan.mission_key,
        _EFFECT_REF,
        now,
    )
    return {
        "schema": _TOOL_SCHEMA,
        "status": "PASS" if mission_advanced else "STOP",
        "run_ref": sheet["run_ref"],
        "decision": (
            "PROVIDER_ACKNOWLEDGED_REQUIRES_STATUS_AND_CHAIN_VERIFICATION"
            if mission_advanced
            else "PROVIDER_ACKNOWLEDGED_LOCAL_RECONCILIATION_REQUIRED"
        ),
        "simulation_posts": 0,
        "broadcast_posts": transport.calls,
        "provider_reference_present": True,
        "provider_reference": _short(reference.provider_reference),
        "provider_summary": _safe_provider_summary(transport.last_response),
        "funds_movement": "UNKNOWN_PENDING_CHAIN_VERIFICATION",
        "retry_same_effect": False,
    }


def observe_provider_status(
    *,
    api_key: str,
    run_ref: str,
    base_root: Path | None = None,
    http_transport_factory: Callable[[str], Any] = KeeperHubHttpTransport,
) -> dict[str, Any]:
    sheet, _, _, plan, paths = _load_context(
        run_ref,
        base_root=base_root,
    )
    references = SQLiteProviderExecutionReferenceStore(paths["provider_references"])
    reference = references.get(plan.attempt_id)
    if reference is None:
        _fail("PROVIDER_REFERENCE_NOT_FOUND")
    transport = http_transport_factory(api_key)
    try:
        observation = KeeperHubExecutionStatusObserver(transport).observe(reference)
    except Exception as error:
        code = getattr(error, "code", "STATUS_OUTCOME_UNKNOWN")
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": sheet["run_ref"],
            "reason": code,
            "status_gets": 1,
            "retry_broadcast": False,
        }
    result: dict[str, Any] = {
        "schema": _TOOL_SCHEMA,
        "status": "PASS",
        "run_ref": sheet["run_ref"],
        "provider_status": observation.status.value,
        "status_gets": 1,
        "poll_after_seconds": observation.poll_after_seconds,
        "terminal": observation.is_terminal,
        "requires_independent_chain_verification": (
            observation.requires_independent_chain_verification
        ),
        "retry_broadcast": False,
    }
    if observation.transaction_hash is not None:
        result["transaction_hash"] = observation.transaction_hash
    if observation.transaction_link is not None:
        result["transaction_link"] = observation.transaction_link
    return result


def local_status(
    run_ref: str,
    *,
    base_root: Path | None = None,
) -> dict[str, Any]:
    sheet, _, _, plan, paths = _load_context(
        run_ref,
        base_root=base_root,
    )
    mission_store = SQLiteMissionStore(paths["missions"])
    attempt_store = SQLiteExecutionAttemptStore(paths["attempts"])
    ledger = SQLiteKeeperHubAuthorizationLedger(paths["authorizations"])
    references = SQLiteProviderExecutionReferenceStore(paths["provider_references"])
    mission = mission_store.get(plan.mission_key)
    attempt = attempt_store.get(plan.attempt_id)
    try:
        simulation = ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.SIMULATION,
            plan.attempt_id,
        )
    except Exception:
        simulation = None
    try:
        broadcast = ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.BROADCAST,
            plan.attempt_id,
        )
    except Exception:
        broadcast = None
    try:
        reference = references.get(plan.attempt_id)
    except Exception:
        reference = None
    return {
        "schema": _TOOL_SCHEMA,
        "status": "LOCAL_STATUS",
        "run_ref": sheet["run_ref"],
        "network_calls": 0,
        "recipient_masked": _mask_address(sheet["recipient_address"]),
        "mission_state": (
            mission.record.state.value if mission is not None else "NOT_FOUND"
        ),
        "effect_state": (
            mission.record.effects[0].state.value
            if mission is not None and mission.record.effects
            else "NOT_FOUND"
        ),
        "attempt_state": (
            attempt.record.state.value if attempt is not None else "NOT_CREATED"
        ),
        "simulation_authorization_state": (
            simulation.state.value if simulation is not None else "NOT_CLAIMED"
        ),
        "broadcast_authorization_state": (
            broadcast.state.value if broadcast is not None else "NOT_CLAIMED"
        ),
        "provider_reference_present": reference is not None,
        "retry_same_effect": False,
    }


def _required_env(name: str, code: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def _emit(value: Mapping[str, Any]) -> int:
    print(json.dumps(dict(value), ensure_ascii=False, sort_keys=True))
    return 0 if value.get("status") in {"PASS", "PREPARED", "LOCAL_STATUS"} else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-shot KeeperHub Base Sepolia rehearsal execution runner."
    )
    parser.add_argument(
        "command",
        choices=("prepare", "simulate", "broadcast", "provider-status", "status"),
    )
    parser.add_argument("--run-ref", required=True)
    parser.add_argument(
        _REQUIRED_BROADCAST_FLAG,
        action="store_true",
        dest="approve_testnet_write",
        help="Required only for the separately approved testnet broadcast.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "prepare":
            return _emit(
                prepare_action_sheet(
                    _required_env(
                        _RECIPIENT_ENV,
                        "LOCAL_REHEARSAL_RECIPIENT_NOT_SET",
                    ),
                    args.run_ref,
                )
            )
        if args.command == "simulate":
            return _emit(
                execute_simulation(
                    api_key=_required_env(_API_KEY_ENV, "LOCAL_API_KEY_NOT_SET"),
                    approval=_required_env(
                        _SIM_APPROVAL_ENV,
                        "LOCAL_SIMULATION_APPROVAL_NOT_SET",
                    ),
                    run_ref=args.run_ref,
                )
            )
        if args.command == "broadcast":
            return _emit(
                execute_broadcast(
                    api_key=_required_env(_API_KEY_ENV, "LOCAL_API_KEY_NOT_SET"),
                    approval=_required_env(
                        _BROADCAST_APPROVAL_ENV,
                        "LOCAL_BROADCAST_APPROVAL_NOT_SET",
                    ),
                    run_ref=args.run_ref,
                    approve_testnet_write=args.approve_testnet_write,
                )
            )
        if args.command == "provider-status":
            return _emit(
                observe_provider_status(
                    api_key=_required_env(_API_KEY_ENV, "LOCAL_API_KEY_NOT_SET"),
                    run_ref=args.run_ref,
                )
            )
        return _emit(local_status(args.run_ref))
    except RehearsalExecutionError as error:
        return _emit(
            {
                "schema": _TOOL_SCHEMA,
                "status": "STOP",
                "run_ref": getattr(args, "run_ref", None),
                "reason": error.code,
                "retry_same_effect": False,
            }
        )
    except KeeperHubHttpTransportError as error:
        return _emit(
            {
                "schema": _TOOL_SCHEMA,
                "status": "STOP",
                "run_ref": getattr(args, "run_ref", None),
                "reason": error.code,
                "provider_summary": _transport_error_summary(error),
                "retry_same_effect": False,
            }
        )
    except Exception:
        return _emit(
            {
                "schema": _TOOL_SCHEMA,
                "status": "STOP",
                "run_ref": getattr(args, "run_ref", None),
                "reason": "UNEXPECTED_LOCAL_FAILURE",
                "retry_same_effect": False,
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
