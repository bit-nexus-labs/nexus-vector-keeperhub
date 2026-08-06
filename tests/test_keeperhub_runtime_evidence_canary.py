from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from nexus_vector.integrations.keeperhub_direct_execution import (
    KeeperHubTransportResponse,
)

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "keeperhub_runtime_evidence_canary.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_runtime_evidence_canary",
    TOOL_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("CANARY_TOOL_IMPORT_FAILED")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


SENDER = "0x" + "11" * 20
RECIPIENT = "0x" + "22" * 20
TOKEN = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


class ScriptedSimulationTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_transfer(self, body, *, idempotency_key):
        self.calls.append((dict(body), idempotency_key))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def simulation_ok():
    return KeeperHubTransportResponse(
        200,
        {
            "success": True,
            "status": "simulated",
            "wouldRevert": False,
            "gasEstimate": "65000",
        },
    )


class RuntimeEvidenceCanaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime_root = self.root / "runtime"
        self.registry = self.root / "wallets.private-local.json"
        self.registry.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at_utc": "2026-08-06T00:00:00Z",
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
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self):
        return MODULE.prepare(
            registry_path=self.registry,
            runtime_root=self.runtime_root,
        )

    def test_prepare_is_network_free_and_broadcast_incapable(self):
        result = self.prepare()

        self.assertEqual(result["status"], "PREPARED")
        self.assertEqual(result["mission_ref"], "simulation-canary-20260806-v1")
        self.assertEqual(result["effect_ref"], "provider-canary")
        self.assertEqual(result["amount"], "0.000001")
        self.assertEqual(result["network_calls_performed"], 0)
        self.assertEqual(result["maximum_simulation_posts"], 1)
        self.assertEqual(result["maximum_broadcast_posts"], 0)
        self.assertFalse(result["broadcast_authorized"])
        self.assertFalse(result["funds_moved"])
        self.assertTrue(
            (self.runtime_root / "canary.private-action-sheet.json").exists()
        )

    def test_success_is_exactly_one_simulation_post(self):
        prepared = self.prepare()
        transport = ScriptedSimulationTransport(simulation_ok())

        result = MODULE.execute(
            api_key="kh_test_key",
            approval=prepared["approval_challenge"],
            runtime_root=self.runtime_root,
            simulation_transport_factory=lambda _: transport,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            result["decision"],
            "ELIGIBLE_FOR_BROADCAST_APPROVAL",
        )
        self.assertEqual(result["simulation_posts"], 1)
        self.assertEqual(result["broadcast_posts"], 0)
        self.assertFalse(result["broadcast_authorized"])
        self.assertFalse(result["funds_moved"])
        self.assertEqual(result["provider_summary"]["http_status"], 200)
        self.assertEqual(result["provider_summary"]["gas_estimate"], "65000")
        self.assertEqual(len(transport.calls), 1)
        body, idempotency_key = transport.calls[0]
        self.assertIs(body["simulate"], True)
        self.assertEqual(body["chainId"], 84532)
        self.assertEqual(body["amount"], "0.000001")
        self.assertEqual(body["tokenAddress"], TOKEN.casefold())
        self.assertIsNone(idempotency_key)

    def test_success_is_terminal_across_restart(self):
        prepared = self.prepare()
        first = ScriptedSimulationTransport(simulation_ok())
        first_result = MODULE.execute(
            api_key="kh_test_key",
            approval=prepared["approval_challenge"],
            runtime_root=self.runtime_root,
            simulation_transport_factory=lambda _: first,
        )
        self.assertEqual(first_result["status"], "PASS")

        second = ScriptedSimulationTransport(simulation_ok())
        second_result = MODULE.execute(
            api_key="kh_test_key",
            approval=prepared["approval_challenge"],
            runtime_root=self.runtime_root,
            simulation_transport_factory=lambda _: second,
        )

        self.assertEqual(second_result["status"], "STOP")
        self.assertEqual(
            second_result["reason"],
            "AUTHORIZATION_ALREADY_CONSUMED",
        )
        self.assertEqual(second_result["simulation_posts"], 0)
        self.assertEqual(second.calls, [])
        self.assertEqual(second_result["broadcast_posts"], 0)

    def test_timeout_is_unknown_and_never_retried(self):
        prepared = self.prepare()
        first = ScriptedSimulationTransport(TimeoutError("timeout"))
        first_result = MODULE.execute(
            api_key="kh_test_key",
            approval=prepared["approval_challenge"],
            runtime_root=self.runtime_root,
            simulation_transport_factory=lambda _: first,
        )

        self.assertEqual(first_result["status"], "STOP")
        self.assertEqual(first_result["reason"], "SIMULATION_OUTCOME_UNKNOWN")
        self.assertEqual(first_result["authorization_state"], "OUTCOME_UNKNOWN")
        self.assertEqual(first_result["simulation_posts"], 1)
        self.assertEqual(first_result["retry"], "FORBIDDEN")

        second = ScriptedSimulationTransport(simulation_ok())
        second_result = MODULE.execute(
            api_key="kh_test_key",
            approval=prepared["approval_challenge"],
            runtime_root=self.runtime_root,
            simulation_transport_factory=lambda _: second,
        )
        self.assertEqual(second_result["simulation_posts"], 0)
        self.assertEqual(second.calls, [])
        self.assertEqual(
            second_result["reason"],
            "AUTHORIZATION_ALREADY_CONSUMED",
        )

    def test_wrong_approval_fails_before_transport_and_durable_claim(self):
        self.prepare()
        transport = ScriptedSimulationTransport(simulation_ok())

        with self.assertRaises(MODULE.RuntimeEvidenceCanaryError) as caught:
            MODULE.execute(
                api_key="kh_test_key",
                approval="WRONG",
                runtime_root=self.runtime_root,
                simulation_transport_factory=lambda _: transport,
            )

        self.assertEqual(caught.exception.code, "SIMULATION_APPROVAL_MISMATCH")
        self.assertEqual(transport.calls, [])

        status = MODULE.status(runtime_root=self.runtime_root)
        self.assertEqual(status["authorization_state"], "NOT_CLAIMED")
        self.assertEqual(status["network_calls_performed"], 0)

    def test_action_sheet_mutation_fails_closed(self):
        self.prepare()
        path = self.runtime_root / "canary.private-action-sheet.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["amount_base_units"] = 2
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(MODULE.RuntimeEvidenceCanaryError) as caught:
            MODULE.status(runtime_root=self.runtime_root)

        self.assertEqual(caught.exception.code, "ACTION_SHEET_BINDING_MISMATCH")

    def test_source_has_no_broadcast_operator_surface(self):
        source = TOOL_PATH.read_text(encoding="utf-8")
        self.assertNotIn("KeeperHubApprovedBroadcastPort", source)
        self.assertNotIn("--approve-testnet-write", source)
        self.assertNotIn('choices=("broadcast"', source)
        self.assertIn(
            'choices=("prepare", "execute", "status")',
            source,
        )
        self.assertIn("KeeperHubSimulationOnlyTransport", source)


if __name__ == "__main__":
    unittest.main()
