from __future__ import annotations

import importlib.util
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools" / "keeperhub_readiness_probe.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_readiness_probe",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

WALLET = "0x" + "12" * 20
TOKEN = "0x" + "34" * 20


@dataclass(frozen=True)
class FakeReadiness:
    has_wallet: bool
    is_active: bool
    wallet_address: str | None

    @property
    def ready(self):
        return self.has_wallet and self.is_active and self.wallet_address is not None


@dataclass(frozen=True)
class FakeChain:
    chain_id: int
    name: str
    chain_type: str = "evm"
    is_testnet: bool = True
    is_enabled: bool = True

    @property
    def eligible_for_testnet_execution(self):
        return self.chain_type == "evm" and self.is_testnet and self.is_enabled


class FakeClient:
    def __init__(self, readiness, chains, balances):
        self.readiness = readiness
        self.chains = chains
        self.balances = balances
        self.calls = []

    def get_wallet_readiness(self):
        self.calls.append("wallet")
        return self.readiness

    def list_chains(self):
        self.calls.append("chains")
        return self.chains

    def get_wallet_balances(self):
        self.calls.append("balances")
        return self.balances


class KeeperHubReadinessProbeTests(unittest.TestCase):
    def test_pass_calls_each_approved_surface_once_and_sanitizes(self):
        client = FakeClient(
            FakeReadiness(True, True, WALLET),
            (
                FakeChain(84532, "Base Sepolia"),
                FakeChain(11155111, "Ethereum Sepolia"),
            ),
            {
                "organizationId": "org_sensitive",
                "email": "private@example.test",
                "balances": [
                    {
                        "chainId": 84532,
                        "symbol": "USDC",
                        "balance": "2.5",
                        "tokenAddress": TOKEN,
                        "walletAddress": WALLET,
                    }
                ],
            },
        )

        exit_code, result = PROBE.run_probe(client)

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(client.calls, ["wallet", "chains", "balances"])
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(WALLET, serialized)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn("org_sensitive", serialized)
        self.assertNotIn("private@example.test", serialized)
        self.assertIn("2.5", serialized)
        self.assertIn("USDC", serialized)
        self.assertEqual(
            result["wallet"]["wallet_address_masked"],
            f"{WALLET[:8]}…{WALLET[-6:]}",
        )

    def test_live_balance_shape_exposes_safe_funding_facts(self):
        payload = {
            "walletAddress": WALLET,
            "balances": [
                {
                    "chainId": 84532,
                    "chainName": "Base Sepolia",
                    "isTestnet": True,
                    "nativeBalance": "0.125",
                    "nativeBalanceRaw": "125000000000000000",
                    "supportedTokens": [
                        {
                            "name": "USD Coin",
                            "symbol": "USDC",
                            "decimals": 6,
                            "balance": "10.5",
                            "tokenAddress": TOKEN,
                            "privateId": "provider-private-id",
                        }
                    ],
                    "symbol": "BASE",
                    "tokens": [
                        {
                            "symbol": "USDC",
                            "balance": "10.5",
                            "tokenAddress": TOKEN,
                        }
                    ],
                }
            ],
        }

        sanitized = PROBE._sanitize_balance_payload(payload)
        chain = sanitized["balances"][0]

        self.assertTrue(chain["isTestnet"])
        self.assertEqual(chain["nativeBalance"], "0.125")
        self.assertEqual(chain["nativeBalanceRaw"], "125000000000000000")
        self.assertEqual(chain["symbol"], "BASE")
        self.assertEqual(chain["supportedTokens"][0]["symbol"], "USDC")
        self.assertEqual(chain["supportedTokens"][0]["balance"], "10.5")
        self.assertEqual(
            chain["supportedTokens"][0]["tokenAddress"],
            f"{TOKEN[:8]}…{TOKEN[-6:]}",
        )
        self.assertEqual(
            chain["supportedTokens"][0]["privateId"],
            "<redacted>",
        )
        self.assertEqual(sanitized["walletAddress"], "<redacted>")
        self.assertNotIn(WALLET, json.dumps(sanitized, sort_keys=True))
        self.assertNotIn(TOKEN, json.dumps(sanitized, sort_keys=True))
        self.assertNotIn("provider-private-id", json.dumps(sanitized, sort_keys=True))

    def test_unbounded_or_control_character_balance_text_is_redacted(self):
        sanitized = PROBE._sanitize_balance_payload(
            {
                "balances": [
                    {
                        "chainId": 84532,
                        "nativeBalance": "1" * 257,
                        "nativeBalanceRaw": "10\n20",
                        "symbol": " BASE ",
                    }
                ]
            }
        )
        chain = sanitized["balances"][0]
        self.assertEqual(chain["nativeBalance"], "<redacted>")
        self.assertEqual(chain["nativeBalanceRaw"], "<redacted>")
        self.assertEqual(chain["symbol"], "<redacted>")

    def test_wallet_not_ready_stops_before_other_calls(self):
        client = FakeClient(
            FakeReadiness(False, False, None),
            (FakeChain(84532, "Base Sepolia"),),
            {"balances": []},
        )

        exit_code, result = PROBE.run_probe(client)

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["reason"], "WALLET_NOT_READY")
        self.assertEqual(client.calls, ["wallet"])
        self.assertEqual(result["requests"]["chains"], "NOT_CALLED")
        self.assertEqual(result["requests"]["balances"], "NOT_CALLED")

    def test_missing_base_sepolia_stops_before_balance_call(self):
        client = FakeClient(
            FakeReadiness(True, True, WALLET),
            (FakeChain(11155111, "Ethereum Sepolia"),),
            {"balances": []},
        )

        exit_code, result = PROBE.run_probe(client)

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["reason"], "BASE_SEPOLIA_NOT_ELIGIBLE")
        self.assertEqual(client.calls, ["wallet", "chains"])
        self.assertEqual(result["requests"]["balances"], "NOT_CALLED")

    def test_missing_or_invalid_api_key_is_sanitized_and_not_retained(self):
        for environment in ({}, {"KEEPERHUB_API_KEY": "not-a-key"}):
            with self.subTest(environment=environment):
                output = io.StringIO()
                with patch.dict(os.environ, environment, clear=True):
                    with redirect_stdout(output):
                        exit_code = PROBE.main()
                    self.assertNotIn("KEEPERHUB_API_KEY", os.environ)
                payload = json.loads(output.getvalue())
                self.assertEqual(exit_code, 2)
                self.assertEqual(payload["status"], "STOP")
                self.assertNotIn("not-a-key", output.getvalue())

    def test_probe_source_has_no_simulation_or_broadcast_invocation(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("KeeperHubControlledSimulationService", source)
        self.assertNotIn("KeeperHubSimulationOnlyTransport", source)
        self.assertNotIn("--approve-testnet-write", source)
        self.assertNotIn("post_transfer(", source)


if __name__ == "__main__":
    unittest.main()
