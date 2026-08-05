from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools" / "keeperhub_wallet_binding_preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_wallet_binding_preflight",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

SENDER = "0x" + "11" * 20
RECIPIENT = "0x" + "22" * 20
OTHER_WALLET = "0x" + "33" * 20
TOKEN = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
ORGANIZATION_ID = "org_private_identifier"


def valid_registry() -> dict:
    return {
        "schema_version": 1,
        "updated_at_utc": "2026-08-05T20:30:00.0000000Z",
        "network": {
            "name": "Base Sepolia",
            "chain_id": 84532,
            "environment": "testnet",
        },
        "wallets": {
            "keeperhub_organization_wallet": SENDER,
            "personal_recipient_wallet": RECIPIENT,
        },
        "tokens": {
            "base_sepolia_usdc": {
                "role": "TOKEN_CONTRACT_NOT_WALLET",
                "symbol": "USDC",
                "decimals": 6,
                "contract_address": TOKEN,
            }
        },
        "safety": {
            "mainnet_blocked": True,
            "contains_seed_phrase": False,
            "contains_wallet_private_key": False,
            "contains_turnkey_signing_key": False,
            "api_key_storage": "WINDOWS_DPAPI_CLIXML",
        },
    }


class FakeClient:
    def __init__(self, readiness):
        self.readiness = readiness
        self.calls = 0

    def get_wallet_readiness(self):
        self.calls += 1
        return self.readiness


class KeeperHubWalletBindingPreflightTests(unittest.TestCase):
    def test_valid_registry_and_live_wallet_match_pass_once_without_echo(self):
        binding = PROBE._parse_registry(valid_registry())
        client = FakeClient(
            PROBE.KeeperHubWalletReadiness(
                has_wallet=True,
                is_active=True,
                wallet_address=SENDER.casefold(),
                organization_id=ORGANIZATION_ID,
            )
        )

        exit_code, result = PROBE.run_preflight(client, binding)

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["reason"], "WALLET_BINDINGS_VERIFIED")
        self.assertEqual(result["keeperhub_wallet_binding"], "MATCH")
        self.assertEqual(result["get_requests"], 1)
        self.assertEqual(result["post_requests"], 0)
        self.assertEqual(result["simulation_posts"], 0)
        self.assertEqual(result["broadcast_posts"], 0)
        self.assertFalse(result["funds_moved"])

        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(SENDER.casefold(), serialized.casefold())
        self.assertNotIn(RECIPIENT.casefold(), serialized.casefold())
        self.assertNotIn(TOKEN.casefold(), serialized.casefold())
        self.assertNotIn(ORGANIZATION_ID, serialized)

    def test_live_wallet_mismatch_stops_after_one_get_without_echo(self):
        binding = PROBE._parse_registry(valid_registry())
        client = FakeClient(
            PROBE.KeeperHubWalletReadiness(
                has_wallet=True,
                is_active=True,
                wallet_address=OTHER_WALLET,
                organization_id=ORGANIZATION_ID,
            )
        )

        exit_code, result = PROBE.run_preflight(client, binding)

        self.assertEqual(exit_code, 2)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result["keeperhub_wallet_binding"], "MISMATCH")
        self.assertEqual(result["reason"], "KEEPERHUB_WALLET_BINDING_MISMATCH")
        self.assertEqual(result["retry"], "MANUAL_LOCAL_REVIEW_REQUIRED")
        serialized = json.dumps(result, sort_keys=True).casefold()
        self.assertNotIn(SENDER.casefold(), serialized)
        self.assertNotIn(OTHER_WALLET.casefold(), serialized)

    def test_not_ready_wallet_stops_without_claiming_binding(self):
        binding = PROBE._parse_registry(valid_registry())
        client = FakeClient(
            PROBE.KeeperHubWalletReadiness(
                has_wallet=False,
                is_active=False,
                wallet_address=None,
                organization_id=None,
            )
        )

        exit_code, result = PROBE.run_preflight(client, binding)

        self.assertEqual(exit_code, 2)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result["reason"], "KEEPERHUB_WALLET_NOT_READY")
        self.assertNotIn("keeperhub_wallet_binding", result)

    def test_registry_rejects_network_token_safety_collisions_and_unknown_fields(self):
        cases = []

        mainnet = valid_registry()
        mainnet["network"]["chain_id"] = 8453
        mainnet["network"]["name"] = "Base"
        mainnet["network"]["environment"] = "mainnet"
        cases.append((mainnet, "BASE_SEPOLIA_BINDING_REQUIRED"))

        wrong_token = valid_registry()
        wrong_token["tokens"]["base_sepolia_usdc"]["contract_address"] = OTHER_WALLET
        cases.append((wrong_token, "BASE_SEPOLIA_USDC_BINDING_MISMATCH"))

        mainnet_unblocked = valid_registry()
        mainnet_unblocked["safety"]["mainnet_blocked"] = False
        cases.append((mainnet_unblocked, "MAINNET_MUST_BE_BLOCKED"))

        same_sender_recipient = valid_registry()
        same_sender_recipient["wallets"]["personal_recipient_wallet"] = SENDER
        cases.append((same_sender_recipient, "SENDER_RECIPIENT_COLLISION"))

        recipient_is_token = valid_registry()
        recipient_is_token["wallets"]["personal_recipient_wallet"] = TOKEN
        cases.append((recipient_is_token, "RECIPIENT_TOKEN_CONTRACT_COLLISION"))

        unknown_field = valid_registry()
        unknown_field["private_note"] = "must not be accepted"
        cases.append((unknown_field, "INVALID_REGISTRY_SCHEMA"))

        for payload, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(PROBE.WalletBindingPreflightError) as caught:
                    PROBE._parse_registry(copy.deepcopy(payload))
                self.assertEqual(caught.exception.code, expected_code)

    def test_missing_registry_stops_before_transport_and_removes_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = {
                "LOCALAPPDATA": temporary,
                "KEEPERHUB_API_KEY": "kh_" + "A" * 32,
            }
            output = io.StringIO()
            with patch.dict(os.environ, environment, clear=True):
                with patch.object(
                    PROBE,
                    "KeeperHubHttpTransport",
                    side_effect=AssertionError("transport must not be constructed"),
                ):
                    with redirect_stdout(output):
                        exit_code = PROBE.main()
                self.assertNotIn("KEEPERHUB_API_KEY", os.environ)

        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["reason"], "LOCAL_WALLET_REGISTRY_NOT_FOUND")
        self.assertEqual(result["get_requests"], 0)
        self.assertEqual(result["requests"]["wallet"], "NOT_CALLED")

    def test_invalid_local_key_is_zero_network_and_not_echoed(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry_path = (
                Path(temporary)
                / "NexusVector"
                / "Config"
                / "wallets.private-local.json"
            )
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(
                json.dumps(valid_registry()),
                encoding="utf-8",
            )
            output = io.StringIO()
            environment = {
                "LOCALAPPDATA": temporary,
                "KEEPERHUB_API_KEY": "not-a-key",
            }
            with patch.dict(os.environ, environment, clear=True):
                with redirect_stdout(output):
                    exit_code = PROBE.main()
                self.assertNotIn("KEEPERHUB_API_KEY", os.environ)

        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["reason"], "LOCAL_API_KEY_FORMAT_INVALID")
        self.assertEqual(result["get_requests"], 0)
        self.assertNotIn("not-a-key", output.getvalue())

    def test_transport_accounting_distinguishes_local_and_network_failures(self):
        error = PROBE.KeeperHubHttpTransportError("NETWORK_OUTCOME_UNKNOWN")

        before_request = PROBE._transport_failure(error, request_attempted=False)
        after_request = PROBE._transport_failure(error, request_attempted=True)

        self.assertEqual(before_request["get_requests"], 0)
        self.assertEqual(before_request["status"], "STOP")
        self.assertEqual(before_request["retry"], "FORBIDDEN")
        self.assertEqual(after_request["get_requests"], 1)
        self.assertEqual(after_request["status"], "OUTCOME_UNKNOWN")
        self.assertEqual(after_request["retry"], "REVIEW_BEFORE_REPEAT")

    def test_readonly_client_and_source_have_no_broader_capabilities(self):
        public_methods = {
            name
            for name in PROBE.KeeperHubWalletBindingReadOnlyClient.__dict__
            if not name.startswith("_")
        }
        self.assertEqual(public_methods, {"get_wallet_readiness"})

        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("post_transfer(", source)
        self.assertNotIn("get_wallet_balances(", source)
        self.assertNotIn("list_chains(", source)
        self.assertNotIn("--approve-testnet-write", source)
        self.assertNotIn("transactionHash", source)


if __name__ == "__main__":
    unittest.main()
