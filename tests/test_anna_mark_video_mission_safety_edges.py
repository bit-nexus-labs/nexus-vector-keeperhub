from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.integrations.keeperhub_controlled_execution import (
    KeeperHubControlledExecutionError,
)
from nexus_vector.integrations.keeperhub_direct_execution import KeeperHubTransportResponse

ROOT = Path(__file__).parents[1]


def load_tool():
    path = ROOT / "tools" / "anna_mark_video_mission.py"
    spec = importlib.util.spec_from_file_location("anna_mark_video_mission_edges", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VIDEO = load_tool()
T0 = datetime(2026, 8, 9, 1, 0, tzinfo=timezone.utc)
RUN_REF = "anna-mark-video-safety-edge-v1"
SENDER = "0x" + "11" * 20
ANNA = "0x" + "22" * 20
MARK = "0x" + "33" * 20


class RaisingTransferTransport:
    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0

    def post_transfer(self, body, *, idempotency_key):
        self.calls += 1
        raise self.error


class FixedTransferTransport:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def post_transfer(self, body, *, idempotency_key):
        self.calls += 1
        return self.response


class ExplodingOnPostTransport:
    def __init__(self):
        self.calls = 0

    def post_transfer(self, body, *, idempotency_key):
        self.calls += 1
        raise AssertionError("a second provider POST must not occur")


class AnnaMarkSafetyEdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        temp = Path(self.temp.name)
        self.base_root = temp / "missions"
        self.wallet_path = temp / "wallets.private-local.json"
        self.video_path = temp / "anna_mark_video_recipients.private-local.json"
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
                    "safety": {"mainnet_blocked": True},
                    "tokens": {},
                }
            ),
            encoding="utf-8",
        )
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
                    "recipients": {"mark_recipient_wallet": MARK},
                    "safety": {"mainnet_blocked": True},
                }
            ),
            encoding="utf-8",
        )
        self.prepared = VIDEO.prepare_video_mission(
            RUN_REF,
            base_root=self.base_root,
            wallet_path=self.wallet_path,
            video_path=self.video_path,
            observed_at=T0,
        )
        self.challenge = self.prepared["simulation_approval_challenge"]

    def tearDown(self):
        self.temp.cleanup()

    def test_unknown_simulation_outcome_cannot_send_second_post(self):
        first_transport = RaisingTransferTransport(TimeoutError("timeout"))
        first = VIDEO.execute_simulation(
            api_key="kh_test",
            approval=self.challenge,
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=1),
            http_transport_factory=lambda key: first_transport,
        )
        self.assertEqual(first["status"], "STOP")
        self.assertEqual(first["reason"], "SIMULATION_OUTCOME_UNKNOWN")
        self.assertEqual(first["simulation_posts"], 1)
        self.assertEqual(first_transport.calls, 1)
        self.assertFalse(first["retry_same_effect"])

        second_transport = ExplodingOnPostTransport()
        second = VIDEO.execute_simulation(
            api_key="kh_test",
            approval=self.challenge,
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=2),
            http_transport_factory=lambda key: second_transport,
        )
        self.assertEqual(second["status"], "STOP")
        self.assertEqual(second["reason"], "AUTHORIZATION_ALREADY_CONSUMED")
        self.assertEqual(second["simulation_posts"], 0)
        self.assertEqual(second_transport.calls, 0)
        self.assertFalse(second["retry_same_effect"])

    def test_rejected_simulation_can_never_reach_broadcast_post(self):
        rejected = FixedTransferTransport(KeeperHubTransportResponse(403, {}))
        first = VIDEO.execute_simulation(
            api_key="kh_test",
            approval=self.challenge,
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=1),
            http_transport_factory=lambda key: rejected,
        )
        self.assertEqual(first["status"], "STOP")
        self.assertEqual(first["reason"], "REJECTED_FINAL")
        self.assertEqual(first["simulation_posts"], 1)
        self.assertEqual(rejected.calls, 1)

        # The low-level receipt can still deterministically derive a challenge,
        # but the broadcast port must reject the non-eligible durable receipt
        # before any provider POST is possible.
        second = VIDEO.execute_simulation(
            api_key="kh_test",
            approval=self.challenge,
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=2),
            http_transport_factory=lambda key: ExplodingOnPostTransport(),
        )
        self.assertEqual(second["status"], "PASS")
        self.assertEqual(second["simulation_posts"], 0)

        broadcast_transport = ExplodingOnPostTransport()
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            VIDEO.execute_broadcast(
                api_key="kh_test",
                approval=second["broadcast_approval_challenge"],
                run_ref=RUN_REF,
                effect_ref="anna",
                approve_testnet_write=True,
                base_root=self.base_root,
                observed_at=T0 + timedelta(minutes=3),
                http_transport_factory=lambda key: broadcast_transport,
            )
        self.assertEqual(caught.exception.code, "SIMULATION_NOT_ELIGIBLE")
        self.assertEqual(broadcast_transport.calls, 0)

    def test_local_status_surfaces_non_eligible_simulation_state(self):
        rejected = FixedTransferTransport(KeeperHubTransportResponse(403, {}))
        VIDEO.execute_simulation(
            api_key="kh_test",
            approval=self.challenge,
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=1),
            http_transport_factory=lambda key: rejected,
        )
        status = VIDEO.local_status(RUN_REF, base_root=self.base_root)
        anna = next(item for item in status["effects"] if item["effect_ref"] == "anna")
        self.assertEqual(
            anna["simulation_authorization_state"],
            "REJECTED_FINAL",
        )
        self.assertEqual(status["network_calls"], 0)
        self.assertFalse(status["retry_broadcast"])


if __name__ == "__main__":
    unittest.main()
