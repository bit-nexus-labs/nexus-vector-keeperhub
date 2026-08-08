from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from nexus_vector.integrations.keeperhub_direct_execution import KeeperHubTransportResponse

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "keeperhub_second_readonly_simulation.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_second_readonly_simulation",
    TOOL_PATH,
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("SECOND_READONLY_TOOL_IMPORT_FAILED")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
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
            "gasEstimate": "45415",
        },
    )


class SecondReadOnlySimulationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime_root = self.root / "runtime"
        self.registry = self.root / "wallets.private-local.json"
        self.preflight = self.root / "second-readonly-key-preflight-test.sanitized.json"

        self.registry.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at_utc": "2026-08-07T00:00:00Z",
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
        self.preflight.write_text(
            json.dumps(
                {
                    "broadcast_posts": 0,
                    "endpoint": "GET /api/keys",
                    "funds_moved": False,
                    "get_requests": 1,
                    "http_status": 200,
                    "organization_key_match": "MATCH",
                    "post_requests": 0,
                    "probe": "KEEPERHUB_KEY_IDENTITY_SURFACE_V1",
                    "reason": "ORGANIZATION_KEY_VISIBLE_TO_BACKEND",
                    "request_id_reflection": "NOT_PRESENT",
                    "response_surface": "APPLICATION_JSON",
                    "retry": "NOT_REQUIRED",
                    "simulation_posts": 0,
                    "status": "PASS",
                    "support_request_id": "nv-key-surface-test-001",
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
            preflight_evidence=self.preflight,
        )

    def execute(self, prepared, transport):
        return MODULE.execute(
            api_key="kh_second_readonly_test_key",
            approval=prepared["approval_challenge"],
            runtime_root=self.runtime_root,
            preflight_evidence=self.preflight,
            simulation_transport_factory=lambda _: transport,
        )

    def test_prepare_is_network_free_and_uses_new_identity(self):
        result = self.prepare()

        self.assertEqual(result["status"], "PREPARED")
        self.assertEqual(result["mission_ref"], "readonly-key2-validation-20260807-v1")
        self.assertEqual(result["effect_ref"], "readonly-key2-simulation-v1")
        self.assertEqual(result["amount"], "0.000001")
        self.assertEqual(result["network_calls_performed"], 0)
        self.assertEqual(result["maximum_simulation_posts"], 1)
        self.assertEqual(result["maximum_broadcast_posts"], 0)
        self.assertEqual(result["preflight_binding"], "MATCH")
        self.assertFalse(result["broadcast_authorized"])
        self.assertFalse(result["funds_moved"])

    def test_success_is_exactly_one_simulation_post(self):
        prepared = self.prepare()
        transport = ScriptedSimulationTransport(simulation_ok())
        result = self.execute(prepared, transport)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["decision"], "ELIGIBLE_FOR_BROADCAST_APPROVAL")
        self.assertEqual(result["simulation_posts"], 1)
        self.assertEqual(result["broadcast_posts"], 0)
        self.assertFalse(result["broadcast_authorized"])
        self.assertFalse(result["funds_moved"])
        self.assertEqual(result["claim_boundary"], "SIMULATION_ONLY_NOT_TRANSACTION_EVIDENCE")
        self.assertEqual(result["provider_summary"]["http_status"], 200)
        self.assertEqual(result["provider_summary"]["gas_estimate"], "45415")
        self.assertEqual(len(transport.calls), 1)
        body, idempotency_key = transport.calls[0]
        self.assertIs(body["simulate"], True)
        self.assertEqual(body["chainId"], 84532)
        self.assertEqual(body["amount"], "0.000001")
        self.assertEqual(body["tokenAddress"], TOKEN.casefold())
        self.assertIsNone(idempotency_key)

    def test_success_consumes_authorization_across_restart(self):
        prepared = self.prepare()
        first = ScriptedSimulationTransport(simulation_ok())
        first_result = self.execute(prepared, first)
        self.assertEqual(first_result["status"], "PASS")

        second = ScriptedSimulationTransport(simulation_ok())
        second_result = self.execute(prepared, second)
        self.assertEqual(second_result["status"], "STOP")
        self.assertEqual(second_result["reason"], "AUTHORIZATION_ALREADY_CONSUMED")
        self.assertEqual(second_result["simulation_posts"], 0)
        self.assertEqual(second.calls, [])

    def test_timeout_consumes_slot_and_blocks_retry(self):
        prepared = self.prepare()
        first = ScriptedSimulationTransport(TimeoutError("timeout"))
        first_result = self.execute(prepared, first)

        self.assertEqual(first_result["status"], "STOP")
        self.assertEqual(first_result["reason"], "SIMULATION_OUTCOME_UNKNOWN")
        self.assertEqual(first_result["authorization_state"], "OUTCOME_UNKNOWN")
        self.assertEqual(first_result["simulation_posts"], 1)
        self.assertEqual(first_result["retry"], "FORBIDDEN")

        second = ScriptedSimulationTransport(simulation_ok())
        second_result = self.execute(prepared, second)
        self.assertEqual(second_result["simulation_posts"], 0)
        self.assertEqual(second.calls, [])
        self.assertEqual(second_result["reason"], "AUTHORIZATION_ALREADY_CONSUMED")

    def test_changed_preflight_evidence_fails_before_transport(self):
        prepared = self.prepare()
        payload = json.loads(self.preflight.read_text(encoding="utf-8"))
        payload["support_request_id"] = "nv-key-surface-changed"
        self.preflight.write_text(json.dumps(payload), encoding="utf-8")
        transport = ScriptedSimulationTransport(simulation_ok())

        with self.assertRaises(MODULE.SecondReadOnlyValidationError) as caught:
            self.execute(prepared, transport)

        self.assertEqual(caught.exception.code, "PREFLIGHT_EVIDENCE_BINDING_MISMATCH")
        self.assertEqual(transport.calls, [])

    def test_failed_preflight_cannot_prepare(self):
        payload = json.loads(self.preflight.read_text(encoding="utf-8"))
        payload["status"] = "STOP"
        self.preflight.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(MODULE.SecondReadOnlyValidationError) as caught:
            self.prepare()

        self.assertEqual(caught.exception.code, "PREFLIGHT_EVIDENCE_NOT_PASS")

    def test_source_has_no_broadcast_surface_and_uses_canonical_transport(self):
        source = TOOL_PATH.read_text(encoding="utf-8")
        self.assertNotIn("KeeperHubApprovedBroadcastPort", source)
        self.assertNotIn("--approve-testnet-write", source)
        self.assertNotIn('choices=("broadcast"', source)
        self.assertIn('choices=("prepare", "execute", "status")', source)
        self.assertIn("KeeperHubSimulationOnlyTransport", source)
        self.assertIn("KeeperHubHttpTransport(api_key)", source)
        self.assertNotIn("simulation-canary-20260806-v1", source)
        self.assertNotIn('effect_ref="anna"', source)
        self.assertNotIn('effect_ref="mark"', source)


if __name__ == "__main__":
    unittest.main()
