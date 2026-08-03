from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from nexus_vector.integrations.keeperhub_direct_execution import (
    KeeperHubTransportResponse,
)

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools" / "keeperhub_one_shot_simulation.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_one_shot_simulation",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)

API_KEY = "kh_" + "a" * 32
RECIPIENT = "0x" + "12" * 20
FROM = "0x" + "34" * 20
TO = "0x" + "56" * 20


class FakeSimulationTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_transfer(self, body, *, idempotency_key):
        self.calls.append((dict(body), idempotency_key))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def factories(fake):
    return (lambda key: object()), (lambda http: fake)


class OneShotKeeperHubSimulationTests(unittest.TestCase):
    def test_prepare_is_network_free_private_and_fixed_to_minimal_usdc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = RUNNER.prepare_action_sheet(RECIPIENT, root)
            self.assertEqual(result["status"], "PREPARED")
            self.assertEqual(result["network_calls"], 0)
            self.assertEqual(result["chain_id"], 84532)
            self.assertEqual(result["amount"], "0.000001")
            self.assertEqual(result["maximum_simulation_posts"], 1)
            self.assertEqual(result["maximum_broadcast_posts"], 0)
            serialized = json.dumps(result, sort_keys=True)
            self.assertNotIn(RECIPIENT, serialized)
            sheet = json.loads(
                (root / "private_action_sheet.json").read_text()
            )
            self.assertEqual(sheet["recipient_address"], RECIPIENT)
            self.assertEqual(sheet["amount_base_units"], 1)
            self.assertEqual(sheet["token_decimals"], 6)

    def test_invalid_recipient_and_existing_sheet_fail_before_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(RUNNER.OneShotSimulationError) as invalid:
                RUNNER.prepare_action_sheet("0x1234", root)
            self.assertEqual(
                invalid.exception.code,
                "INVALID_RECIPIENT_ADDRESS",
            )
            RUNNER.prepare_action_sheet(RECIPIENT, root)
            with self.assertRaises(RUNNER.OneShotSimulationError) as duplicate:
                RUNNER.prepare_action_sheet(RECIPIENT, root)
            self.assertEqual(
                duplicate.exception.code,
                "ACTION_SHEET_ALREADY_EXISTS",
            )

    def test_success_is_one_post_without_idempotency_and_duplicate_is_blocked(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = RUNNER.prepare_action_sheet(RECIPIENT, root)
            response = KeeperHubTransportResponse(
                200,
                {
                    "success": True,
                    "status": "simulated",
                    "wouldRevert": False,
                    "from": FROM,
                    "to": TO,
                    "value": "0",
                    "gasEstimate": "65432",
                    "simulatedReturnValue": "0x",
                },
            )
            fake = FakeSimulationTransport(response)
            http_factory, simulation_factory = factories(fake)
            result = RUNNER.execute_action_sheet(
                api_key=API_KEY,
                approval=preview["approval_challenge"],
                state_root=root,
                http_transport_factory=http_factory,
                simulation_transport_factory=simulation_factory,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["simulation_posts"], 1)
            self.assertFalse(result["broadcast_authorized"])
            self.assertFalse(result["funds_moved"])
            self.assertEqual(len(fake.calls), 1)
            body, key = fake.calls[0]
            self.assertIs(body["simulate"], True)
            self.assertIsNone(key)
            serialized = json.dumps(result, sort_keys=True)
            self.assertNotIn(RECIPIENT, serialized)
            self.assertNotIn(FROM, serialized)
            self.assertNotIn(TO, serialized)
            self.assertNotIn(API_KEY, serialized)

            second_fake = FakeSimulationTransport(response)
            second_http, second_simulation = factories(second_fake)
            second = RUNNER.execute_action_sheet(
                api_key=API_KEY,
                approval=preview["approval_challenge"],
                state_root=root,
                http_transport_factory=second_http,
                simulation_transport_factory=second_simulation,
            )
            self.assertEqual(second["status"], "STOP")
            self.assertEqual(second["simulation_posts"], 0)
            self.assertEqual(second_fake.calls, [])

    def test_timeout_is_durable_unknown_and_never_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = RUNNER.prepare_action_sheet(RECIPIENT, root)
            fake = FakeSimulationTransport(
                RuntimeError("private transport detail")
            )
            http_factory, simulation_factory = factories(fake)
            result = RUNNER.execute_action_sheet(
                api_key=API_KEY,
                approval=preview["approval_challenge"],
                state_root=root,
                http_transport_factory=http_factory,
                simulation_transport_factory=simulation_factory,
            )
            self.assertEqual(result["status"], "STOP")
            self.assertEqual(result["reason"], "SIMULATION_OUTCOME_UNKNOWN")
            self.assertEqual(
                result["authorization_state"],
                "OUTCOME_UNKNOWN",
            )
            self.assertEqual(result["simulation_posts"], 1)
            self.assertNotIn("private transport detail", json.dumps(result))

            second_fake = FakeSimulationTransport(
                KeeperHubTransportResponse(
                    200,
                    {
                        "success": True,
                        "status": "simulated",
                        "wouldRevert": False,
                    },
                )
            )
            second_http, second_simulation = factories(second_fake)
            second = RUNNER.execute_action_sheet(
                api_key=API_KEY,
                approval=preview["approval_challenge"],
                state_root=root,
                http_transport_factory=second_http,
                simulation_transport_factory=second_simulation,
            )
            self.assertEqual(second["status"], "STOP")
            self.assertEqual(second["simulation_posts"], 0)
            self.assertEqual(second_fake.calls, [])

    def test_corrupt_sheet_and_wrong_approval_block_before_transport(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview = RUNNER.prepare_action_sheet(RECIPIENT, root)
            created = []

            def http_factory(key):
                created.append(key)
                return object()

            with self.assertRaises(RUNNER.OneShotSimulationError) as mismatch:
                RUNNER.execute_action_sheet(
                    api_key=API_KEY,
                    approval="wrong",
                    state_root=root,
                    http_transport_factory=http_factory,
                    simulation_transport_factory=lambda value: value,
                )
            self.assertEqual(
                mismatch.exception.code,
                "SIMULATION_APPROVAL_MISMATCH",
            )
            self.assertEqual(created, [])

            sheet_path = root / "private_action_sheet.json"
            sheet_path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(RUNNER.OneShotSimulationError) as corrupt:
                RUNNER.execute_action_sheet(
                    api_key=API_KEY,
                    approval=preview["approval_challenge"],
                    state_root=root,
                    http_transport_factory=http_factory,
                    simulation_transport_factory=lambda value: value,
                )
            self.assertEqual(corrupt.exception.code, "ACTION_SHEET_CORRUPT")
            self.assertEqual(created, [])

    def test_source_has_no_broadcast_command_or_broadcast_port(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            'choices=("prepare", "execute", "status")',
            source,
        )
        self.assertNotIn("KeeperHubApprovedBroadcastPort", source)
        self.assertNotIn("--approve-testnet-write", source)
        self.assertNotIn("idempotency_key=plan.request_key", source)


if __name__ == "__main__":
    unittest.main()
