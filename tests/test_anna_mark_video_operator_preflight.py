from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.integrations.keeperhub_direct_execution import KeeperHubTransportResponse

ROOT = Path(__file__).parents[1]


def load_tool(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VIDEO = load_tool("anna_mark_video_mission_for_preflight", "tools/anna_mark_video_mission.py")
PREFLIGHT = load_tool(
    "anna_mark_video_operator_preflight",
    "tools/anna_mark_video_operator_preflight.py",
)

T0 = datetime(2026, 8, 9, 2, 0, tzinfo=timezone.utc)
RUN_REF = "anna-mark-video-preflight-v1"
SENDER = "0x" + "11" * 20
ANNA = "0x" + "22" * 20
MARK = "0x" + "33" * 20
EXECUTION_ID = "exec_anna_preflight_01"


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def post_transfer(self, body, *, idempotency_key):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class OperatorPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        temp = Path(self.temp.name)
        self.base_root = temp / "missions"
        wallet_path = temp / "wallets.private-local.json"
        video_path = temp / "anna_mark_video_recipients.private-local.json"
        wallet_path.write_text(
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
                    "tokens": {},
                    "safety": {"mainnet_blocked": True},
                }
            ),
            encoding="utf-8",
        )
        video_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at_utc": "2026-08-09T02:00:00.000000Z",
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
        prepared = VIDEO.prepare_video_mission(
            RUN_REF,
            base_root=self.base_root,
            wallet_path=wallet_path,
            video_path=video_path,
            observed_at=T0,
        )
        self.challenge = prepared["simulation_approval_challenge"]

    def tearDown(self):
        self.temp.cleanup()

    def preflight_sim(self):
        return PREFLIGHT.simulation_preflight(
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
        )

    def preflight_broadcast(self):
        return PREFLIGHT.broadcast_preflight(
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
        )

    def simulate(self, transport):
        return VIDEO.execute_simulation(
            api_key="kh_test",
            approval=self.challenge,
            run_ref=RUN_REF,
            effect_ref="anna",
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=1),
            http_transport_factory=lambda key: transport,
        )

    def test_fresh_simulation_is_allowed_locally(self):
        result = self.preflight_sim()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["decision"], "ALLOW_FIRST_SIMULATION")
        self.assertEqual(result["network_calls"], 0)
        self.assertFalse(result["retry_mutating_call"])

    def test_unknown_simulation_is_stopped_before_runner_retry(self):
        first = self.simulate(FakeTransport(error=TimeoutError("timeout")))
        self.assertEqual(first["status"], "STOP")
        result = self.preflight_sim()
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(
            result["reason"],
            "SIMULATION_RECONCILIATION_REQUIRED_NO_RETRY",
        )
        self.assertEqual(result["simulation_authorization_state"], "OUTCOME_UNKNOWN")
        self.assertEqual(result["network_calls"], 0)

    def test_rejected_simulation_is_stopped_before_runner_retry(self):
        first = self.simulate(
            FakeTransport(response=KeeperHubTransportResponse(403, {}))
        )
        self.assertEqual(first["status"], "STOP")
        result = self.preflight_sim()
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(result["reason"], "SIMULATION_REJECTED_FINAL_NO_RETRY")
        self.assertEqual(result["simulation_authorization_state"], "REJECTED_FINAL")
        self.assertEqual(result["network_calls"], 0)

    def test_eligible_simulation_allows_only_durable_receipt_read(self):
        first = self.simulate(
            FakeTransport(
                response=KeeperHubTransportResponse(
                    200,
                    {
                        "success": True,
                        "status": "simulated",
                        "wouldRevert": False,
                        "gasEstimate": "45415",
                    },
                )
            )
        )
        self.assertEqual(first["status"], "PASS")
        result = self.preflight_sim()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            result["decision"],
            "ALLOW_DURABLE_SIMULATION_RECEIPT_READ",
        )
        self.assertEqual(result["network_calls"], 0)

    def test_broadcast_requires_durable_eligible_simulation(self):
        result = self.preflight_broadcast()
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(result["reason"], "SIMULATION_NOT_DURABLY_ELIGIBLE")
        self.assertEqual(result["network_calls"], 0)

    def test_broadcast_preflight_allows_exactly_fresh_prepared_attempt(self):
        simulation = self.simulate(
            FakeTransport(
                response=KeeperHubTransportResponse(
                    200,
                    {
                        "success": True,
                        "status": "simulated",
                        "wouldRevert": False,
                        "gasEstimate": "45415",
                    },
                )
            )
        )
        self.assertEqual(simulation["status"], "PASS")
        result = self.preflight_broadcast()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["decision"], "ALLOW_ONE_BROADCAST_ATTEMPT")
        self.assertEqual(result["attempt_state"], "PREPARED")
        self.assertEqual(result["network_calls"], 0)

        broadcast = VIDEO.execute_broadcast(
            api_key="kh_test",
            approval=simulation["broadcast_approval_challenge"],
            run_ref=RUN_REF,
            effect_ref="anna",
            approve_testnet_write=True,
            base_root=self.base_root,
            observed_at=T0 + timedelta(minutes=2),
            http_transport_factory=lambda key: FakeTransport(
                response=KeeperHubTransportResponse(
                    202,
                    {"executionId": EXECUTION_ID, "status": "completed"},
                )
            ),
        )
        self.assertEqual(broadcast["status"], "PASS")

        after = self.preflight_broadcast()
        self.assertEqual(after["status"], "STOP")
        self.assertEqual(
            after["reason"],
            "BROADCAST_ALREADY_CONSUMED_RECONCILE_ONLY",
        )
        self.assertTrue(after["provider_reference_present"])
        self.assertEqual(after["network_calls"], 0)

    def test_wrapper_invokes_preflight_before_dpapi_key_loading(self):
        source = (ROOT / "tools" / "invoke_anna_mark_video_mission.ps1").read_text(
            encoding="utf-8"
        )
        preflight_pos = source.index("$preflightOutput")
        credential_pos = source.index("Import-Clixml")
        self.assertLess(preflight_pos, credential_pos)
        self.assertIn("anna_mark_video_operator_preflight.py", source)


if __name__ == "__main__":
    unittest.main()
