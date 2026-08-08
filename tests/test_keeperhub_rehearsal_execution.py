from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.integrations.keeperhub_direct_execution import (
    KeeperHubTransportResponse,
)

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools" / "keeperhub_rehearsal_execution.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_rehearsal_execution",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)

T0 = datetime(2026, 8, 8, 18, 0, tzinfo=timezone.utc)
RUN_REF = "rehearsal-a-20260808-01"
RECIPIENT = "0x" + "12" * 20
API_KEY = "kh_" + "a" * 32
EXECUTION_ID = "direct_rehearsal_execution_00000001"
TX_HASH = "0x" + "ab" * 32
TX_LINK = f"https://sepolia.basescan.org/tx/{TX_HASH}"


class FakeHttp:
    def __init__(self, *, post_response=None, status_response=None):
        self.post_response = post_response
        self.status_response = status_response
        self.post_calls = []
        self.status_calls = []

    def post_transfer(self, body, *, idempotency_key):
        self.post_calls.append((dict(body), idempotency_key))
        if isinstance(self.post_response, BaseException):
            raise self.post_response
        if self.post_response is None:
            raise AssertionError("UNEXPECTED_POST")
        return self.post_response

    def get_execution_status(self, provider_reference):
        self.status_calls.append(provider_reference)
        if isinstance(self.status_response, BaseException):
            raise self.status_response
        if self.status_response is None:
            raise AssertionError("UNEXPECTED_STATUS_GET")
        return self.status_response


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


def broadcast_ok():
    return KeeperHubTransportResponse(
        202,
        {
            "executionId": EXECUTION_ID,
            "status": "pending",
        },
    )


def status_completed():
    return KeeperHubTransportResponse(
        200,
        {
            "executionId": EXECUTION_ID,
            "status": "completed",
            "transactionHash": TX_HASH,
            "transactionLink": TX_LINK,
        },
        {"X-Poll-Interval-Hint": "0"},
    )


class KeeperHubRehearsalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self):
        return RUNNER.prepare_action_sheet(
            RECIPIENT,
            RUN_REF,
            base_root=self.root,
            observed_at=T0,
        )

    def simulate(self, fake=None):
        preview = self.prepare()
        fake = fake or FakeHttp(post_response=simulation_ok())
        result = RUNNER.execute_simulation(
            api_key=API_KEY,
            approval=preview["simulation_approval_challenge"],
            run_ref=RUN_REF,
            base_root=self.root,
            observed_at=T0 + timedelta(minutes=1),
            http_transport_factory=lambda key: fake,
        )
        return preview, result, fake

    def test_prepare_is_network_free_and_persists_ready_mission(self):
        preview = self.prepare()
        self.assertEqual(preview["status"], "PREPARED")
        self.assertEqual(preview["network_calls"], 0)
        self.assertEqual(preview["chain_id"], 84532)
        self.assertEqual(preview["amount"], "0.000001")
        self.assertEqual(preview["maximum_simulation_posts"], 1)
        self.assertEqual(preview["maximum_broadcast_posts"], 1)
        self.assertEqual(preview["maximum_mutating_calls"], 1)
        self.assertFalse(preview["broadcast_authorized"])
        self.assertFalse(preview["mainnet_allowed"])
        self.assertNotIn(RECIPIENT, json.dumps(preview, sort_keys=True))

        private_sheet = json.loads(
            (
                self.root
                / RUN_REF
                / "private_action_sheet.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(private_sheet["recipient_address"], RECIPIENT)
        self.assertEqual(private_sheet["amount_base_units"], 1)
        self.assertEqual(private_sheet["same_key_recovery_posts_after_ambiguity"], 0)
        self.assertEqual(private_sheet["new_request_keys_after_ambiguity"], 0)

        local = RUNNER.local_status(RUN_REF, base_root=self.root)
        self.assertEqual(local["mission_state"], "READY_FOR_EXECUTION")
        self.assertEqual(local["effect_state"], "PLANNED")
        self.assertEqual(local["attempt_state"], "NOT_CREATED")
        self.assertFalse(local["provider_reference_present"])

    def test_prepare_rejects_invalid_ref_and_duplicate_without_network(self):
        with self.assertRaises(RUNNER.RehearsalExecutionError) as invalid:
            RUNNER.prepare_action_sheet(
                RECIPIENT,
                "../escape",
                base_root=self.root,
                observed_at=T0,
            )
        self.assertEqual(invalid.exception.code, "INVALID_RUN_REF")

        self.prepare()
        with self.assertRaises(RUNNER.RehearsalExecutionError) as duplicate:
            self.prepare()
        self.assertEqual(duplicate.exception.code, "ACTION_SHEET_ALREADY_EXISTS")

    def test_simulation_is_one_post_without_idempotency_and_separate_approval(self):
        preview, result, fake = self.simulate()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            result["decision"],
            "ELIGIBLE_FOR_SEPARATE_BROADCAST_APPROVAL",
        )
        self.assertEqual(result["simulation_posts"], 1)
        self.assertEqual(result["broadcast_posts"], 0)
        self.assertFalse(result["broadcast_authorized"])
        self.assertEqual(result["funds_movement"], "NONE_FROM_SIMULATION")
        self.assertTrue(result["broadcast_approval_challenge"].startswith("BROADCAST-"))
        self.assertEqual(len(fake.post_calls), 1)
        body, key = fake.post_calls[0]
        self.assertIs(body["simulate"], True)
        self.assertEqual(body["chainId"], 84532)
        self.assertEqual(body["amount"], "0.000001")
        self.assertIsNone(key)
        self.assertNotEqual(
            result["broadcast_approval_challenge"],
            preview["simulation_approval_challenge"],
        )

        local = RUNNER.local_status(RUN_REF, base_root=self.root)
        self.assertEqual(local["attempt_state"], "PREPARED")
        self.assertEqual(
            local["simulation_authorization_state"],
            "ELIGIBLE_FOR_BROADCAST_APPROVAL",
        )
        self.assertEqual(local["broadcast_authorization_state"], "NOT_CLAIMED")

    def test_duplicate_simulation_is_blocked_before_second_post(self):
        preview, result, _ = self.simulate()
        self.assertEqual(result["status"], "PASS")
        second = FakeHttp(post_response=simulation_ok())
        again = RUNNER.execute_simulation(
            api_key=API_KEY,
            approval=preview["simulation_approval_challenge"],
            run_ref=RUN_REF,
            base_root=self.root,
            observed_at=T0 + timedelta(minutes=2),
            http_transport_factory=lambda key: second,
        )
        self.assertEqual(again["status"], "STOP")
        self.assertEqual(second.post_calls, [])
        self.assertFalse(again["retry_same_effect"])

    def test_broadcast_requires_exact_flag_and_challenge_before_transport(self):
        _, simulation, _ = self.simulate()
        challenge = simulation["broadcast_approval_challenge"]

        with self.assertRaises(RUNNER.RehearsalExecutionError) as flag:
            RUNNER.execute_broadcast(
                api_key=API_KEY,
                approval=challenge,
                run_ref=RUN_REF,
                approve_testnet_write=False,
                base_root=self.root,
                observed_at=T0 + timedelta(minutes=2),
                http_transport_factory=lambda key: FakeHttp(
                    post_response=broadcast_ok()
                ),
            )
        self.assertEqual(flag.exception.code, "BROADCAST_RUNTIME_FLAG_REQUIRED")

        fake = FakeHttp(post_response=broadcast_ok())
        with self.assertRaises(RUNNER.RehearsalExecutionError) as approval:
            RUNNER.execute_broadcast(
                api_key=API_KEY,
                approval="BROADCAST-wrong",
                run_ref=RUN_REF,
                approve_testnet_write=True,
                base_root=self.root,
                observed_at=T0 + timedelta(minutes=2),
                http_transport_factory=lambda key: fake,
            )
        self.assertEqual(approval.exception.code, "BROADCAST_APPROVAL_MISMATCH")
        self.assertEqual(fake.post_calls, [])

    def test_broadcast_is_one_post_with_durable_reference_and_no_retry(self):
        _, simulation, _ = self.simulate()
        fake = FakeHttp(post_response=broadcast_ok())
        result = RUNNER.execute_broadcast(
            api_key=API_KEY,
            approval=simulation["broadcast_approval_challenge"],
            run_ref=RUN_REF,
            approve_testnet_write=True,
            base_root=self.root,
            observed_at=T0 + timedelta(minutes=2),
            http_transport_factory=lambda key: fake,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            result["decision"],
            "PROVIDER_ACKNOWLEDGED_REQUIRES_STATUS_AND_CHAIN_VERIFICATION",
        )
        self.assertEqual(result["broadcast_posts"], 1)
        self.assertTrue(result["provider_reference_present"])
        self.assertEqual(
            result["funds_movement"],
            "UNKNOWN_PENDING_CHAIN_VERIFICATION",
        )
        self.assertFalse(result["retry_same_effect"])
        self.assertEqual(len(fake.post_calls), 1)
        body, key = fake.post_calls[0]
        self.assertNotIn("simulate", body)
        self.assertEqual(body["chainId"], 84532)
        self.assertEqual(body["amount"], "0.000001")
        self.assertIsInstance(key, str)
        self.assertTrue(key.startswith("khreq_"))

        local = RUNNER.local_status(RUN_REF, base_root=self.root)
        self.assertEqual(local["attempt_state"], "PROVIDER_ACKNOWLEDGED")
        self.assertEqual(local["mission_state"], "VERIFYING")
        self.assertEqual(local["effect_state"], "SUBMITTED")
        self.assertEqual(local["broadcast_authorization_state"], "ACCEPTED")
        self.assertTrue(local["provider_reference_present"])

        second = FakeHttp(post_response=broadcast_ok())
        again = RUNNER.execute_broadcast(
            api_key=API_KEY,
            approval=simulation["broadcast_approval_challenge"],
            run_ref=RUN_REF,
            approve_testnet_write=True,
            base_root=self.root,
            observed_at=T0 + timedelta(minutes=3),
            http_transport_factory=lambda key: second,
        )
        self.assertEqual(again["status"], "STOP")
        self.assertEqual(second.post_calls, [])
        self.assertFalse(again["retry_same_effect"])

    def test_ambiguous_broadcast_is_terminal_for_same_effect(self):
        _, simulation, _ = self.simulate()
        first = FakeHttp(post_response=TimeoutError("private detail"))
        result = RUNNER.execute_broadcast(
            api_key=API_KEY,
            approval=simulation["broadcast_approval_challenge"],
            run_ref=RUN_REF,
            approve_testnet_write=True,
            base_root=self.root,
            observed_at=T0 + timedelta(minutes=2),
            http_transport_factory=lambda key: first,
        )
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(result["broadcast_posts"], 1)
        self.assertEqual(
            result["funds_movement"],
            "UNKNOWN_AFTER_BROADCAST_ATTEMPT",
        )
        self.assertNotIn("private detail", json.dumps(result))
        self.assertFalse(result["retry_same_effect"])

        second = FakeHttp(post_response=broadcast_ok())
        again = RUNNER.execute_broadcast(
            api_key=API_KEY,
            approval=simulation["broadcast_approval_challenge"],
            run_ref=RUN_REF,
            approve_testnet_write=True,
            base_root=self.root,
            observed_at=T0 + timedelta(minutes=3),
            http_transport_factory=lambda key: second,
        )
        self.assertEqual(again["status"], "STOP")
        self.assertEqual(second.post_calls, [])

    def test_completed_provider_status_remains_separate_from_chain_confirmation(self):
        _, simulation, _ = self.simulate()
        broadcast = FakeHttp(post_response=broadcast_ok())
        result = RUNNER.execute_broadcast(
            api_key=API_KEY,
            approval=simulation["broadcast_approval_challenge"],
            run_ref=RUN_REF,
            approve_testnet_write=True,
            base_root=self.root,
            observed_at=T0 + timedelta(minutes=2),
            http_transport_factory=lambda key: broadcast,
        )
        self.assertEqual(result["status"], "PASS")

        status = FakeHttp(status_response=status_completed())
        observation = RUNNER.observe_provider_status(
            api_key=API_KEY,
            run_ref=RUN_REF,
            base_root=self.root,
            http_transport_factory=lambda key: status,
        )
        self.assertEqual(observation["status"], "PASS")
        self.assertEqual(observation["provider_status"], "completed")
        self.assertTrue(observation["terminal"])
        self.assertTrue(observation["requires_independent_chain_verification"])
        self.assertEqual(observation["transaction_hash"], TX_HASH)
        self.assertEqual(observation["transaction_link"], TX_LINK)
        self.assertEqual(len(status.status_calls), 1)

        local = RUNNER.local_status(RUN_REF, base_root=self.root)
        self.assertEqual(local["mission_state"], "VERIFYING")
        self.assertEqual(local["effect_state"], "SUBMITTED")

    def test_source_has_explicit_testnet_write_gate_and_no_mainnet_path(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('"--approve-testnet-write"', source)
        self.assertIn("_BASE_SEPOLIA_CHAIN_ID = 84532", source)
        self.assertIn("_AMOUNT_BASE_UNITS = 1", source)
        self.assertNotIn("8453\n", source)
        self.assertNotIn("11155111", source)
        self.assertNotIn("maximum_broadcast_posts\": 2", source)


if __name__ == "__main__":
    unittest.main()
