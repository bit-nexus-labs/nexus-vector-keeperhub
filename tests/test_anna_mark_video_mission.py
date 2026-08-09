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
    path = ROOT / "tools" / "anna_mark_video_mission.py"
    spec = importlib.util.spec_from_file_location("anna_mark_video_mission", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VIDEO = load_tool()
T0 = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
RUN_REF = "anna-mark-video-20260809-v1"
SENDER = "0x" + "11" * 20
ANNA = "0x" + "22" * 20
MARK = "0x" + "33" * 20
OTHER = "0x" + "44" * 20
ANNA_EXECUTION = "exec_anna_01"
MARK_EXECUTION = "exec_mark_01"
ANNA_TX = "0x" + "aa" * 32
MARK_TX = "0x" + "bb" * 32
ANNA_BLOCK = "0x" + "cc" * 32
MARK_BLOCK = "0x" + "dd" * 32
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e".lower()


def topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def uint256(value: int) -> str:
    return "0x" + f"{value:064x}"


def transfer_log(*, tx_hash, block_hash, recipient, amount, log_index=0):
    return {
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
        "logIndex": hex(log_index),
    }


def receipt(*, tx_hash, block_hash, recipient, amount):
    return {
        "transactionHash": tx_hash,
        "status": "0x1",
        "blockHash": block_hash,
        "blockNumber": "0x64",
        "logs": [
            transfer_log(
                tx_hash=tx_hash,
                block_hash=block_hash,
                recipient=recipient,
                amount=amount,
            )
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


class ExplodingRpc:
    def __init__(self):
        self.calls = 0

    def call(self, method, params):
        self.calls += 1
        raise AssertionError("RPC MUST NOT BE CALLED")


class AnnaMarkVideoMissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        temp = Path(self.temp.name)
        self.base_root = temp / "missions"
        self.wallet_path = temp / "wallets.private-local.json"
        self.video_path = temp / "anna_mark_video_recipients.private-local.json"
        self.write_wallets()
        self.write_video_recipients()

    def tearDown(self):
        self.temp.cleanup()

    def write_wallets(self, *, sender=SENDER, anna=ANNA, mainnet_blocked=True):
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
                        "personal_recipient_wallet": anna,
                    },
                    "tokens": {"base_sepolia_usdc": {}},
                    "safety": {"mainnet_blocked": mainnet_blocked},
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def write_video_recipients(self, *, mark=MARK, mainnet_blocked=True):
        self.video_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at_utc": "2026-08-09T00:00:00.000000Z",
                    "network": {
                        "name": "Base Sepolia",
                        "chain_id": 84532,
                        "environment": "testnet",
                    },
                    "recipients": {"mark_recipient_wallet": mark},
                    "safety": {"mainnet_blocked": mainnet_blocked},
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

    def simulate(self, effect, approval, response):
        return VIDEO.execute_simulation(
            api_key="kh_test",
            approval=approval,
            run_ref=RUN_REF,
            effect_ref=effect,
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=1),
            http_transport_factory=lambda key: FakeTransferTransport(response),
        )

    def broadcast(self, effect, approval, execution_id, minute):
        return VIDEO.execute_broadcast(
            api_key="kh_test",
            approval=approval,
            run_ref=RUN_REF,
            effect_ref=effect,
            approve_testnet_write=True,
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=minute),
            http_transport_factory=lambda key: FakeTransferTransport(
                KeeperHubTransportResponse(
                    202,
                    {"executionId": execution_id, "status": "completed"},
                )
            ),
        )

    def bind(self, effect, execution_id, tx_hash, minute):
        return VIDEO.capture_provider_binding(
            api_key="kh_test",
            run_ref=RUN_REF,
            effect_ref=effect,
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=minute),
            http_transport_factory=lambda key: FakeStatusTransport(
                execution_id, tx_hash
            ),
        )

    def verify(self, effect, rpc, minute):
        return VIDEO.verify_effect_chain(
            run_ref=RUN_REF,
            effect_ref=effect,
            base_root=self.base_root,
            wallet_path=self.wallet_path,
            video_path=self.video_path,
            observed_at=T0 + timedelta(minutes=minute),
            transport=rpc,
        )

    def full_effect(self, effect, execution_id, tx_hash, block_hash, recipient, amount, minute):
        status = self.status()
        challenge = status["simulation_approval_challenge"]
        simulation = self.simulate(
            effect,
            challenge,
            KeeperHubTransportResponse(
                200,
                {
                    "success": True,
                    "status": "simulated",
                    "wouldRevert": False,
                    "gasEstimate": "45415",
                },
            ),
        )
        self.assertEqual(simulation["status"], "PASS")
        broadcast = self.broadcast(
            effect,
            simulation["broadcast_approval_challenge"],
            execution_id,
            minute,
        )
        self.assertEqual(broadcast["status"], "PASS")
        self.assertEqual(broadcast["broadcast_posts"], 1)
        binding = self.bind(effect, execution_id, tx_hash, minute + 1)
        self.assertEqual(binding["status"], "PASS")
        self.assertEqual(binding["status_gets"], 1)
        return self.verify(
            effect,
            FakeRpc(
                receipt(
                    tx_hash=tx_hash,
                    block_hash=block_hash,
                    recipient=recipient,
                    amount=amount,
                )
            ),
            minute + 2,
        )

    def test_full_anna_then_mark_lifecycle_completes_once(self):
        prepared = self.prepare()
        self.assertEqual(prepared["status"], "PREPARED")
        self.assertEqual(prepared["mission_state"], "READY_FOR_EXECUTION")
        self.assertEqual(prepared["next_effect"], "anna")
        self.assertEqual(prepared["network_calls"], 0)
        self.assertEqual(
            [effect["amount_base_units"] for effect in prepared["effects"]],
            [1, 2],
        )

        anna = self.full_effect(
            "anna",
            ANNA_EXECUTION,
            ANNA_TX,
            ANNA_BLOCK,
            ANNA,
            1,
            2,
        )
        self.assertEqual(anna["status"], "PASS")
        self.assertEqual(anna["decision"], "ANNA_CONFIRMED_SKIP_FOREVER_MARK_NOW_ELIGIBLE")
        self.assertEqual(anna["effect_state"], "CHAIN_CONFIRMED")
        self.assertEqual(anna["mission_state"], "READY_FOR_EXECUTION")
        self.assertEqual(anna["confirmed_effects"], ["anna"])
        self.assertEqual(anna["next_effect"], "mark")
        self.assertEqual(anna["broadcast_posts"], 0)
        self.assertEqual(anna["keeperhub_calls"], 0)

        after_anna = self.status()
        self.assertEqual(after_anna["next_effect"], "mark")
        self.assertEqual(after_anna["confirmed_effects"], ["anna"])
        self.assertTrue(after_anna["simulation_approval_challenge"].startswith("SIMULATE-MARK-"))

        skipped = VIDEO.execute_simulation(
            api_key="kh_test",
            approval="irrelevant-after-confirmation",
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=5),
            http_transport_factory=lambda key: (_ for _ in ()).throw(
                AssertionError("network must not be constructed")
            ),
        )
        self.assertEqual(skipped["status"], "PASS")
        self.assertEqual(skipped["decision"], "SKIP_ALREADY_CHAIN_CONFIRMED")
        self.assertEqual(skipped["simulation_posts"], 0)
        self.assertFalse(skipped["retry_same_effect"])

        mark = self.full_effect(
            "mark",
            MARK_EXECUTION,
            MARK_TX,
            MARK_BLOCK,
            MARK,
            2,
            6,
        )
        self.assertEqual(mark["status"], "PASS")
        self.assertEqual(mark["decision"], "MISSION_COMPLETE_ALL_EFFECTS_CHAIN_CONFIRMED")
        self.assertEqual(mark["effect_state"], "CHAIN_CONFIRMED")
        self.assertEqual(mark["mission_state"], "COMPLETED")
        self.assertEqual(mark["confirmed_effects"], ["anna", "mark"])
        self.assertIsNone(mark["next_effect"])

        final = self.status()
        self.assertEqual(final["mission_state"], "COMPLETED")
        self.assertEqual(final["confirmed_effects"], ["anna", "mark"])
        self.assertIsNone(final["next_effect"])
        self.assertEqual(final["network_calls"], 0)

    def test_mark_is_blocked_before_anna_confirmation_without_network(self):
        self.prepare()
        challenge = self.status()["simulation_approval_challenge"]
        with self.assertRaises(VIDEO.AnnaMarkVideoMissionError) as caught:
            VIDEO.execute_simulation(
                api_key="kh_test",
                approval=challenge,
                run_ref=RUN_REF,
                effect_ref="mark",
                base_root=self.base_root,
                observed_at=T0 + timedelta(minutes=1),
                http_transport_factory=lambda key: (_ for _ in ()).throw(
                    AssertionError("network must not be constructed")
                ),
            )
        self.assertEqual(caught.exception.code, "OUT_OF_SEQUENCE_EFFECT")

    def test_repeat_chain_verification_after_verified_is_zero_rpc(self):
        self.prepare()
        first = self.full_effect(
            "anna",
            ANNA_EXECUTION,
            ANNA_TX,
            ANNA_BLOCK,
            ANNA,
            1,
            2,
        )
        self.assertEqual(first["status"], "PASS")
        rpc = ExplodingRpc()
        second = self.verify("anna", rpc, 7)
        self.assertEqual(second["status"], "PASS")
        self.assertEqual(second["outcome"], "VERIFIED")
        self.assertEqual(second["rpc_calls"], 0)
        self.assertEqual(rpc.calls, 0)
        self.assertEqual(second["next_effect"], "mark")

    def test_duplicate_recipient_configuration_fails_closed(self):
        self.write_video_recipients(mark=ANNA)
        with self.assertRaises(VIDEO.AnnaMarkVideoMissionError) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, "VIDEO_WALLETS_MUST_BE_DISTINCT")

    def test_mainnet_block_is_required(self):
        self.write_video_recipients(mainnet_blocked=False)
        with self.assertRaises(VIDEO.AnnaMarkVideoMissionError) as caught:
            self.prepare()
        self.assertEqual(
            caught.exception.code,
            "VIDEO_RECIPIENT_MAINNET_BLOCK_NOT_CONFIRMED",
        )

    def test_operator_output_masks_wallet_addresses(self):
        prepared = self.prepare()
        serialized = json.dumps(prepared, sort_keys=True)
        self.assertNotIn(SENDER.lower(), serialized.lower())
        self.assertNotIn(ANNA.lower(), serialized.lower())
        self.assertNotIn(MARK.lower(), serialized.lower())

    def test_source_has_no_automatic_mutating_retry_loop(self):
        source = (ROOT / "tools" / "anna_mark_video_mission.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("while True", source)
        self.assertNotIn("for retry", source)
        self.assertIn("retry_same_effect\": False", source)
        self.assertIn("retry_broadcast\": False", source)


if __name__ == "__main__":
    unittest.main()
