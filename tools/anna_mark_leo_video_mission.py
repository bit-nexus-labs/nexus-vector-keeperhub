"""Controlled three-effect KeeperHub video Mission for Base Sepolia USDC.

This is a thin, safety-preserving specialization of the already-reviewed
Anna/Mark runner. It keeps the same durable state machine, authorization ledger,
provider-reference persistence, provider transaction binding, independent Base
Sepolia verification, and no-mutating-retry guarantees while extending the
ordered Mission to Anna -> Mark -> Leo.

Economic effects are fixed and immutable for this demo Mission:
- Anna: 0.25 USDC = 250000 base units
- Mark: 0.42 USDC = 420000 base units
- Leo:  0.37 USDC = 370000 base units
Total: 1.04 USDC.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_BASE_MODULE_PATH = Path(__file__).with_name("anna_mark_video_mission.py")
_BASE_MODULE_NAME = "_nexus_vector_anna_mark_base_for_anna_mark_leo_v1"
_spec = importlib.util.spec_from_file_location(_BASE_MODULE_NAME, _BASE_MODULE_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError("ANNA_MARK_BASE_MODULE_LOAD_FAILED")
base = importlib.util.module_from_spec(_spec)
sys.modules[_BASE_MODULE_NAME] = base
_spec.loader.exec_module(base)

_TOOL_SCHEMA = "nexus-vector.anna-mark-leo-video-mission.v1"
_BINDING_SCHEMA = "nexus-vector.anna-mark-leo-video-provider-binding.v1"
_PURPOSE = "ANNA_MARK_LEO_VIDEO_MISSION_TESTNET"
_MISSION_TYPE = "ANNA_MARK_LEO_VIDEO_TESTNET"
_SEQUENCE = ("anna", "mark", "leo")
_AMOUNT_BASE_UNITS = {
    "anna": 250_000,
    "mark": 420_000,
    "leo": 370_000,
}
_TOTAL_BASE_UNITS = sum(_AMOUNT_BASE_UNITS.values())
_VIDEO_RECIPIENT_SCHEMA = 1

# Patch only the isolated scenario module. The canonical Anna/Mark module in
# sys.modules is never imported or mutated by this runner.
base._TOOL_SCHEMA = _TOOL_SCHEMA
base._BINDING_SCHEMA = _BINDING_SCHEMA
base._PURPOSE = _PURPOSE
base._MISSION_TYPE = _MISSION_TYPE
base._SEQUENCE = _SEQUENCE
base._AMOUNT_BASE_UNITS = dict(_AMOUNT_BASE_UNITS)
base._VIDEO_RECIPIENT_SCHEMA = _VIDEO_RECIPIENT_SCHEMA


def _base_root() -> Path:
    return Path.home() / ".nexus-vector" / "anna-mark-leo-video-mission-v1"


def _video_recipients_path(video_path: Path | None = None) -> Path:
    if video_path is not None:
        return video_path
    local = os.environ.get("LOCALAPPDATA")
    if not isinstance(local, str) or not local:
        base._fail("LOCALAPPDATA_NOT_AVAILABLE")
    return (
        Path(local)
        / "NexusVector"
        / "Config"
        / "anna_mark_leo_video_recipients.private-local.json"
    )


def _load_wallet_context(
    *,
    wallet_path: Path | None = None,
    video_path: Path | None = None,
) -> tuple[str, dict[str, str]]:
    wallets_doc = base._read_json(
        base._wallet_registry_path(wallet_path),
        missing_code="WALLET_REGISTRY_NOT_FOUND",
        corrupt_code="WALLET_REGISTRY_INVALID",
    )
    if wallets_doc.get("schema_version") != 1:
        base._fail("WALLET_REGISTRY_SCHEMA_MISMATCH")
    network = wallets_doc.get("network")
    wallets = wallets_doc.get("wallets")
    safety = wallets_doc.get("safety")
    if not isinstance(network, Mapping) or not isinstance(wallets, Mapping) or not isinstance(safety, Mapping):
        base._fail("WALLET_REGISTRY_INVALID")
    if network.get("chain_id") != base.BASE_SEPOLIA_CHAIN_ID or network.get("environment") != "testnet":
        base._fail("WALLET_REGISTRY_NETWORK_MISMATCH")
    if safety.get("mainnet_blocked") is not True:
        base._fail("MAINNET_BLOCK_NOT_CONFIRMED")

    sender = base._validate_address(
        wallets.get("keeperhub_organization_wallet"),
        "INVALID_KEEPERHUB_ORGANIZATION_WALLET",
    )
    anna = base._validate_address(
        wallets.get("personal_recipient_wallet"),
        "INVALID_ANNA_RECIPIENT_WALLET",
    )

    video_doc = base._read_json(
        _video_recipients_path(video_path),
        missing_code="VIDEO_RECIPIENT_CONFIG_NOT_FOUND",
        corrupt_code="VIDEO_RECIPIENT_CONFIG_INVALID",
    )
    if video_doc.get("schema_version") != _VIDEO_RECIPIENT_SCHEMA:
        base._fail("VIDEO_RECIPIENT_CONFIG_SCHEMA_MISMATCH")
    vnetwork = video_doc.get("network")
    recipients = video_doc.get("recipients")
    vsafety = video_doc.get("safety")
    if not isinstance(vnetwork, Mapping) or not isinstance(recipients, Mapping) or not isinstance(vsafety, Mapping):
        base._fail("VIDEO_RECIPIENT_CONFIG_INVALID")
    if vnetwork.get("chain_id") != base.BASE_SEPOLIA_CHAIN_ID or vnetwork.get("environment") != "testnet":
        base._fail("VIDEO_RECIPIENT_CONFIG_NETWORK_MISMATCH")
    if vsafety.get("mainnet_blocked") is not True:
        base._fail("VIDEO_RECIPIENT_MAINNET_BLOCK_NOT_CONFIRMED")
    if vsafety.get("contains_seed_phrase") is not False:
        base._fail("VIDEO_RECIPIENT_CONFIG_SEED_FLAG_INVALID")
    if vsafety.get("contains_wallet_private_key") is not False:
        base._fail("VIDEO_RECIPIENT_CONFIG_PRIVATE_KEY_FLAG_INVALID")

    mark = base._validate_address(
        recipients.get("mark_recipient_wallet"),
        "INVALID_MARK_RECIPIENT_WALLET",
    )
    leo = base._validate_address(
        recipients.get("leo_recipient_wallet"),
        "INVALID_LEO_RECIPIENT_WALLET",
    )

    all_addresses = {sender, anna, mark, leo}
    if len(all_addresses) != 4:
        base._fail("VIDEO_WALLETS_MUST_BE_DISTINCT")

    return sender, {"anna": anna, "mark": mark, "leo": leo}


# Preserve the original validator, then add a second defense-in-depth check that
# includes Leo. The original implementation still verifies all deterministic
# identities, amounts, request fingerprints, approval challenges and action-sheet
# structure using the patched three-effect constants.
_original_validate_action_sheet = base._validate_action_sheet


def _validate_action_sheet(sheet: Mapping[str, Any]):
    request, contexts = _original_validate_action_sheet(sheet)
    sender = base._validate_address(
        sheet.get("expected_sender_address"),
        "INVALID_ACTION_SHEET_SENDER",
    )
    recipients = [effect.recipient for effect in request.effects]
    if len(recipients) != 3 or len(set(recipients)) != 3:
        base._fail("ACTION_SHEET_RECIPIENTS_MUST_BE_DISTINCT")
    if sender in set(recipients):
        base._fail("ACTION_SHEET_SENDER_RECIPIENT_CONFLICT")
    return request, contexts


def _rewrite_verified_decision(payload: dict[str, Any], effect_ref: str) -> dict[str, Any]:
    if payload.get("outcome") != "VERIFIED":
        return payload
    if effect_ref == "anna":
        payload["decision"] = "ANNA_CONFIRMED_SKIP_FOREVER_MARK_NOW_ELIGIBLE"
    elif effect_ref == "mark":
        if payload.get("next_effect") != "leo":
            base._fail("MARK_VERIFIED_BUT_LEO_NOT_NEXT")
        payload["decision"] = "MARK_CONFIRMED_SKIP_FOREVER_LEO_NOW_ELIGIBLE"
    elif effect_ref == "leo":
        if payload.get("mission_state") != "COMPLETED" or payload.get("next_effect") is not None:
            base._fail("LEO_VERIFIED_BUT_MISSION_NOT_COMPLETE")
        payload["decision"] = "MISSION_COMPLETE_ALL_EFFECTS_CHAIN_CONFIRMED"
    return payload


_original_verify_effect_chain = base.verify_effect_chain


def verify_effect_chain(*, run_ref: str, effect_ref: str, **kwargs: Any) -> dict[str, Any]:
    payload = _original_verify_effect_chain(
        run_ref=run_ref,
        effect_ref=effect_ref,
        **kwargs,
    )
    return _rewrite_verified_decision(payload, effect_ref)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Controlled Anna -> Mark -> Leo KeeperHub video Mission on Base Sepolia."
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
            item.add_argument(base._REQUIRED_BROADCAST_FLAG, action="store_true")
    return parser


# Install scenario-specific hooks only in the isolated module instance.
base._base_root = _base_root
base._video_recipients_path = _video_recipients_path
base._load_wallet_context = _load_wallet_context
base._validate_action_sheet = _validate_action_sheet
base.verify_effect_chain = verify_effect_chain
base._parser = _parser

# Re-export useful functions for focused tests and controlled local tooling.
prepare_video_mission = base.prepare_video_mission
execute_simulation = base.execute_simulation
execute_broadcast = base.execute_broadcast
capture_provider_binding = base.capture_provider_binding
local_status = base.local_status
AnnaMarkLeoVideoMissionError = base.AnnaMarkVideoMissionError


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
