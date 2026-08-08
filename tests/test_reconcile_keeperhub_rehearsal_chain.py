from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.domain.mission_models import EffectState, MissionState
from nexus_vector.integrations.base_sepolia_rpc_verification import (
    BASE_SEPOLIA_CHAIN_ID,
    ERC20_TRANSFER_TOPIC,
)
from nexus_vector.integrations.keeperhub_direct_execution import KeeperHubTransportResponse

ROOT = Path(__file__).parents[1]


def load_tool(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REHEARSAL = load_tool(
    "keeperhub_rehearsal_execution_for_chain_test",
    "tools/keeperhub_rehearsal_execution.py",
)
CHAIN = load_tool(
    "reconcile_keeperhub_rehearsal_chain",
    "tools/reconcile_keeperhub_rehearsal_chain.py",
)

T0 = datetime(2026, 8, 8, 20, 0, tzinfo=timezone.utc)
RUN_REF = "rehearsal-a-20260808-01"
SENDER = "0x" + "44" * 20
RECIPIENT = "0x" + "55" * 20
OTHER_RECIPIENT = "0x" + "66" * 20
TX_HASH = "0x" + "11" * 32
BLOCK_HASH = "0x" + "22" * 32
EXECUTION_ID = "direct_test_execution_01"
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e".lower()


def topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def uint256(value: int) -> str:
    return "0x" + f"{value:064x}"


def transfer_log(
    *,
    token=USDC,
    sender=SENDER,
    recipient=RECIPIENT,
    amount=1,
    log_index=0,
):
    return {
        "address": token,
        "topics": [
            ERC20_TRANSFER_TOPIC,
            topic_address(sender),
            topic_address(recipient),
        ],
        "data": uint256(amount),
        "transactionHash": TX_HASH,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x64",
        "logIndex": hex(log_index),
    }


def receipt(*logs):
    return {
        "transactionHash": TX_HASH,
        "status": "0x1",
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x64",
        "logs": list(logs),
    }


class FakeKeeperHubTransport:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def post_transfer(self, body, *, idempotency_key):
        self.calls += 1
        return self.response


class FakeRpc:
    def __init__(self, *, tx_receipt, latest=102, chain_id=BASE_SEPOLIA_CHAIN_ID):
        self.tx_receipt = tx_receipt
        self.latest = latest
        self.chain_id = chain_id
        self.calls = 0

    def call(self, method, params):
        self.calls += 1
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_getTransactionReceipt":
            return self.tx_receipt
        if method == "eth_blockNumber":
            return hex(self.latest)
        raise AssertionError(method)


class ExplodingRpc:
    def __init__(self):
        self.calls = 0

    def call(self, method, params):
        self.calls += 1
        raise AssertionError("RPC MUST NOT BE CALLED")


class RehearsalChainRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base_root = Path(self.temp.name) / "rehearsals"
        self.wallet_path = Path(self.temp.name) / "wallets.private-local.json"
        self.write_wallet_registry(SENDER, RECIPIENT)
        self.prepare_provider_acknowledged_rehearsal()

    def tearDown(self):
        self.temp.cleanup()

    def write_wallet_registry(self, sender, recipient, *, mainnet_blocked=True):
        self.wallet_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "network": {
                        "name": "Base Sepolia",
                        "chain_id": 84532,
                        "environment": "testnet",
                    },
                    "wallets": {
                        "keeperhub_organization_wallet": sender,
                        "personal_recipient_wallet": recipient,
                    },
                    "tokens": {"base_sepolia_usdc": {}},
                    "safety": {
                        "mainnet_blocked": mainnet_blocked,
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

    def prepare_provider_acknowledged_rehearsal(self):
        preview = REHEARSAL.prepare_action_sheet(
            RECIPIENT,
            RUN_REF,
            base_root=self.base_root,
            observed_at=T0,
        )
        simulation = REHEARSAL.execute_simulation(
            api_key="kh_test",
            approval=preview["simulation_approval_challenge"],
            run_ref=RUN_REF,
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=1),
            http_transport_factory=lambda key: FakeKeeperHubTransport(
                KeeperHubTransportResponse(
                    200,
                    {
                        "success": True,
                        "status": "simulated",
                        "wouldRevert": False,
                        "gasEstimate": "45415",
                    },
                )
            ),
        )
        self.assertEqual(simulation["status"], "PASS")
        broadcast = REHEARSAL.execute_broadcast(
            api_key="kh_test",
            approval=simulation["broadcast_approval_challenge"],
            run_ref=RUN_REF,
            approve_testnet_write=True,
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=2),
            http_transport_factory=lambda key: FakeKeeperHubTransport(
                KeeperHubTransportResponse(
                    202,
                    {"executionId": EXECUTION_ID, "status": "completed"},
                )
            ),
        )
        self.assertEqual(broadcast["status"], "PASS")

    def reconcile(self, rpc):
        return CHAIN.reconcile_rehearsal_chain(
            run_ref=RUN_REF,
            transaction_hash=TX_HASH,
            base_root=self.base_root,
            wallet_path=self.wallet_path,
            observed_at=T0 + timedelta(minutes=3),
            transport=rpc,
        )

    def test_exact_chain_evidence_completes_durable_mission(self):
        rpc = FakeRpc(tx_receipt=receipt(transfer_log()), latest=102)
        result = self.reconcile(rpc)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["outcome"], "VERIFIED")
        self.assertEqual(result["rpc_calls"], 3)
        self.assertEqual(result["keeperhub_calls"], 0)
        self.assertEqual(result["broadcast_posts"], 0)
        self.assertEqual(result["confirmations"], 3)
        self.assertEqual(result["amount_base_units"], 1)
        self.assertEqual(result["attempt_state"], "VERIFIED")
        self.assertEqual(result["effect_state"], "CHAIN_CONFIRMED")
        self.assertEqual(result["mission_state"], "COMPLETED")
        self.assertTrue(result["evidence_fingerprint"].startswith("evf_"))
        self.assertFalse(result["retry_broadcast"])
        self.assertNotIn(SENDER, json.dumps(result, sort_keys=True))
        self.assertNotIn(RECIPIENT, json.dumps(result, sort_keys=True))

    def test_repeat_after_verified_is_zero_rpc_and_no_state_change(self):
        first = self.reconcile(FakeRpc(tx_receipt=receipt(transfer_log())))
        self.assertEqual(first["status"], "PASS")

        second = self.reconcile(ExplodingRpc())
        self.assertEqual(second["status"], "PASS")
        self.assertEqual(second["outcome"], "VERIFIED")
        self.assertEqual(second["rpc_calls"], 0)
        self.assertEqual(second["keeperhub_calls"], 0)
        self.assertEqual(second["broadcast_posts"], 0)
        self.assertEqual(second["attempt_state"], "VERIFIED")
        self.assertEqual(second["effect_state"], "CHAIN_CONFIRMED")
        self.assertEqual(second["mission_state"], "COMPLETED")

    def test_sender_mismatch_blocks_attempt_and_requires_manual_review(self):
        wrong_sender = "0x" + "77" * 20
        self.write_wallet_registry(wrong_sender, RECIPIENT)
        result = self.reconcile(FakeRpc(tx_receipt=receipt(transfer_log())))

        self.assertEqual(result["status"], "STOP")
        self.assertEqual(result["outcome"], "BLOCKED")
        self.assertEqual(result["attempt_state"], "BLOCKED")
        self.assertEqual(result["effect_state"], "SUBMITTED")
        self.assertEqual(result["mission_state"], "MANUAL_REVIEW_REQUIRED")
        self.assertFalse(result["retry_broadcast"])

    def test_registered_recipient_mismatch_stops_before_rpc(self):
        self.write_wallet_registry(SENDER, OTHER_RECIPIENT)
        rpc = ExplodingRpc()
        with self.assertRaises(CHAIN.RehearsalChainReconciliationError) as caught:
            self.reconcile(rpc)
        self.assertEqual(caught.exception.code, "RECIPIENT_WALLET_BINDING_MISMATCH")
        self.assertEqual(rpc.calls, 0)

    def test_mainnet_block_must_be_true_before_rpc(self):
        self.write_wallet_registry(SENDER, RECIPIENT, mainnet_blocked=False)
        rpc = ExplodingRpc()
        with self.assertRaises(CHAIN.RehearsalChainReconciliationError) as caught:
            self.reconcile(rpc)
        self.assertEqual(caught.exception.code, "MAINNET_BLOCK_NOT_CONFIRMED")
        self.assertEqual(rpc.calls, 0)

    def test_low_confirmations_is_wait_and_never_rebroadcasts(self):
        result = self.reconcile(
            FakeRpc(tx_receipt=receipt(transfer_log()), latest=100)
        )
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["outcome"], "UNRESOLVED")
        self.assertEqual(result["confirmations"], 1)
        self.assertEqual(result["minimum_confirmations"], 2)
        self.assertEqual(result["attempt_state"], "EXECUTION_UNKNOWN")
        self.assertEqual(result["effect_state"], "SUBMITTED")
        self.assertEqual(result["mission_state"], "VERIFYING")
        self.assertTrue(result["retry_read_only_verification"])
        self.assertFalse(result["retry_broadcast"])

    def test_duplicate_exact_transfer_is_ambiguous_and_waits(self):
        result = self.reconcile(
            FakeRpc(
                tx_receipt=receipt(
                    transfer_log(log_index=0),
                    transfer_log(log_index=1),
                )
            )
        )
        self.assertEqual(result["status"], "WAIT")
        self.assertEqual(result["outcome"], "UNRESOLVED")
        self.assertEqual(result["observation_status"], "AMBIGUOUS")
        self.assertFalse(result["retry_broadcast"])

    def test_wrong_rpc_chain_becomes_unknown_without_broadcast_retry(self):
        result = self.reconcile(
            FakeRpc(
                tx_receipt=receipt(transfer_log()),
                chain_id=8453,
            )
        )
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(result["reason"], "VERIFICATION_OUTCOME_UNKNOWN")
        self.assertEqual(result["rpc_calls"], 1)
        self.assertEqual(result["keeperhub_calls"], 0)
        self.assertEqual(result["broadcast_posts"], 0)
        self.assertFalse(result["retry_broadcast"])

    def test_runner_source_has_no_keeperhub_api_key_or_write_path(self):
        source = (ROOT / "tools/reconcile_keeperhub_rehearsal_chain.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("KEEPERHUB_API_KEY", source)
        self.assertNotIn("post_transfer", source)
        self.assertNotIn("--approve-testnet-write", source)
        self.assertIn("keeperhub_calls\": 0", source)


if __name__ == "__main__":
    unittest.main()
