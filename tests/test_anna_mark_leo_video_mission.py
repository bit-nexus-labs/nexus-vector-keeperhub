from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.integrations.base_sepolia_rpc_verification import (
    BASE_SEPOLIA_CHAIN_ID,
    ERC20_TRANSFER_TOPIC,
)
from nexus_vector.integrations.keeperhub_direct_execution import KeeperHubTransportResponse

ROOT = Path(__file__).parents[1]


def load_tool():
    path = ROOT / "tools" / "anna_mark_leo_video_mission.py"
    spec = importlib.util.spec_from_file_location("anna_mark_leo_video_mission_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VIDEO = load_tool()
T0 = datetime(2026, 8, 9, 1, 0, tzinfo=timezone.utc)
RUN_REF = "anna-mark-leo-video-20260809-v1"
SENDER = "0x" + "11" * 20
ANNA = "0x" + "22" * 20
MARK = "0x" + "33" * 20
LEO = "0x" + "44" * 20
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e".lower()


def topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def uint256(value: int) -> str:
    return "0x" + f"{value:064x}"


def receipt(*, tx_hash: str, block_hash: str, recipient: str, amount: int):
    return {
        "transactionHash": tx_hash,
        "status": "0x1",
        "blockHash": block_hash,
        "blockNumber": "0x64",
        "logs": [
            {
                "address": USDC,
                "topics": [
                    ERC20_TRANSFER_TOPIC,
                    topic_address(SENDER),
                    topic_address(recipient),
                ],
                "data": uint256(amount),
                "transactionHash": tx_hash,
                "blockHash": block_hash,
                "blockNumber": "0x64",
                "logIndex": "0x0",
            }
        ],
    }


class FakeTransferTransport:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def post_transfer(self, body, *, idempotency_key):
        self.calls += 1
        return self.response


class FakeStatusTransport:
    def __init__(self, execution_id: str, tx_hash: str):
        self.execution_id = execution_id
        self.tx_hash = tx_hash
        self.calls = 0

    def get_execution_status(self, provider_reference):
        self.calls += 1
        if provider_reference != self.execution_id:
            raise AssertionError("provider reference mismatch")
        return KeeperHubTransportResponse(
            200,
            {
                "executionId": self.execution_id,
                "status": "completed",
                "transactionHash": self.tx_hash,
                "transactionLink": f"https://sepolia.basescan.org/tx/{self.tx_hash}",
            },
            headers={"X-Poll-Interval-Hint": "0"},
        )


class FakeRpc:
    def __init__(self, tx_receipt, *, latest=102):
        self.tx_receipt = tx_receipt
        self.latest = latest
        self.calls = 0

    def call(self, method, params):
        self.calls += 1
        if method == "eth_chainId":
            return hex(BASE_SEPOLIA_CHAIN_ID)
        if method == "eth_getTransactionReceipt":
            return self.tx_receipt
        if method == "eth_blockNumber":
            return hex(self.latest)
        raise AssertionError(method)


class AnnaMarkLeoVideoMissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        temp = Path(self.temp.name)
        self.base_root = temp / "missions"
        self.wallet_path = temp / "wallets.private-local.json"
        self.video_path = temp / "anna_mark_leo_video_recipients.private-local.json"
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
                        "keeperhub_organization_wallet": SENDER,
                        "personal_recipient_wallet": ANNA,
                    },
                    "tokens": {"base_sepolia_usdc": {}},
                    "safety": {"mainnet_blocked": True},
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.write_recipients()

    def tearDown(self):
        self.temp.cleanup()

    def write_recipients(self, *, mark=MARK, leo=LEO, mainnet_blocked=True):
        self.video_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at_utc": "2026-08-09T01:00:00.000000Z",
                    "network": {
                        "name": "Base Sepolia",
                        "chain_id": 84532,
                        "environment": "testnet",
                    },
                    "recipients": {
                        "mark_recipient_wallet": mark,
                        "leo_recipient_wallet": leo,
                    },
                    "safety": {
                        "mainnet_blocked": mainnet_blocked,
                        "contains_seed_phrase": False,
                        "contains_wallet_private_key": False,
                        "contains_turnkey_signing_key": False,
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def prepare(self):
        return VIDEO.prepare_video_mission(
            RUN_REF,
            base_root=self.base_root,
            wallet_path=self.wallet_path,
            video_path=self.video_path,
            observed_at=T0,
        )

    def status(self):
        return VIDEO.local_status(RUN_REF, base_root=self.base_root)

    def full_effect(self, effect, execution_id, tx_hash, block_hash, recipient, amount, minute):
        challenge = self.status()["simulation_approval_challenge"]
        simulation = VIDEO.execute_simulation(
            api_key="kh_test",
            approval=challenge,
            run_ref=RUN_REF,
            effect_ref=effect,
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=minute),
            http_transport_factory=lambda key: FakeTransferTransport(
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
        self.assertEqual(simulation["simulation_posts"], 1)
        self.assertEqual(simulation["broadcast_posts"], 0)

        broadcast = VIDEO.execute_broadcast(
            api_key="kh_test",
            approval=simulation["broadcast_approval_challenge"],
            run_ref=RUN_REF,
            effect_ref=effect,
            approve_testnet_write=True,
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=minute + 1),
            http_transport_factory=lambda key: FakeTransferTransport(
                KeeperHubTransportResponse(
                    202,
                    {"executionId": execution_id, "status": "completed"},
                )
            ),
        )
        self.assertEqual(broadcast["status"], "PASS")
        self.assertEqual(broadcast["broadcast_posts"], 1)

        binding = VIDEO.capture_provider_binding(
            api_key="kh_test",
            run_ref=RUN_REF,
            effect_ref=effect,
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=minute + 2),
            http_transport_factory=lambda key: FakeStatusTransport(execution_id, tx_hash),
        )
        self.assertEqual(binding["status"], "PASS")
        self.assertEqual(binding["keeperhub_mutating_calls"], 0)

        return VIDEO.verify_effect_chain(
            run_ref=RUN_REF,
            effect_ref=effect,
            base_root=self.base_root,
            wallet_path=self.wallet_path,
            video_path=self.video_path,
            observed_at=T0 + timedelta(minutes=minute + 3),
            transport=FakeRpc(
                receipt(
                    tx_hash=tx_hash,
                    block_hash=block_hash,
                    recipient=recipient,
                    amount=amount,
                )
            ),
        )

    def test_full_three_effect_lifecycle(self):
        prepared = self.prepare()
        self.assertEqual(prepared["status"], "PREPARED")
        self.assertEqual(prepared["mission_state"], "READY_FOR_EXECUTION")
        self.assertEqual(prepared["next_effect"], "anna")
        self.assertEqual(
            [item["amount_base_units"] for item in prepared["effects"]],
            [250_000, 420_000, 370_000],
        )

        anna = self.full_effect(
            "anna", "exec_anna", "0x" + "aa" * 32, "0x" + "ab" * 32,
            ANNA, 250_000, 1,
        )
        self.assertEqual(anna["decision"], "ANNA_CONFIRMED_SKIP_FOREVER_MARK_NOW_ELIGIBLE")
        self.assertEqual(anna["confirmed_effects"], ["anna"])
        self.assertEqual(anna["next_effect"], "mark")
        self.assertEqual(anna["mission_state"], "READY_FOR_EXECUTION")

        skipped_anna = VIDEO.execute_simulation(
            api_key="kh_test",
            approval="irrelevant-after-confirmation",
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=5),
            http_transport_factory=lambda key: (_ for _ in ()).throw(
                AssertionError("confirmed Anna must not construct network transport")
            ),
        )
        self.assertEqual(skipped_anna["decision"], "SKIP_ALREADY_CHAIN_CONFIRMED")
        self.assertEqual(skipped_anna["simulation_posts"], 0)
        self.assertEqual(skipped_anna["broadcast_posts"], 0)

        mark = self.full_effect(
            "mark", "exec_mark", "0x" + "bb" * 32, "0x" + "bc" * 32,
            MARK, 420_000, 6,
        )
        self.assertEqual(mark["decision"], "MARK_CONFIRMED_SKIP_FOREVER_LEO_NOW_ELIGIBLE")
        self.assertEqual(mark["confirmed_effects"], ["anna", "mark"])
        self.assertEqual(mark["next_effect"], "leo")
        self.assertEqual(mark["mission_state"], "READY_FOR_EXECUTION")

        leo = self.full_effect(
            "leo", "exec_leo", "0x" + "cc" * 32, "0x" + "cd" * 32,
            LEO, 370_000, 11,
        )
        self.assertEqual(leo["decision"], "MISSION_COMPLETE_ALL_EFFECTS_CHAIN_CONFIRMED")
        self.assertEqual(leo["confirmed_effects"], ["anna", "mark", "leo"])
        self.assertIsNone(leo["next_effect"])
        self.assertEqual(leo["mission_state"], "COMPLETED")

        final = self.status()
        self.assertEqual(final["mission_state"], "COMPLETED")
        self.assertEqual(final["confirmed_effects"], ["anna", "mark", "leo"])
        self.assertIsNone(final["next_effect"])
        self.assertEqual(final["network_calls"], 0)

    def test_leo_is_blocked_before_mark_confirmation_without_network(self):
        self.prepare()
        with self.assertRaises(VIDEO.AnnaMarkLeoVideoMissionError) as caught:
            VIDEO.execute_simulation(
                api_key="kh_test",
                approval="not-used",
                run_ref=RUN_REF,
                effect_ref="leo",
                base_root=self.base_root,
                observed_at=T0 + timedelta(minutes=1),
                http_transport_factory=lambda key: (_ for _ in ()).throw(
                    AssertionError("network must not be constructed")
                ),
            )
        self.assertEqual(caught.exception.code, "OUT_OF_SEQUENCE_EFFECT")

    def test_duplicate_leo_recipient_fails_closed(self):
        self.write_recipients(leo=MARK)
        with self.assertRaises(VIDEO.AnnaMarkLeoVideoMissionError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, "VIDEO_WALLETS_MUST_BE_DISTINCT")

    def test_operator_output_masks_all_wallet_addresses(self):
        prepared = self.prepare()
        serialized = json.dumps(prepared, sort_keys=True).lower()
        for address in (SENDER, ANNA, MARK, LEO):
            self.assertNotIn(address.lower(), serialized)


if __name__ == "__main__":
    unittest.main()
