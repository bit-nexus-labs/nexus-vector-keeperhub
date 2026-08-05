#!/usr/bin/env python3
"""Prepare durable testnet evidence plans without provider network access."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus_vector.application.runtime_evidence_plan import (
    RuntimeEvidencePlanError,
    admit_and_prepare_mission,
    build_flagship_mission_request,
    build_simulation_canary_request,
    sanitized_mission_snapshot,
    sanitized_selection_snapshot,
    select_flagship_effect,
    select_simulation_canary,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_mission_store import SQLiteMissionStore

_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")


class RuntimeEvidenceCliError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RuntimeEvidenceCliError(code)


def _default_local_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        _fail("LOCALAPPDATA_NOT_AVAILABLE")
    return Path(local_app_data) / "NexusVector"


def _default_wallet_registry() -> Path:
    return _default_local_root() / "Config" / "wallets.private-local.json"


def _default_runtime_root() -> Path:
    return _default_local_root() / "RuntimeEvidence"


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
    safety = payload.get("safety")
    if (
        not isinstance(network, dict)
        or network.get("chain_id") != 84532
        or network.get("environment") != "testnet"
        or not isinstance(wallets, dict)
        or not isinstance(safety, dict)
        or safety.get("mainnet_blocked") is not True
    ):
        _fail("LOCAL_WALLET_REGISTRY_BINDING_INVALID")
    recipient = wallets.get("personal_recipient_wallet")
    if (
        not isinstance(recipient, str)
        or _ADDRESS_PATTERN.fullmatch(recipient) is None
    ):
        _fail("LOCAL_RECIPIENT_INVALID")
    return recipient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare or inspect the network-free Nexus Vector runtime "
            "evidence plan. This tool has no KeeperHub transport."
        )
    )
    parser.add_argument(
        "--operation",
        choices=("prepare", "select"),
        required=True,
    )
    parser.add_argument(
        "--mission",
        choices=("flagship", "canary"),
        required=True,
    )
    parser.add_argument(
        "--effect",
        choices=("anna", "mark"),
    )
    parser.add_argument("--wallet-registry", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    return parser


def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    os.environ.pop("KEEPERHUB_API_KEY", None)

    registry = arguments.wallet_registry or _default_wallet_registry()
    runtime_root = arguments.runtime_root or _default_runtime_root()
    recipient = _load_recipient(registry)

    runtime_root.mkdir(parents=True, exist_ok=True)
    mission_store = SQLiteMissionStore(runtime_root / "missions.sqlite3")
    attempt_store = SQLiteExecutionAttemptStore(
        runtime_root / "execution-attempts.sqlite3"
    )
    attempt_store.initialize()

    if arguments.mission == "flagship":
        request = build_flagship_mission_request(recipient)
    else:
        request = build_simulation_canary_request(recipient)

    now = datetime.now(timezone.utc)
    if arguments.operation == "prepare":
        mission = admit_and_prepare_mission(request, mission_store, now)
        return sanitized_mission_snapshot(mission, attempt_store)

    identity = request.build_identity()
    mission_store.initialize()
    mission = mission_store.get(identity.mission_key)
    if mission is None:
        _fail("MISSION_NOT_PREPARED")

    if arguments.mission == "canary":
        if arguments.effect is not None:
            _fail("CANARY_EFFECT_ARGUMENT_FORBIDDEN")
        selection = select_simulation_canary(mission, attempt_store)
    else:
        if arguments.effect is None:
            _fail("FLAGSHIP_EFFECT_REQUIRED")
        selection = select_flagship_effect(
            mission,
            attempt_store,
            arguments.effect,
        )
    return sanitized_selection_snapshot(selection)


def main() -> int:
    try:
        result = _run(_parser().parse_args())
    except (RuntimeEvidenceCliError, RuntimeEvidencePlanError) as error:
        print(
            json.dumps(
                {
                    "status": "STOP",
                    "reason": error.code,
                    "network_calls_performed": 0,
                    "funds_moved": False,
                },
                sort_keys=True,
            )
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "status": "STOP",
                    "reason": "LOCAL_PLANNING_FAILURE",
                    "network_calls_performed": 0,
                    "funds_moved": False,
                },
                sort_keys=True,
            )
        )
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
