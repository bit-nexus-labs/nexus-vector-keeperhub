"""Controlled two-effect KeeperHub video Mission for Base Sepolia USDC.

This operator tool demonstrates the core Nexus Vector guarantee with one durable
Mission containing two ordered economic effects: Anna, then Mark. Every effect
has its own deterministic attempt identity, simulation authorization, broadcast
authorization, provider reference, immutable provider transaction binding, and
independent Base Sepolia verification.

The tool never auto-retries a mutating POST. A confirmed effect is permanently
skipped, and Mark cannot become dispatchable until Anna is CHAIN_CONFIRMED.
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
from nexus_vector.application.execution_reconciliation import (  # noqa: E402
    ExecutionReconciliationError,
    ExecutionReconciliationService,
    ReconciliationOutcome,
)
from nexus_vector.application.mission_admission import MissionAdmissionService  # noqa: E402
from nexus_vector.application.provider_reference_port import (  # noqa: E402
    ProviderReferencePersistingPort,
)
from nexus_vector.domain.execution_attempts import (  # noqa: E402
    ExecutionAttemptPlan,
    ExecutionAttemptState,
    build_execution_attempt_plan,
)
from nexus_vector.domain.mission_identity import SCHEMA_VERSION, derive_effect_id  # noqa: E402
from nexus_vector.domain.mission_models import (  # noqa: E402
    EffectState,
    MissionRequest,
    MissionState,
)
from nexus_vector.integrations.base_sepolia_rpc_verification import (  # noqa: E402
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_RPC_URL,
    BaseSepoliaErc20TransferVerifier,
    BaseSepoliaJsonRpcTransport,
    BaseSepoliaVerificationError,
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
    KeeperHubExecutionStatus,
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

_TOOL_SCHEMA = "nexus-vector.anna-mark-video-mission.v1"
_BINDING_SCHEMA = "nexus-vector.anna-mark-video-provider-binding.v1"
_VIDEO_RECIPIENT_SCHEMA = 1
_PURPOSE = "ANNA_MARK_VIDEO_MISSION_TESTNET"
_MISSION_NAMESPACE = "nexus-vector.keeperhub"
_MISSION_TYPE = "ANNA_MARK_VIDEO_TESTNET"
_SEQUENCE = ("anna", "mark")
_AMOUNT_BASE_UNITS = {"anna": 1, "mark": 2}
_BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e".lower()
_TOKEN_DECIMALS = 6
_MINIMUM_CONFIRMATIONS = 2
_REQUIRED_BROADCAST_FLAG = "--approve-testnet-write"
_API_KEY_ENV = "KEEPERHUB_API_KEY"
_RUN_REF_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_HASH_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")
_SAFE_DIGITS = re.compile(r"[0-9]{1,32}")
_MAX_JSON_BYTES = 131_072


class AnnaMarkVideoMissionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise AnnaMarkVideoMissionError(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    if value.utcoffset() != timedelta(0):
        _fail("NON_UTC_TIMESTAMP")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _digest(prefix: str, value: Any, length: int = 64) -> str:
    return prefix + hashlib.sha256(_canonical_json(value)).hexdigest()[:length]


def _validate_run_ref(value: Any) -> str:
    if not isinstance(value, str) or _RUN_REF_PATTERN.fullmatch(value) is None:
        _fail("INVALID_RUN_REF")
    return value


def _validate_effect_ref(value: Any) -> str:
    if value not in _SEQUENCE:
        _fail("INVALID_EFFECT_REF")
    return str(value)


def _validate_address(value: Any, code: str) -> str:
    if not isinstance(value, str) or _EVM_ADDRESS.fullmatch(value) is None:
        _fail(code)
    return value.lower()


def _validate_hash(value: Any) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        _fail("INVALID_TRANSACTION_HASH")
    return value.lower()


def _mask_address(value: str) -> str:
    checked = _validate_address(value, "INVALID_ADDRESS_FOR_MASKING")
    return f"{checked[:8]}…{checked[-6:]}"


def _short(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) <= 18:
        return value
    return f"{value[:10]}…{value[-6:]}"


def _provider_reference_fingerprint(value: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("INVALID_PROVIDER_REFERENCE")
    return "khref_sha256_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


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


def _exclusive_json_write(path: Path, value: Mapping[str, Any], *, exists_code: str) -> None:
    payload = _canonical_json(dict(value)) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _fail(exists_code)
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


def _base_root() -> Path:
    return Path.home() / ".nexus-vector" / "anna-mark-video-mission-v1"


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
        "bindings": root / "provider_bindings",
    }


def _wallet_registry_path(wallet_path: Path | None = None) -> Path:
    if wallet_path is not None:
        return wallet_path
    local = os.environ.get("LOCALAPPDATA")
    if not isinstance(local, str) or not local:
        _fail("LOCALAPPDATA_NOT_AVAILABLE")
    return Path(local) / "NexusVector" / "Config" / "wallets.private-local.json"


def _video_recipients_path(video_path: Path | None = None) -> Path:
    if video_path is not None:
        return video_path
    local = os.environ.get("LOCALAPPDATA")
    if not isinstance(local, str) or not local:
        _fail("LOCALAPPDATA_NOT_AVAILABLE")
    return (
        Path(local)
        / "NexusVector"
        / "Config"
        / "anna_mark_video_recipients.private-local.json"
    )


def _load_wallet_context(
    *,
    wallet_path: Path | None = None,
    video_path: Path | None = None,
) -> tuple[str, dict[str, str]]:
    wallets_doc = _read_json(
        _wallet_registry_path(wallet_path),
        missing_code="WALLET_REGISTRY_NOT_FOUND",
        corrupt_code="WALLET_REGISTRY_INVALID",
    )
    if wallets_doc.get("schema_version") != 1:
        _fail("WALLET_REGISTRY_SCHEMA_MISMATCH")
    network = wallets_doc.get("network")
    wallets = wallets_doc.get("wallets")
    safety = wallets_doc.get("safety")
    if not isinstance(network, Mapping) or not isinstance(wallets, Mapping) or not isinstance(safety, Mapping):
        _fail("WALLET_REGISTRY_INVALID")
    if network.get("chain_id") != BASE_SEPOLIA_CHAIN_ID or network.get("environment") != "testnet":
        _fail("WALLET_REGISTRY_NETWORK_MISMATCH")
    if safety.get("mainnet_blocked") is not True:
        _fail("MAINNET_BLOCK_NOT_CONFIRMED")
    sender = _validate_address(
        wallets.get("keeperhub_organization_wallet"),
        "INVALID_KEEPERHUB_ORGANIZATION_WALLET",
    )
    anna = _validate_address(
        wallets.get("personal_recipient_wallet"),
        "INVALID_ANNA_RECIPIENT_WALLET",
    )

    video_doc = _read_json(
        _video_recipients_path(video_path),
        missing_code="VIDEO_RECIPIENT_CONFIG_NOT_FOUND",
        corrupt_code="VIDEO_RECIPIENT_CONFIG_INVALID",
    )
    if video_doc.get("schema_version") != _VIDEO_RECIPIENT_SCHEMA:
        _fail("VIDEO_RECIPIENT_CONFIG_SCHEMA_MISMATCH")
    vnetwork = video_doc.get("network")
    recipients = video_doc.get("recipients")
    vsafety = video_doc.get("safety")
    if not isinstance(vnetwork, Mapping) or not isinstance(recipients, Mapping) or not isinstance(vsafety, Mapping):
        _fail("VIDEO_RECIPIENT_CONFIG_INVALID")
    if vnetwork.get("chain_id") != BASE_SEPOLIA_CHAIN_ID or vnetwork.get("environment") != "testnet":
        _fail("VIDEO_RECIPIENT_CONFIG_NETWORK_MISMATCH")
    if vsafety.get("mainnet_blocked") is not True:
        _fail("VIDEO_RECIPIENT_MAINNET_BLOCK_NOT_CONFIRMED")
    mark = _validate_address(
        recipients.get("mark_recipient_wallet"),
        "INVALID_MARK_RECIPIENT_WALLET",
    )
    if len({sender, anna, mark}) != 3:
        _fail("VIDEO_WALLETS_MUST_BE_DISTINCT")
    return sender, {"anna": anna, "mark": mark}


def _request_mapping(run_ref: str, recipients: Mapping[str, str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mission_namespace": _MISSION_NAMESPACE,
        "mission_ref": run_ref,
        "mission_type": _MISSION_TYPE,
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "asset": {"token_address": _BASE_SEPOLIA_USDC, "decimals": _TOKEN_DECIMALS},
        "effects": [
            {
                "effect_ref": effect_ref,
                "recipient": recipients[effect_ref],
                "amount_base_units": _AMOUNT_BASE_UNITS[effect_ref],
            }
            for effect_ref in _SEQUENCE
        ],
    }


def _request_key(run_ref: str, effect_ref: str, effect_id: str) -> str:
    suffix = hashlib.sha256(
        f"{run_ref}\x00{effect_ref}\x00{effect_id}".encode("utf-8")
    ).hexdigest()[:32]
    return f"nv-video-{effect_ref}-{suffix}"


def _simulation_approval_reference(action_sheet_id: str, effect_ref: str) -> str:
    return _digest("sim_", [action_sheet_id, effect_ref], 48)


def _simulation_challenge(action_sheet_id: str, effect_ref: str, attempt_id: str) -> str:
    suffix = hashlib.sha256(
        f"{action_sheet_id}\x00{effect_ref}\x00{attempt_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"SIMULATE-{effect_ref.upper()}-{suffix}"


def _broadcast_challenge(
    action_sheet_id: str,
    effect_ref: str,
    receipt: KeeperHubSimulationReceipt,
) -> str:
    suffix = hashlib.sha256(
        (
            f"{action_sheet_id}\x00{effect_ref}\x00{receipt.attempt_id}\x00"
            f"{receipt.simulation_body_fingerprint}"
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"BROADCAST-{effect_ref.upper()}-{suffix}"


def _broadcast_approval_reference(
    action_sheet_id: str,
    effect_ref: str,
    receipt: KeeperHubSimulationReceipt,
) -> str:
    return _digest(
        "brd_",
        [action_sheet_id, effect_ref, receipt.attempt_id, receipt.simulation_body_fingerprint],
        48,
    )


def _build_action_sheet(
    *,
    run_ref: str,
    sender: str,
    recipients: Mapping[str, str],
    created_at: datetime,
) -> dict[str, Any]:
    request_mapping = _request_mapping(run_ref, recipients)
    request = MissionRequest.from_mapping(request_mapping)
    identity = request.build_identity()
    action_sheet_id = _digest("ams_", [run_ref, identity.mission_key], 48)
    effects: dict[str, Any] = {}
    for effect_ref in _SEQUENCE:
        effect_id = derive_effect_id(identity.mission_key, effect_ref)
        intent = KeeperHubTransferIntent(
            chain_id=BASE_SEPOLIA_CHAIN_ID,
            recipient_address=recipients[effect_ref],
            amount_base_units=_AMOUNT_BASE_UNITS[effect_ref],
            token_address=_BASE_SEPOLIA_USDC,
            token_decimals=_TOKEN_DECIMALS,
        )
        request_key = _request_key(run_ref, effect_ref, effect_id)
        plan = build_execution_attempt_plan(
            mission_key=identity.mission_key,
            effect_id=effect_id,
            provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
            request_key=request_key,
            request_material=intent.request_material,
        )
        effects[effect_ref] = {
            "effect_ref": effect_ref,
            "effect_id": effect_id,
            "attempt_id": plan.attempt_id,
            "request_key": request_key,
            "request_fingerprint": plan.request_fingerprint,
            "recipient_address": recipients[effect_ref],
            "amount_base_units": _AMOUNT_BASE_UNITS[effect_ref],
            "simulation_approval_reference": _simulation_approval_reference(
                action_sheet_id, effect_ref
            ),
            "simulation_approval_challenge": _simulation_challenge(
                action_sheet_id, effect_ref, plan.attempt_id
            ),
        }
    return {
        "schema": _TOOL_SCHEMA,
        "purpose": _PURPOSE,
        "run_ref": run_ref,
        "action_sheet_id": action_sheet_id,
        "mission_key": identity.mission_key,
        "mission_request": request_mapping,
        "expected_sender_address": sender,
        "sequence": list(_SEQUENCE),
        "effects": effects,
        "created_at_utc": _timestamp(created_at),
        "maximum_simulation_posts_per_effect": 1,
        "maximum_broadcast_posts_per_effect": 1,
        "maximum_mutating_calls_per_effect": 1,
        "mainnet_allowed": False,
    }


def _validate_action_sheet(sheet: Mapping[str, Any]) -> tuple[MissionRequest, dict[str, tuple[KeeperHubTransferIntent, ExecutionAttemptPlan]]]:
    required = {
        "schema",
        "purpose",
        "run_ref",
        "action_sheet_id",
        "mission_key",
        "mission_request",
        "expected_sender_address",
        "sequence",
        "effects",
        "created_at_utc",
        "maximum_simulation_posts_per_effect",
        "maximum_broadcast_posts_per_effect",
        "maximum_mutating_calls_per_effect",
        "mainnet_allowed",
    }
    if set(sheet.keys()) != required:
        _fail("ACTION_SHEET_FIELD_MISMATCH")
    if sheet.get("schema") != _TOOL_SCHEMA or sheet.get("purpose") != _PURPOSE:
        _fail("ACTION_SHEET_SCHEMA_MISMATCH")
    run_ref = _validate_run_ref(sheet.get("run_ref"))
    if sheet.get("sequence") != list(_SEQUENCE):
        _fail("ACTION_SHEET_SEQUENCE_MISMATCH")
    if sheet.get("mainnet_allowed") is not False:
        _fail("ACTION_SHEET_MAINNET_NOT_BLOCKED")
    for field in (
        "maximum_simulation_posts_per_effect",
        "maximum_broadcast_posts_per_effect",
        "maximum_mutating_calls_per_effect",
    ):
        if sheet.get(field) != 1:
            _fail("ACTION_SHEET_BUDGET_MISMATCH")
    sender = _validate_address(sheet.get("expected_sender_address"), "INVALID_ACTION_SHEET_SENDER")
    request = MissionRequest.from_mapping(sheet.get("mission_request"))
    identity = request.build_identity()
    if identity.mission_key != sheet.get("mission_key") or request.mission_ref != run_ref:
        _fail("ACTION_SHEET_MISSION_IDENTITY_MISMATCH")
    if request.chain_id != BASE_SEPOLIA_CHAIN_ID or request.asset.token_address != _BASE_SEPOLIA_USDC:
        _fail("ACTION_SHEET_NETWORK_MISMATCH")
    if request.asset.decimals != _TOKEN_DECIMALS:
        _fail("ACTION_SHEET_TOKEN_DECIMALS_MISMATCH")
    if not isinstance(sheet.get("created_at_utc"), str):
        _fail("ACTION_SHEET_TIMESTAMP_INVALID")
    effects_raw = sheet.get("effects")
    if not isinstance(effects_raw, Mapping) or set(effects_raw.keys()) != set(_SEQUENCE):
        _fail("ACTION_SHEET_EFFECT_SET_MISMATCH")
    request_by_ref = {item.effect_ref: item for item in request.effects}
    contexts: dict[str, tuple[KeeperHubTransferIntent, ExecutionAttemptPlan]] = {}
    for effect_ref in _SEQUENCE:
        value = effects_raw[effect_ref]
        if not isinstance(value, Mapping):
            _fail("ACTION_SHEET_EFFECT_INVALID")
        expected_fields = {
            "effect_ref",
            "effect_id",
            "attempt_id",
            "request_key",
            "request_fingerprint",
            "recipient_address",
            "amount_base_units",
            "simulation_approval_reference",
            "simulation_approval_challenge",
        }
        if set(value.keys()) != expected_fields or value.get("effect_ref") != effect_ref:
            _fail("ACTION_SHEET_EFFECT_FIELD_MISMATCH")
        request_effect = request_by_ref.get(effect_ref)
        if request_effect is None:
            _fail("ACTION_SHEET_EFFECT_REQUEST_MISSING")
        recipient = _validate_address(value.get("recipient_address"), "INVALID_ACTION_SHEET_RECIPIENT")
        amount = value.get("amount_base_units")
        if recipient != request_effect.recipient or amount != request_effect.amount_base_units:
            _fail("ACTION_SHEET_EFFECT_ECONOMIC_MISMATCH")
        if amount != _AMOUNT_BASE_UNITS[effect_ref]:
            _fail("ACTION_SHEET_EFFECT_AMOUNT_MISMATCH")
        effect_id = derive_effect_id(identity.mission_key, effect_ref)
        if value.get("effect_id") != effect_id:
            _fail("ACTION_SHEET_EFFECT_ID_MISMATCH")
        intent = KeeperHubTransferIntent(
            chain_id=BASE_SEPOLIA_CHAIN_ID,
            recipient_address=recipient,
            amount_base_units=amount,
            token_address=_BASE_SEPOLIA_USDC,
            token_decimals=_TOKEN_DECIMALS,
        )
        request_key = value.get("request_key")
        if not isinstance(request_key, str) or not request_key:
            _fail("ACTION_SHEET_REQUEST_KEY_INVALID")
        plan = build_execution_attempt_plan(
            mission_key=identity.mission_key,
            effect_id=effect_id,
            provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
            request_key=request_key,
            request_material=intent.request_material,
        )
        if plan.attempt_id != value.get("attempt_id") or plan.request_fingerprint != value.get("request_fingerprint"):
            _fail("ACTION_SHEET_ATTEMPT_IDENTITY_MISMATCH")
        if value.get("simulation_approval_reference") != _simulation_approval_reference(
            sheet["action_sheet_id"], effect_ref
        ):
            _fail("ACTION_SHEET_SIMULATION_REFERENCE_MISMATCH")
        if value.get("simulation_approval_challenge") != _simulation_challenge(
            sheet["action_sheet_id"], effect_ref, plan.attempt_id
        ):
            _fail("ACTION_SHEET_SIMULATION_CHALLENGE_MISMATCH")
        contexts[effect_ref] = (intent, plan)
    if sender in {request_by_ref["anna"].recipient, request_by_ref["mark"].recipient}:
        _fail("ACTION_SHEET_SENDER_RECIPIENT_CONFLICT")
    return request, contexts


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
        states = {item.state for item in current.record.effects}
        if states == {EffectState.PLANNED} or states <= {
            EffectState.PLANNED,
            EffectState.CHAIN_CONFIRMED,
        }:
            store.transition_mission(
                current.record.mission_key,
                current.revision,
                MissionState.READY_FOR_EXECUTION,
                observed_at,
            )


def _load_context(
    run_ref: str,
    *,
    effect_ref: str | None = None,
    base_root: Path | None = None,
) -> tuple[dict[str, Any], MissionRequest, dict[str, tuple[KeeperHubTransferIntent, ExecutionAttemptPlan]], dict[str, Path]]:
    root = _run_root(run_ref, base_root)
    paths = _paths(root)
    sheet = _read_json(
        paths["action_sheet"],
        missing_code="ACTION_SHEET_NOT_FOUND",
        corrupt_code="ACTION_SHEET_INVALID",
    )
    request, contexts = _validate_action_sheet(sheet)
    if effect_ref is not None:
        _validate_effect_ref(effect_ref)
    return sheet, request, contexts, paths


def _effect_by_ref(mission: Any, effect_ref: str):
    effect = next(
        (item for item in mission.record.effects if item.effect_ref == effect_ref),
        None,
    )
    if effect is None:
        _fail("EFFECT_NOT_FOUND")
    return effect


def _next_effect(mission: Any) -> str | None:
    for effect_ref in _SEQUENCE:
        if _effect_by_ref(mission, effect_ref).state is not EffectState.CHAIN_CONFIRMED:
            return effect_ref
    return None


def _gate_dispatchable_effect(mission: Any, effect_ref: str) -> str:
    selected = _validate_effect_ref(effect_ref)
    effect = _effect_by_ref(mission, selected)
    if effect.state is EffectState.CHAIN_CONFIRMED:
        return "SKIP_CONFIRMED"
    next_effect = _next_effect(mission)
    if selected != next_effect:
        _fail("OUT_OF_SEQUENCE_EFFECT")
    if mission.record.state is not MissionState.READY_FOR_EXECUTION:
        _fail("MISSION_NOT_READY_FOR_EXECUTION")
    if effect.state is not EffectState.PLANNED:
        _fail("EFFECT_NOT_DISPATCHABLE")
    if selected == "mark" and _effect_by_ref(mission, "anna").state is not EffectState.CHAIN_CONFIRMED:
        _fail("ANNA_NOT_CHAIN_CONFIRMED")
    return "DISPATCHABLE"


def _preview(sheet: Mapping[str, Any], mission: Any) -> dict[str, Any]:
    return {
        "schema": _TOOL_SCHEMA,
        "status": "PREPARED",
        "run_ref": sheet["run_ref"],
        "mission_key": _short(sheet["mission_key"]),
        "mission_state": mission.record.state.value,
        "expected_sender_masked": _mask_address(sheet["expected_sender_address"]),
        "effects": [
            {
                "effect_ref": effect_ref,
                "effect_id": _short(sheet["effects"][effect_ref]["effect_id"]),
                "attempt_id": _short(sheet["effects"][effect_ref]["attempt_id"]),
                "recipient_masked": _mask_address(
                    sheet["effects"][effect_ref]["recipient_address"]
                ),
                "amount_base_units": sheet["effects"][effect_ref]["amount_base_units"],
                "state": _effect_by_ref(mission, effect_ref).state.value,
            }
            for effect_ref in _SEQUENCE
        ],
        "next_effect": _next_effect(mission),
        "simulation_approval_challenge": sheet["effects"]["anna"][
            "simulation_approval_challenge"
        ],
        "maximum_mutating_calls_per_effect": 1,
        "network_calls": 0,
        "mainnet_allowed": False,
    }


def prepare_video_mission(
    run_ref: str,
    *,
    base_root: Path | None = None,
    wallet_path: Path | None = None,
    video_path: Path | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    checked_ref = _validate_run_ref(run_ref)
    sender, recipients = _load_wallet_context(
        wallet_path=wallet_path,
        video_path=video_path,
    )
    now = observed_at or _utc_now()
    root = _run_root(checked_ref, base_root)
    paths = _paths(root)
    _ensure_private_directory(root)
    _ensure_private_directory(paths["bindings"])
    if paths["action_sheet"].exists():
        sheet = _read_json(
            paths["action_sheet"],
            missing_code="ACTION_SHEET_NOT_FOUND",
            corrupt_code="ACTION_SHEET_INVALID",
        )
        request, _ = _validate_action_sheet(sheet)
        if sheet["run_ref"] != checked_ref:
            _fail("ACTION_SHEET_RUN_REF_MISMATCH")
        if sheet["expected_sender_address"] != sender:
            _fail("ACTION_SHEET_SENDER_BINDING_MISMATCH")
        for effect_ref in _SEQUENCE:
            if sheet["effects"][effect_ref]["recipient_address"] != recipients[effect_ref]:
                _fail("ACTION_SHEET_RECIPIENT_BINDING_MISMATCH")
    else:
        sheet = _build_action_sheet(
            run_ref=checked_ref,
            sender=sender,
            recipients=recipients,
            created_at=now,
        )
        request, _ = _validate_action_sheet(sheet)
        _exclusive_json_write(
            paths["action_sheet"],
            sheet,
            exists_code="ACTION_SHEET_ALREADY_EXISTS",
        )
    try:
        _admit_ready_mission(request, paths, now)
        SQLiteExecutionAttemptStore(paths["attempts"]).initialize()
        SQLiteKeeperHubAuthorizationLedger(paths["authorizations"]).initialize()
        SQLiteProviderExecutionReferenceStore(paths["provider_references"]).initialize()
    except Exception as error:
        _fail(getattr(error, "code", "MISSION_PREPARATION_FAILED"))
    mission = SQLiteMissionStore(paths["missions"]).get(sheet["mission_key"])
    if mission is None:
        _fail("MISSION_NOT_FOUND")
    return _preview(sheet, mission)


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
        response = self._inner.post_transfer(body, idempotency_key=idempotency_key)
        self.last_response = response
        return response


def _safe_provider_summary(response: KeeperHubTransportResponse | None) -> dict[str, Any] | None:
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


def execute_simulation(
    *,
    api_key: str,
    approval: str,
    run_ref: str,
    effect_ref: str,
    base_root: Path | None = None,
    observed_at: datetime | None = None,
    http_transport_factory: Callable[[str], Any] = KeeperHubHttpTransport,
) -> dict[str, Any]:
    sheet, _, contexts, paths = _load_context(
        run_ref,
        effect_ref=effect_ref,
        base_root=base_root,
    )
    selected = _validate_effect_ref(effect_ref)
    mission_store = SQLiteMissionStore(paths["missions"])
    mission = mission_store.get(sheet["mission_key"])
    if mission is None:
        _fail("MISSION_NOT_FOUND")
    gate = _gate_dispatchable_effect(mission, selected)
    if gate == "SKIP_CONFIRMED":
        return {
            "schema": _TOOL_SCHEMA,
            "status": "PASS",
            "decision": "SKIP_ALREADY_CHAIN_CONFIRMED",
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
            "simulation_posts": 0,
            "broadcast_posts": 0,
            "retry_same_effect": False,
        }
    effect_sheet = sheet["effects"][selected]
    if approval != effect_sheet["simulation_approval_challenge"]:
        _fail("SIMULATION_APPROVAL_MISMATCH")
    intent, plan = contexts[selected]
    ledger = SQLiteKeeperHubAuthorizationLedger(paths["authorizations"])
    try:
        existing = load_keeperhub_simulation_receipt(
            ledger,
            effect_sheet["simulation_approval_reference"],
        )
    except KeeperHubControlledExecutionError as error:
        if error.code not in {"SIMULATION_RECEIPT_NOT_FOUND", "SIMULATION_RECEIPT_NOT_FINAL"}:
            raise
        existing = None
    if existing is not None:
        return {
            "schema": _TOOL_SCHEMA,
            "status": "PASS",
            "decision": "ALREADY_SIMULATED_ELIGIBLE_FOR_BROADCAST",
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
            "simulation_posts": 0,
            "broadcast_posts": 0,
            "broadcast_approval_challenge": _broadcast_challenge(
                sheet["action_sheet_id"], selected, existing
            ),
            "retry_same_effect": False,
        }
    now = observed_at or _utc_now()
    transport = _CapturingTransferTransport(http_transport_factory(api_key))
    service = KeeperHubControlledSimulationService(
        transport,
        intent,
        SQLiteExecutionAttemptStore(paths["attempts"]),
        ledger,
    )
    authorization = KeeperHubSimulationAuthorization(
        action_sheet_id=sheet["action_sheet_id"],
        approval_reference=effect_sheet["simulation_approval_reference"],
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
            "effect_ref": selected,
            "reason": error.code,
            "simulation_posts": transport.calls,
            "broadcast_posts": 0,
            "provider_summary": _safe_provider_summary(transport.last_response),
            "retry_same_effect": False,
        }
    if receipt.decision is not KeeperHubSimulationDecision.ELIGIBLE_FOR_BROADCAST_APPROVAL:
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
            "reason": receipt.decision.value,
            "simulation_posts": transport.calls,
            "broadcast_posts": 0,
            "provider_summary": _safe_provider_summary(transport.last_response),
            "retry_same_effect": False,
        }
    return {
        "schema": _TOOL_SCHEMA,
        "status": "PASS",
        "decision": "ELIGIBLE_FOR_SEPARATE_BROADCAST_APPROVAL",
        "run_ref": sheet["run_ref"],
        "effect_ref": selected,
        "simulation_posts": transport.calls,
        "broadcast_posts": 0,
        "provider_summary": _safe_provider_summary(transport.last_response),
        "broadcast_approval_challenge": _broadcast_challenge(
            sheet["action_sheet_id"], selected, receipt
        ),
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
        effect = _effect_by_ref(current, effect_ref)
        if effect.state is EffectState.PLANNED:
            current = mission_store.transition_effect(
                mission_key,
                effect_ref,
                current.revision,
                EffectState.RESERVED,
                observed_at,
            )
            effect = _effect_by_ref(current, effect_ref)
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
        final_effect = _effect_by_ref(current, effect_ref)
        return (
            current.record.state is MissionState.VERIFYING
            and final_effect.state is EffectState.SUBMITTED
        )
    except (SQLiteMissionStoreError, AnnaMarkVideoMissionError):
        return False


def execute_broadcast(
    *,
    api_key: str,
    approval: str,
    run_ref: str,
    effect_ref: str,
    approve_testnet_write: bool,
    base_root: Path | None = None,
    observed_at: datetime | None = None,
    http_transport_factory: Callable[[str], Any] = KeeperHubHttpTransport,
) -> dict[str, Any]:
    if not approve_testnet_write:
        _fail("BROADCAST_RUNTIME_FLAG_REQUIRED")
    sheet, _, contexts, paths = _load_context(
        run_ref,
        effect_ref=effect_ref,
        base_root=base_root,
    )
    selected = _validate_effect_ref(effect_ref)
    mission_store = SQLiteMissionStore(paths["missions"])
    mission = mission_store.get(sheet["mission_key"])
    if mission is None:
        _fail("MISSION_NOT_FOUND")
    gate = _gate_dispatchable_effect(mission, selected)
    if gate == "SKIP_CONFIRMED":
        return {
            "schema": _TOOL_SCHEMA,
            "status": "PASS",
            "decision": "SKIP_ALREADY_CHAIN_CONFIRMED",
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
            "broadcast_posts": 0,
            "retry_same_effect": False,
        }
    intent, plan = contexts[selected]
    effect_sheet = sheet["effects"][selected]
    ledger = SQLiteKeeperHubAuthorizationLedger(paths["authorizations"])
    receipt = load_keeperhub_simulation_receipt(
        ledger,
        effect_sheet["simulation_approval_reference"],
    )
    expected_challenge = _broadcast_challenge(
        sheet["action_sheet_id"], selected, receipt
    )
    if approval != expected_challenge:
        _fail("BROADCAST_APPROVAL_MISMATCH")
    references = SQLiteProviderExecutionReferenceStore(paths["provider_references"])
    existing_reference = references.get(plan.attempt_id)
    attempt = SQLiteExecutionAttemptStore(paths["attempts"]).get(plan.attempt_id)
    if existing_reference is not None or (
        attempt is not None
        and attempt.record.state is not ExecutionAttemptState.PREPARED
    ):
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
            "reason": "RECONCILIATION_REQUIRED_NO_REBROADCAST",
            "broadcast_posts": 0,
            "provider_reference_present": existing_reference is not None,
            "retry_same_effect": False,
        }
    now = observed_at or _utc_now()
    transport = _CapturingTransferTransport(http_transport_factory(api_key))
    authorization = KeeperHubBroadcastAuthorization(
        action_sheet_id=sheet["action_sheet_id"],
        approval_reference=_broadcast_approval_reference(
            sheet["action_sheet_id"], selected, receipt
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
        intent,
        receipt,
        authorization,
        ledger,
    )
    wrapped = ProviderReferencePersistingPort(
        direct,
        references,
        provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
    )
    try:
        stored_attempt = ExecutionDispatchService(
            mission_store,
            SQLiteExecutionAttemptStore(paths["attempts"]),
        ).dispatch(plan, wrapped, now)
    except (
        ExecutionDispatchError,
        KeeperHubControlledExecutionError,
        SQLiteProviderExecutionReferenceStoreError,
    ) as error:
        try:
            reference = references.get(plan.attempt_id)
        except Exception:
            reference = None
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
            "reason": getattr(error, "code", "BROADCAST_OUTCOME_UNKNOWN"),
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
            "effect_ref": selected,
            "reason": "PROVIDER_ACK_PERSISTENCE_INCOMPLETE",
            "broadcast_posts": transport.calls,
            "provider_reference_present": reference is not None,
            "retry_same_effect": False,
        }
    advanced = _advance_after_ack(
        mission_store,
        plan.mission_key,
        selected,
        now,
    )
    return {
        "schema": _TOOL_SCHEMA,
        "status": "PASS" if advanced else "STOP",
        "decision": (
            "PROVIDER_ACKNOWLEDGED_REQUIRES_BINDING_AND_CHAIN_VERIFICATION"
            if advanced
            else "PROVIDER_ACKNOWLEDGED_LOCAL_RECONCILIATION_REQUIRED"
        ),
        "run_ref": sheet["run_ref"],
        "effect_ref": selected,
        "broadcast_posts": transport.calls,
        "provider_reference_present": True,
        "provider_reference": _provider_reference_fingerprint(
            reference.provider_reference
        ),
        "provider_summary": _safe_provider_summary(transport.last_response),
        "funds_movement": "UNKNOWN_PENDING_CHAIN_VERIFICATION",
        "retry_same_effect": False,
    }


def _binding_path(paths: Mapping[str, Path], effect_ref: str) -> Path:
    return paths["bindings"] / f"{_validate_effect_ref(effect_ref)}.json"


def _binding_material(
    *,
    sheet: Mapping[str, Any],
    effect_ref: str,
    reference: Any,
    transaction_hash: str,
    transaction_link: str | None,
    bound_at: datetime,
) -> dict[str, Any]:
    effect = sheet["effects"][effect_ref]
    return {
        "schema": _BINDING_SCHEMA,
        "run_ref": sheet["run_ref"],
        "mission_key": sheet["mission_key"],
        "effect_ref": effect_ref,
        "effect_id": effect["effect_id"],
        "attempt_id": effect["attempt_id"],
        "request_fingerprint": effect["request_fingerprint"],
        "provider_namespace": reference.provider_namespace,
        "provider_reference_fingerprint": _provider_reference_fingerprint(
            reference.provider_reference
        ),
        "provider_status": "completed",
        "transaction_hash": _validate_hash(transaction_hash),
        "transaction_link": transaction_link,
        "bound_at_utc": _timestamp(bound_at),
    }


def _load_binding(
    *,
    paths: Mapping[str, Path],
    sheet: Mapping[str, Any],
    effect_ref: str,
    reference: Any,
) -> dict[str, Any]:
    value = _read_json(
        _binding_path(paths, effect_ref),
        missing_code="PROVIDER_TRANSACTION_BINDING_NOT_FOUND",
        corrupt_code="PROVIDER_TRANSACTION_BINDING_INVALID",
    )
    required = {
        "schema",
        "run_ref",
        "mission_key",
        "effect_ref",
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
    if set(value.keys()) != required or value.get("schema") != _BINDING_SCHEMA:
        _fail("PROVIDER_TRANSACTION_BINDING_FIELD_MISMATCH")
    effect = sheet["effects"][effect_ref]
    expected = {
        "run_ref": sheet["run_ref"],
        "mission_key": sheet["mission_key"],
        "effect_ref": effect_ref,
        "effect_id": effect["effect_id"],
        "attempt_id": effect["attempt_id"],
        "request_fingerprint": effect["request_fingerprint"],
        "provider_namespace": reference.provider_namespace,
        "provider_reference_fingerprint": _provider_reference_fingerprint(
            reference.provider_reference
        ),
        "provider_status": "completed",
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            _fail("PROVIDER_TRANSACTION_BINDING_IDENTITY_MISMATCH")
    _validate_hash(value.get("transaction_hash"))
    if value.get("transaction_link") is not None and not isinstance(
        value.get("transaction_link"), str
    ):
        _fail("PROVIDER_TRANSACTION_BINDING_LINK_INVALID")
    if not isinstance(value.get("bound_at_utc"), str):
        _fail("PROVIDER_TRANSACTION_BINDING_TIMESTAMP_INVALID")
    return value


def capture_provider_binding(
    *,
    api_key: str,
    run_ref: str,
    effect_ref: str,
    base_root: Path | None = None,
    observed_at: datetime | None = None,
    http_transport_factory: Callable[[str], Any] = KeeperHubHttpTransport,
) -> dict[str, Any]:
    sheet, _, contexts, paths = _load_context(
        run_ref,
        effect_ref=effect_ref,
        base_root=base_root,
    )
    selected = _validate_effect_ref(effect_ref)
    _, plan = contexts[selected]
    references = SQLiteProviderExecutionReferenceStore(paths["provider_references"])
    reference = references.get(plan.attempt_id)
    if reference is None:
        _fail("PROVIDER_REFERENCE_NOT_FOUND")
    binding_path = _binding_path(paths, selected)
    if binding_path.exists():
        existing = _load_binding(
            paths=paths,
            sheet=sheet,
            effect_ref=selected,
            reference=reference,
        )
        return {
            "schema": _TOOL_SCHEMA,
            "status": "PASS",
            "decision": "ALREADY_BOUND",
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
            "status_gets": 0,
            "keeperhub_mutating_calls": 0,
            "broadcast_posts": 0,
            "transaction_hash": existing["transaction_hash"],
            "transaction_link": existing["transaction_link"],
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
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
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
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
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
    document = _binding_material(
        sheet=sheet,
        effect_ref=selected,
        reference=reference,
        transaction_hash=observation.transaction_hash,
        transaction_link=observation.transaction_link,
        bound_at=observed_at or _utc_now(),
    )
    _exclusive_json_write(
        binding_path,
        document,
        exists_code="PROVIDER_TRANSACTION_BINDING_ALREADY_EXISTS",
    )
    return {
        "schema": _TOOL_SCHEMA,
        "status": "PASS",
        "decision": "BOUND_TO_DURABLE_ATTEMPT",
        "run_ref": sheet["run_ref"],
        "effect_ref": selected,
        "provider_status": "completed",
        "status_gets": 1,
        "keeperhub_mutating_calls": 0,
        "broadcast_posts": 0,
        "transaction_hash": document["transaction_hash"],
        "transaction_link": document["transaction_link"],
        "provider_reference": document["provider_reference_fingerprint"],
        "binding_persisted": True,
        "retry_broadcast": False,
    }


def verify_effect_chain(
    *,
    run_ref: str,
    effect_ref: str,
    base_root: Path | None = None,
    wallet_path: Path | None = None,
    video_path: Path | None = None,
    observed_at: datetime | None = None,
    transport: Any | None = None,
) -> dict[str, Any]:
    sheet, _, contexts, paths = _load_context(
        run_ref,
        effect_ref=effect_ref,
        base_root=base_root,
    )
    selected = _validate_effect_ref(effect_ref)
    _, plan = contexts[selected]
    sender, recipients = _load_wallet_context(
        wallet_path=wallet_path,
        video_path=video_path,
    )
    if sender != sheet["expected_sender_address"]:
        _fail("EXPECTED_SENDER_BINDING_MISMATCH")
    if recipients[selected] != sheet["effects"][selected]["recipient_address"]:
        _fail("RECIPIENT_BINDING_MISMATCH")
    references = SQLiteProviderExecutionReferenceStore(paths["provider_references"])
    reference = references.get(plan.attempt_id)
    if reference is None:
        _fail("PROVIDER_REFERENCE_NOT_FOUND")
    binding = _load_binding(
        paths=paths,
        sheet=sheet,
        effect_ref=selected,
        reference=reference,
    )
    mission_store = SQLiteMissionStore(paths["missions"])
    attempt_store = SQLiteExecutionAttemptStore(paths["attempts"])
    current_attempt = attempt_store.get(plan.attempt_id)
    if current_attempt is None:
        _fail("ATTEMPT_NOT_FOUND")
    if current_attempt.record.state in {
        ExecutionAttemptState.FAILED_FINAL,
        ExecutionAttemptState.BLOCKED,
    }:
        _fail("ATTEMPT_TERMINAL_NOT_VERIFIABLE")
    selected_transport = transport or BaseSepoliaJsonRpcTransport()
    verifier = BaseSepoliaErc20TransferVerifier(
        selected_transport,
        transaction_hash=binding["transaction_hash"],
        expected_token_address=_BASE_SEPOLIA_USDC,
        expected_sender=sender,
        expected_recipient=recipients[selected],
        expected_amount_base_units=_AMOUNT_BASE_UNITS[selected],
    )
    try:
        result = ExecutionReconciliationService(
            mission_store,
            attempt_store,
        ).reconcile(
            attempt_id=plan.attempt_id,
            expected_sender=sender,
            minimum_confirmations=_MINIMUM_CONFIRMATIONS,
            verifier=verifier,
            observed_at_utc=observed_at or _utc_now(),
        )
    except ExecutionReconciliationError as error:
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": sheet["run_ref"],
            "effect_ref": selected,
            "reason": error.code,
            "rpc_calls": getattr(selected_transport, "calls", None),
            "keeperhub_calls": 0,
            "broadcast_posts": 0,
            "retry_broadcast": False,
        }
    observation = verifier.last_observation
    transfer = observation.transfer if observation is not None else None
    mission = result.mission
    current_effect = _effect_by_ref(mission, selected)
    confirmed = [
        ref
        for ref in _SEQUENCE
        if _effect_by_ref(mission, ref).state is EffectState.CHAIN_CONFIRMED
    ]
    payload = {
        "schema": _TOOL_SCHEMA,
        "status": (
            "PASS"
            if result.outcome is ReconciliationOutcome.VERIFIED
            else "WAIT"
            if result.outcome is ReconciliationOutcome.UNRESOLVED
            else "STOP"
        ),
        "outcome": result.outcome.value,
        "run_ref": sheet["run_ref"],
        "effect_ref": selected,
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "rpc_endpoint": BASE_SEPOLIA_RPC_URL,
        "rpc_calls": getattr(selected_transport, "calls", None),
        "keeperhub_calls": 0,
        "broadcast_posts": 0,
        "transaction_hash": binding["transaction_hash"],
        "transaction_link": f"https://sepolia.basescan.org/tx/{binding['transaction_hash']}",
        "sender_masked": _mask_address(sender),
        "recipient_masked": _mask_address(recipients[selected]),
        "amount_base_units": _AMOUNT_BASE_UNITS[selected],
        "confirmations": transfer.confirmations if transfer is not None else None,
        "attempt_state": result.attempt.record.state.value,
        "effect_state": current_effect.state.value,
        "mission_state": mission.record.state.value,
        "confirmed_effects": confirmed,
        "skipped_confirmed_effects": confirmed,
        "next_effect": _next_effect(mission),
        "evidence_fingerprint": result.evidence_fingerprint,
        "retry_read_only_verification": result.outcome is ReconciliationOutcome.UNRESOLVED,
        "retry_broadcast": False,
    }
    if result.outcome is ReconciliationOutcome.VERIFIED and selected == "anna":
        payload["decision"] = "ANNA_CONFIRMED_SKIP_FOREVER_MARK_NOW_ELIGIBLE"
    elif result.outcome is ReconciliationOutcome.VERIFIED and selected == "mark":
        payload["decision"] = "MISSION_COMPLETE_ALL_EFFECTS_CHAIN_CONFIRMED"
    return payload


def local_status(
    run_ref: str,
    *,
    base_root: Path | None = None,
) -> dict[str, Any]:
    sheet, _, contexts, paths = _load_context(run_ref, base_root=base_root)
    mission = SQLiteMissionStore(paths["missions"]).get(sheet["mission_key"])
    if mission is None:
        _fail("MISSION_NOT_FOUND")
    attempt_store = SQLiteExecutionAttemptStore(paths["attempts"])
    ledger = SQLiteKeeperHubAuthorizationLedger(paths["authorizations"])
    references = SQLiteProviderExecutionReferenceStore(paths["provider_references"])
    effects: list[dict[str, Any]] = []
    for effect_ref in _SEQUENCE:
        _, plan = contexts[effect_ref]
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
        effects.append(
            {
                "effect_ref": effect_ref,
                "state": _effect_by_ref(mission, effect_ref).state.value,
                "attempt_state": (
                    attempt.record.state.value if attempt is not None else "NOT_PREPARED"
                ),
                "simulation_authorization_state": (
                    simulation.state.value if simulation is not None else "NOT_CLAIMED"
                ),
                "broadcast_authorization_state": (
                    broadcast.state.value if broadcast is not None else "NOT_CLAIMED"
                ),
                "provider_reference_present": reference is not None,
                "provider_binding_present": _binding_path(paths, effect_ref).exists(),
                "recipient_masked": _mask_address(
                    sheet["effects"][effect_ref]["recipient_address"]
                ),
                "amount_base_units": _AMOUNT_BASE_UNITS[effect_ref],
            }
        )
    next_effect = _next_effect(mission)
    output: dict[str, Any] = {
        "schema": _TOOL_SCHEMA,
        "status": "LOCAL_STATUS",
        "run_ref": sheet["run_ref"],
        "mission_state": mission.record.state.value,
        "effects": effects,
        "next_effect": next_effect,
        "confirmed_effects": [
            effect["effect_ref"]
            for effect in effects
            if effect["state"] == EffectState.CHAIN_CONFIRMED.value
        ],
        "network_calls": 0,
        "retry_broadcast": False,
    }
    if next_effect is not None and _effect_by_ref(mission, next_effect).state is EffectState.PLANNED:
        output["simulation_approval_challenge"] = sheet["effects"][next_effect][
            "simulation_approval_challenge"
        ]
    return output


def _required_api_key() -> str:
    value = os.environ.get(_API_KEY_ENV)
    if not isinstance(value, str) or not value:
        _fail("LOCAL_API_KEY_NOT_SET")
    return value


def _emit(value: Mapping[str, Any]) -> int:
    print(json.dumps(dict(value), ensure_ascii=False, sort_keys=True))
    if value.get("status") in {"PASS", "PREPARED", "LOCAL_STATUS"}:
        return 0
    if value.get("status") == "WAIT":
        return 3
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Controlled Anna -> Mark KeeperHub video Mission on Base Sepolia."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "status"):
        item = sub.add_parser(command)
        item.add_argument("--run-ref", required=True)
    for command in ("simulate", "broadcast", "provider-bind", "verify"):
        item = sub.add_parser(command)
        item.add_argument("--run-ref", required=True)
        item.add_argument("--effect", required=True, choices=_SEQUENCE)
        if command in {"simulate", "broadcast"}:
            item.add_argument("--approval", required=True)
        if command == "broadcast":
            item.add_argument(_REQUIRED_BROADCAST_FLAG, action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "prepare":
            result = prepare_video_mission(args.run_ref)
        elif args.command == "status":
            result = local_status(args.run_ref)
        elif args.command == "simulate":
            result = execute_simulation(
                api_key=_required_api_key(),
                approval=args.approval,
                run_ref=args.run_ref,
                effect_ref=args.effect,
            )
        elif args.command == "broadcast":
            result = execute_broadcast(
                api_key=_required_api_key(),
                approval=args.approval,
                run_ref=args.run_ref,
                effect_ref=args.effect,
                approve_testnet_write=args.approve_testnet_write,
            )
        elif args.command == "provider-bind":
            result = capture_provider_binding(
                api_key=_required_api_key(),
                run_ref=args.run_ref,
                effect_ref=args.effect,
            )
        else:
            result = verify_effect_chain(
                run_ref=args.run_ref,
                effect_ref=args.effect,
            )
        return _emit(result)
    except (
        AnnaMarkVideoMissionError,
        BaseSepoliaVerificationError,
        KeeperHubControlledExecutionError,
        KeeperHubHttpTransportError,
        SQLiteProviderExecutionReferenceStoreError,
    ) as error:
        return _emit(
            {
                "schema": _TOOL_SCHEMA,
                "status": "STOP",
                "run_ref": getattr(args, "run_ref", None),
                "effect_ref": getattr(args, "effect", None),
                "reason": getattr(error, "code", "CONTROLLED_OPERATION_FAILED"),
                "retry_broadcast": False,
            }
        )
    except Exception:
        return _emit(
            {
                "schema": _TOOL_SCHEMA,
                "status": "STOP",
                "run_ref": getattr(args, "run_ref", None),
                "effect_ref": getattr(args, "effect", None),
                "reason": "UNEXPECTED_LOCAL_FAILURE",
                "retry_broadcast": False,
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
