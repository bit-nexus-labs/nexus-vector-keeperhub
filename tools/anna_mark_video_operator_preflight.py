"""Zero-network recovery preflight for the Anna/Mark operator wrapper.

This helper is intentionally narrower than the execution runner. It reads only
local durable state and decides whether an operator-facing simulation/broadcast
invocation may enter the lower-level runner. It never loads credentials and has
no network or signing capability.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexus_vector.domain.execution_attempts import ExecutionAttemptState  # noqa: E402
from nexus_vector.persistence.sqlite_execution_attempt_store import (  # noqa: E402
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_keeperhub_authorization_ledger import (  # noqa: E402
    KeeperHubAuthorizationPhase,
    KeeperHubAuthorizationState,
    SQLiteKeeperHubAuthorizationLedger,
)
from nexus_vector.persistence.sqlite_provider_execution_reference_store import (  # noqa: E402
    SQLiteProviderExecutionReferenceStore,
)

_SCHEMA = "nexus-vector.anna-mark-video-operator-preflight.v1"
_MISSION_SCHEMA = "nexus-vector.anna-mark-video-mission.v1"
_EFFECTS = ("anna", "mark")
_RUN_REF_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_MAX_BYTES = 131_072


class OperatorPreflightError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise OperatorPreflightError(code)


def _run_root(run_ref: str, base_root: Path | None = None) -> Path:
    if not isinstance(run_ref, str) or _RUN_REF_PATTERN.fullmatch(run_ref) is None:
        _fail("INVALID_RUN_REF")
    return (
        base_root
        or (Path.home() / ".nexus-vector" / "anna-mark-video-mission-v1")
    ) / run_ref


def _read_sheet(run_ref: str, base_root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    root = _run_root(run_ref, base_root)
    path = root / "private_action_sheet.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        _fail("ACTION_SHEET_NOT_FOUND")
    except OSError:
        _fail("ACTION_SHEET_INVALID")
    if len(raw) > _MAX_BYTES:
        _fail("ACTION_SHEET_INVALID")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("ACTION_SHEET_INVALID")
    if not isinstance(value, dict):
        _fail("ACTION_SHEET_INVALID")
    if value.get("schema") != _MISSION_SCHEMA or value.get("run_ref") != run_ref:
        _fail("ACTION_SHEET_IDENTITY_MISMATCH")
    effects = value.get("effects")
    if not isinstance(effects, Mapping) or set(effects) != set(_EFFECTS):
        _fail("ACTION_SHEET_EFFECT_SET_MISMATCH")
    return root, value


def _attempt_id(sheet: Mapping[str, Any], effect_ref: str) -> str:
    if effect_ref not in _EFFECTS:
        _fail("INVALID_EFFECT_REF")
    effect = sheet["effects"].get(effect_ref)
    if not isinstance(effect, Mapping):
        _fail("ACTION_SHEET_EFFECT_INVALID")
    attempt_id = effect.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        _fail("ACTION_SHEET_ATTEMPT_ID_INVALID")
    return attempt_id


def simulation_preflight(
    *,
    run_ref: str,
    effect_ref: str,
    base_root: Path | None = None,
) -> dict[str, Any]:
    root, sheet = _read_sheet(run_ref, base_root)
    attempt_id = _attempt_id(sheet, effect_ref)
    ledger = SQLiteKeeperHubAuthorizationLedger(root / "keeperhub_authorizations.sqlite3")
    record = ledger.get_for_attempt(KeeperHubAuthorizationPhase.SIMULATION, attempt_id)
    if record is None:
        return {
            "schema": _SCHEMA,
            "status": "PASS",
            "decision": "ALLOW_FIRST_SIMULATION",
            "run_ref": run_ref,
            "effect_ref": effect_ref,
            "network_calls": 0,
            "retry_mutating_call": False,
        }
    if record.state is KeeperHubAuthorizationState.ELIGIBLE_FOR_BROADCAST_APPROVAL:
        return {
            "schema": _SCHEMA,
            "status": "PASS",
            "decision": "ALLOW_DURABLE_SIMULATION_RECEIPT_READ",
            "run_ref": run_ref,
            "effect_ref": effect_ref,
            "network_calls": 0,
            "retry_mutating_call": False,
        }
    if record.state is KeeperHubAuthorizationState.REJECTED_FINAL:
        reason = "SIMULATION_REJECTED_FINAL_NO_RETRY"
    elif record.state in {
        KeeperHubAuthorizationState.CLAIMED,
        KeeperHubAuthorizationState.OUTCOME_UNKNOWN,
    }:
        reason = "SIMULATION_RECONCILIATION_REQUIRED_NO_RETRY"
    else:
        reason = "SIMULATION_STATE_NOT_OPERATOR_RETRYABLE"
    return {
        "schema": _SCHEMA,
        "status": "STOP",
        "reason": reason,
        "run_ref": run_ref,
        "effect_ref": effect_ref,
        "simulation_authorization_state": record.state.value,
        "network_calls": 0,
        "retry_mutating_call": False,
    }


def broadcast_preflight(
    *,
    run_ref: str,
    effect_ref: str,
    base_root: Path | None = None,
) -> dict[str, Any]:
    root, sheet = _read_sheet(run_ref, base_root)
    attempt_id = _attempt_id(sheet, effect_ref)
    ledger = SQLiteKeeperHubAuthorizationLedger(root / "keeperhub_authorizations.sqlite3")
    simulation = ledger.get_for_attempt(
        KeeperHubAuthorizationPhase.SIMULATION,
        attempt_id,
    )
    if (
        simulation is None
        or simulation.state
        is not KeeperHubAuthorizationState.ELIGIBLE_FOR_BROADCAST_APPROVAL
    ):
        return {
            "schema": _SCHEMA,
            "status": "STOP",
            "reason": "SIMULATION_NOT_DURABLY_ELIGIBLE",
            "run_ref": run_ref,
            "effect_ref": effect_ref,
            "simulation_authorization_state": (
                simulation.state.value if simulation is not None else "NOT_CLAIMED"
            ),
            "network_calls": 0,
            "retry_mutating_call": False,
        }

    broadcast = ledger.get_for_attempt(
        KeeperHubAuthorizationPhase.BROADCAST,
        attempt_id,
    )
    attempt = SQLiteExecutionAttemptStore(root / "execution_attempts.sqlite3").get(
        attempt_id
    )
    reference = SQLiteProviderExecutionReferenceStore(
        root / "provider_references.sqlite3"
    ).get(attempt_id)

    if broadcast is not None or reference is not None:
        reason = "BROADCAST_ALREADY_CONSUMED_RECONCILE_ONLY"
    elif attempt is None:
        reason = "ATTEMPT_NOT_PREPARED"
    elif attempt.record.state is not ExecutionAttemptState.PREPARED:
        reason = "ATTEMPT_NOT_BROADCASTABLE_RECONCILE_ONLY"
    else:
        return {
            "schema": _SCHEMA,
            "status": "PASS",
            "decision": "ALLOW_ONE_BROADCAST_ATTEMPT",
            "run_ref": run_ref,
            "effect_ref": effect_ref,
            "attempt_state": attempt.record.state.value,
            "network_calls": 0,
            "retry_mutating_call": False,
        }

    return {
        "schema": _SCHEMA,
        "status": "STOP",
        "reason": reason,
        "run_ref": run_ref,
        "effect_ref": effect_ref,
        "attempt_state": (
            attempt.record.state.value if attempt is not None else "NOT_FOUND"
        ),
        "broadcast_authorization_state": (
            broadcast.state.value if broadcast is not None else "NOT_CLAIMED"
        ),
        "provider_reference_present": reference is not None,
        "network_calls": 0,
        "retry_mutating_call": False,
    }


def _emit(value: Mapping[str, Any]) -> int:
    print(json.dumps(dict(value), ensure_ascii=False, sort_keys=True))
    return 0 if value.get("status") == "PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero-network Anna/Mark operator preflight.")
    parser.add_argument("command", choices=("simulate", "broadcast"))
    parser.add_argument("--run-ref", required=True)
    parser.add_argument("--effect", required=True, choices=_EFFECTS)
    args = parser.parse_args()
    try:
        if args.command == "simulate":
            result = simulation_preflight(
                run_ref=args.run_ref,
                effect_ref=args.effect,
            )
        else:
            result = broadcast_preflight(
                run_ref=args.run_ref,
                effect_ref=args.effect,
            )
        return _emit(result)
    except OperatorPreflightError as error:
        return _emit(
            {
                "schema": _SCHEMA,
                "status": "STOP",
                "reason": error.code,
                "run_ref": args.run_ref,
                "effect_ref": args.effect,
                "network_calls": 0,
                "retry_mutating_call": False,
            }
        )
    except Exception:
        return _emit(
            {
                "schema": _SCHEMA,
                "status": "STOP",
                "reason": "UNEXPECTED_LOCAL_PREFLIGHT_FAILURE",
                "run_ref": args.run_ref,
                "effect_ref": args.effect,
                "network_calls": 0,
                "retry_mutating_call": False,
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
