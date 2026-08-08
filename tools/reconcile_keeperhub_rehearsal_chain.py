"""Reconcile a KeeperHub rehearsal from independently observed Base Sepolia evidence.

The chain verifier never calls KeeperHub and never accepts a transaction hash
from the operator. It requires an immutable provider transaction binding that
was captured separately from the durable attempt/provider-reference identity.
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

from nexus_vector.application.execution_reconciliation import (  # noqa: E402
    ExecutionReconciliationError,
    ExecutionReconciliationService,
    ReconciliationOutcome,
)
from nexus_vector.domain.execution_attempts import ExecutionAttemptState  # noqa: E402
from nexus_vector.integrations.base_sepolia_rpc_verification import (  # noqa: E402
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_RPC_URL,
    BaseSepoliaErc20TransferVerifier,
    BaseSepoliaJsonRpcTransport,
    BaseSepoliaVerificationError,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (  # noqa: E402
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_mission_store import SQLiteMissionStore  # noqa: E402
from nexus_vector.persistence.sqlite_provider_execution_reference_store import (  # noqa: E402
    SQLiteProviderExecutionReferenceStore,
)

_TOOL_SCHEMA = "nexus-vector.base-sepolia-independent-verification.v1"
_REHEARSAL_SCHEMA = "nexus-vector.keeperhub-rehearsal-execution.v1"
_BINDING_SCHEMA = "nexus-vector.keeperhub-provider-transaction-binding.v1"
_BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e".lower()
_MINIMUM_CONFIRMATIONS = 2
_MAX_JSON_BYTES = 65_536
_RUN_REF_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_HASH_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")


class RehearsalChainReconciliationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RehearsalChainReconciliationError(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_run_ref(value: Any) -> str:
    if not isinstance(value, str) or _RUN_REF_PATTERN.fullmatch(value) is None:
        _fail("INVALID_RUN_REF")
    return value


def _validate_hash(value: Any) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        _fail("INVALID_TRANSACTION_HASH")
    return value.lower()


def _validate_address(value: Any, code: str) -> str:
    if not isinstance(value, str) or _EVM_ADDRESS.fullmatch(value) is None:
        _fail(code)
    return value.lower()


def _mask_address(value: str) -> str:
    checked = _validate_address(value, "INVALID_ADDRESS_FOR_MASKING")
    return f"{checked[:8]}…{checked[-6:]}"


def _provider_reference_fingerprint(value: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("INVALID_PROVIDER_REFERENCE")
    return "khref_sha256_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


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


def _rehearsal_root(run_ref: str, base_root: Path | None = None) -> Path:
    root = base_root or (
        Path.home() / ".nexus-vector" / "keeperhub-rehearsal-execution-v1"
    )
    return root / _validate_run_ref(run_ref)


def _wallet_registry_path(wallet_path: Path | None = None) -> Path:
    if wallet_path is not None:
        return wallet_path
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not isinstance(local_app_data, str) or not local_app_data:
        _fail("LOCALAPPDATA_NOT_AVAILABLE")
    return Path(local_app_data) / "NexusVector" / "Config" / "wallets.private-local.json"


def _load_wallet_context(wallet_path: Path | None = None) -> tuple[str, str]:
    document = _read_json(
        _wallet_registry_path(wallet_path),
        missing_code="WALLET_REGISTRY_NOT_FOUND",
        corrupt_code="WALLET_REGISTRY_INVALID",
    )
    if document.get("schema_version") != 1:
        _fail("WALLET_REGISTRY_SCHEMA_MISMATCH")
    network = document.get("network")
    wallets = document.get("wallets")
    safety = document.get("safety")
    if not isinstance(network, Mapping) or not isinstance(wallets, Mapping) or not isinstance(safety, Mapping):
        _fail("WALLET_REGISTRY_INVALID")
    if network.get("chain_id") != BASE_SEPOLIA_CHAIN_ID:
        _fail("WALLET_REGISTRY_CHAIN_MISMATCH")
    if network.get("environment") != "testnet":
        _fail("WALLET_REGISTRY_NOT_TESTNET")
    if safety.get("mainnet_blocked") is not True:
        _fail("MAINNET_BLOCK_NOT_CONFIRMED")
    sender = _validate_address(
        wallets.get("keeperhub_organization_wallet"),
        "INVALID_KEEPERHUB_ORGANIZATION_WALLET",
    )
    recipient = _validate_address(
        wallets.get("personal_recipient_wallet"),
        "INVALID_PERSONAL_RECIPIENT_WALLET",
    )
    if sender == recipient:
        _fail("SENDER_RECIPIENT_MUST_DIFFER")
    return sender, recipient


def _load_rehearsal_context(
    run_ref: str,
    *,
    base_root: Path | None = None,
):
    root = _rehearsal_root(run_ref, base_root)
    sheet = _read_json(
        root / "private_action_sheet.json",
        missing_code="ACTION_SHEET_NOT_FOUND",
        corrupt_code="ACTION_SHEET_INVALID",
    )
    if sheet.get("schema") != _REHEARSAL_SCHEMA:
        _fail("ACTION_SHEET_SCHEMA_MISMATCH")
    if sheet.get("run_ref") != run_ref:
        _fail("ACTION_SHEET_RUN_REF_MISMATCH")
    if sheet.get("chain_id") != BASE_SEPOLIA_CHAIN_ID:
        _fail("ACTION_SHEET_CHAIN_MISMATCH")
    token = _validate_address(sheet.get("token_address"), "INVALID_ACTION_SHEET_TOKEN")
    if token != _BASE_SEPOLIA_USDC:
        _fail("ACTION_SHEET_TOKEN_MISMATCH")
    if sheet.get("token_decimals") != 6 or sheet.get("amount_base_units") != 1:
        _fail("ACTION_SHEET_AMOUNT_MISMATCH")
    if sheet.get("maximum_broadcast_posts") != 1 or sheet.get("maximum_mutating_calls") != 1:
        _fail("ACTION_SHEET_BUDGET_MISMATCH")

    mission_key = sheet.get("mission_key")
    attempt_id = sheet.get("attempt_id")
    effect_id = sheet.get("effect_id")
    request_fingerprint = sheet.get("request_fingerprint")
    if not all(
        isinstance(value, str) and value
        for value in (mission_key, attempt_id, effect_id, request_fingerprint)
    ):
        _fail("ACTION_SHEET_IDENTITY_INVALID")

    mission_store = SQLiteMissionStore(root / "missions.sqlite3")
    attempt_store = SQLiteExecutionAttemptStore(root / "execution_attempts.sqlite3")
    mission = mission_store.get(mission_key)
    attempt = attempt_store.get(attempt_id)
    if mission is None:
        _fail("MISSION_NOT_FOUND")
    if attempt is None:
        _fail("ATTEMPT_NOT_FOUND")
    if attempt.record.plan.mission_key != mission_key or attempt.record.plan.effect_id != effect_id:
        _fail("ATTEMPT_IDENTITY_MISMATCH")
    effect = next(
        (item for item in mission.record.effects if item.effect_id == effect_id),
        None,
    )
    if effect is None:
        _fail("EFFECT_NOT_FOUND")
    recipient = _validate_address(sheet.get("recipient_address"), "INVALID_ACTION_SHEET_RECIPIENT")
    if (
        effect.chain_id != BASE_SEPOLIA_CHAIN_ID
        or effect.token_address != token
        or effect.recipient != recipient
        or effect.amount_base_units != 1
    ):
        _fail("DURABLE_EFFECT_MISMATCH")

    reference = SQLiteProviderExecutionReferenceStore(
        root / "provider_references.sqlite3"
    ).get(attempt_id)
    if reference is None:
        _fail("PROVIDER_REFERENCE_NOT_FOUND")
    if reference.request_fingerprint != request_fingerprint:
        _fail("PROVIDER_REFERENCE_REQUEST_MISMATCH")
    return root, sheet, mission_store, attempt_store, effect, reference


def _load_transaction_binding(
    *,
    root: Path,
    run_ref: str,
    sheet: Mapping[str, Any],
    reference: Any,
) -> tuple[str, str | None]:
    binding = _read_json(
        root / "provider_transaction_binding.json",
        missing_code="PROVIDER_TRANSACTION_BINDING_NOT_FOUND",
        corrupt_code="PROVIDER_TRANSACTION_BINDING_INVALID",
    )
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
    if set(binding.keys()) != required:
        _fail("PROVIDER_TRANSACTION_BINDING_FIELD_MISMATCH")
    if binding.get("schema") != _BINDING_SCHEMA:
        _fail("PROVIDER_TRANSACTION_BINDING_SCHEMA_MISMATCH")
    expected = {
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
    for field, value in expected.items():
        if binding.get(field) != value:
            _fail("PROVIDER_TRANSACTION_BINDING_IDENTITY_MISMATCH")
    transaction_hash = _validate_hash(binding.get("transaction_hash"))
    transaction_link = binding.get("transaction_link")
    if transaction_link is not None and not isinstance(transaction_link, str):
        _fail("PROVIDER_TRANSACTION_BINDING_LINK_INVALID")
    if not isinstance(binding.get("bound_at_utc"), str):
        _fail("PROVIDER_TRANSACTION_BINDING_TIMESTAMP_INVALID")
    return transaction_hash, transaction_link


def reconcile_rehearsal_chain(
    *,
    run_ref: str,
    base_root: Path | None = None,
    wallet_path: Path | None = None,
    observed_at: datetime | None = None,
    transport: Any | None = None,
) -> dict[str, Any]:
    checked_run_ref = _validate_run_ref(run_ref)
    root, sheet, mission_store, attempt_store, effect, reference = _load_rehearsal_context(
        checked_run_ref,
        base_root=base_root,
    )
    transaction_hash, transaction_link = _load_transaction_binding(
        root=root,
        run_ref=checked_run_ref,
        sheet=sheet,
        reference=reference,
    )
    expected_sender, registered_recipient = _load_wallet_context(wallet_path)
    if effect.recipient != registered_recipient:
        _fail("RECIPIENT_WALLET_BINDING_MISMATCH")

    attempt_id = sheet["attempt_id"]
    current_attempt = attempt_store.get(attempt_id)
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
        transaction_hash=transaction_hash,
        expected_token_address=effect.token_address,
        expected_sender=expected_sender,
        expected_recipient=effect.recipient,
        expected_amount_base_units=effect.amount_base_units,
    )
    try:
        result = ExecutionReconciliationService(
            mission_store,
            attempt_store,
        ).reconcile(
            attempt_id=attempt_id,
            expected_sender=expected_sender,
            minimum_confirmations=_MINIMUM_CONFIRMATIONS,
            verifier=verifier,
            observed_at_utc=observed_at or _utc_now(),
        )
    except ExecutionReconciliationError as error:
        return {
            "schema": _TOOL_SCHEMA,
            "status": "STOP",
            "run_ref": checked_run_ref,
            "reason": error.code,
            "transaction_hash": transaction_hash,
            "rpc_endpoint": BASE_SEPOLIA_RPC_URL,
            "rpc_calls": getattr(selected_transport, "calls", None),
            "keeperhub_calls": 0,
            "broadcast_posts": 0,
            "retry_broadcast": False,
        }

    observation = verifier.last_observation
    transfer = observation.transfer if observation is not None else None
    if result.outcome is ReconciliationOutcome.VERIFIED:
        return {
            "schema": _TOOL_SCHEMA,
            "status": "PASS",
            "outcome": result.outcome.value,
            "run_ref": checked_run_ref,
            "chain_id": BASE_SEPOLIA_CHAIN_ID,
            "rpc_endpoint": BASE_SEPOLIA_RPC_URL,
            "rpc_calls": getattr(selected_transport, "calls", None),
            "keeperhub_calls": 0,
            "broadcast_posts": 0,
            "provider_transaction_binding": True,
            "transaction_hash": transaction_hash,
            "transaction_link": transaction_link
            or f"https://sepolia.basescan.org/tx/{transaction_hash}",
            "token_address": effect.token_address,
            "sender_masked": _mask_address(expected_sender),
            "recipient_masked": _mask_address(effect.recipient),
            "amount_base_units": effect.amount_base_units,
            "confirmations": transfer.confirmations if transfer is not None else None,
            "evidence_fingerprint": result.evidence_fingerprint,
            "attempt_state": result.attempt.record.state.value,
            "effect_state": next(
                item.state.value
                for item in result.mission.record.effects
                if item.effect_id == effect.effect_id
            ),
            "mission_state": result.mission.record.state.value,
            "retry_broadcast": False,
        }

    current_effect = next(
        item
        for item in result.mission.record.effects
        if item.effect_id == effect.effect_id
    )
    return {
        "schema": _TOOL_SCHEMA,
        "status": "WAIT" if result.outcome is ReconciliationOutcome.UNRESOLVED else "STOP",
        "outcome": result.outcome.value,
        "run_ref": checked_run_ref,
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "rpc_endpoint": BASE_SEPOLIA_RPC_URL,
        "rpc_calls": getattr(selected_transport, "calls", None),
        "keeperhub_calls": 0,
        "broadcast_posts": 0,
        "provider_transaction_binding": True,
        "transaction_hash": transaction_hash,
        "observation_status": (
            observation.status.value if observation is not None else None
        ),
        "confirmations": transfer.confirmations if transfer is not None else None,
        "minimum_confirmations": _MINIMUM_CONFIRMATIONS,
        "attempt_state": result.attempt.record.state.value,
        "effect_state": current_effect.state.value,
        "mission_state": result.mission.record.state.value,
        "retry_read_only_verification": result.outcome is ReconciliationOutcome.UNRESOLVED,
        "retry_broadcast": False,
    }


def _emit(value: Mapping[str, Any]) -> int:
    print(json.dumps(dict(value), ensure_ascii=False, sort_keys=True))
    if value.get("status") == "PASS":
        return 0
    if value.get("status") == "WAIT":
        return 3
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Independently verify the durably bound KeeperHub rehearsal transaction "
            "on Base Sepolia and reconcile Nexus Vector state."
        )
    )
    parser.add_argument("--run-ref", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return _emit(reconcile_rehearsal_chain(run_ref=args.run_ref))
    except (RehearsalChainReconciliationError, BaseSepoliaVerificationError) as error:
        return _emit(
            {
                "schema": _TOOL_SCHEMA,
                "status": "STOP",
                "run_ref": getattr(args, "run_ref", None),
                "reason": error.code,
                "keeperhub_calls": 0,
                "broadcast_posts": 0,
                "retry_broadcast": False,
            }
        )
    except Exception:
        return _emit(
            {
                "schema": _TOOL_SCHEMA,
                "status": "STOP",
                "run_ref": getattr(args, "run_ref", None),
                "reason": "UNEXPECTED_LOCAL_FAILURE",
                "keeperhub_calls": 0,
                "broadcast_posts": 0,
                "retry_broadcast": False,
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
