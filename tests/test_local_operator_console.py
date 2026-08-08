from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools.local_operator_console import (
    HOST,
    OperatorConsoleError,
    SnapshotProvider,
    build_server,
    validate_canary_evidence,
    validate_mission_snapshot,
)


def _canary_payload() -> dict[str, object]:
    return {
        "schema": "nexus-vector.keeperhub-simulation-evidence.v1",
        "classification": "SANITIZED_PUBLIC",
        "captured_at_local": "2026-08-06T08:20:00+03:00",
        "probe": "KEEPERHUB_RUNTIME_EVIDENCE_CANARY_V1",
        "purpose": "POST_FIX_PROVIDER_REGRESSION_VALIDATION_V1",
        "mission_ref": "simulation-canary-20260806-v1",
        "effect_ref": "provider-canary",
        "chain": "Base Sepolia",
        "chain_id": 84532,
        "asset": "USDC",
        "amount": "0.000001",
        "status": "PASS",
        "decision": "ELIGIBLE_FOR_BROADCAST_APPROVAL",
        "authorization_state": "ELIGIBLE_FOR_BROADCAST_APPROVAL",
        "provider_summary": {
            "gas_estimate": "45415",
            "http_status": 200,
            "provider_status": "simulated",
            "simulated_return_present": True,
            "success": True,
            "value": "0",
            "would_revert": False,
        },
        "simulation_posts": 1,
        "broadcast_posts": 0,
        "broadcast_authorized": False,
        "funds_moved": False,
        "retry": "NOT_REQUIRED",
        "action_sheet_binding": "MATCH",
        "request_fingerprint_binding": "MATCH",
        "private_values": "REDACTED_BY_CONSTRUCTION",
        "claim_boundary": "SIMULATION_ONLY_NOT_TRANSACTION_EVIDENCE",
    }


def _mission_payload() -> dict[str, object]:
    return {
        "snapshot": "NEXUS_VECTOR_RUNTIME_EVIDENCE_PLAN_V1",
        "mission_ref": "runtime-evidence-001",
        "mission_state": "READY_FOR_EXECUTION",
        "chain": "Base Sepolia",
        "chain_id": 84532,
        "effects": [
            {
                "effect_ref": "anna",
                "amount": "0.12",
                "asset": "USDC",
                "effect_state": "PLANNED",
                "continuation_action": "EXECUTE_MISSING",
                "reason": "PLANNED_EFFECT_NOT_DISPATCHED",
            },
            {
                "effect_ref": "mark",
                "amount": "0.07",
                "asset": "USDC",
                "effect_state": "PLANNED",
                "continuation_action": "EXECUTE_MISSING",
                "reason": "PLANNED_EFFECT_NOT_DISPATCHED",
            },
        ],
        "total_amount": "0.19",
        "provider_calls": {
            "simulation_posts": 0,
            "broadcast_posts": 0,
            "funds_moved": False,
        },
        "private_values": "REDACTED_BY_CONSTRUCTION",
    }


class CanaryValidationTests(unittest.TestCase):
    def test_accepts_exact_sanitized_pass(self) -> None:
        payload = _canary_payload()
        self.assertIs(validate_canary_evidence(payload), payload)

    def test_rejects_any_broadcast(self) -> None:
        payload = _canary_payload()
        payload["broadcast_posts"] = 1
        with self.assertRaisesRegex(
            OperatorConsoleError,
            "CANARY_BROADCAST_PRESENT",
        ):
            validate_canary_evidence(payload)

    def test_rejects_unredacted_address(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "canary.json"
            payload = _canary_payload()
            payload["captured_at_local"] = "0x" + "a" * 40
            path.write_text(json.dumps(payload), encoding="utf-8")
            snapshot = SnapshotProvider(canary_path=path).snapshot()
        self.assertEqual(snapshot["canary"]["status"], "STOP")
        self.assertIn(
            "CANARY:UNREDACTED_ADDRESS_PRESENT",
            snapshot["errors"],
        )

    def test_rejects_unknown_field(self) -> None:
        payload = _canary_payload()
        payload["unexpected"] = True
        with self.assertRaisesRegex(
            OperatorConsoleError,
            "INVALID_CANARY_SHAPE",
        ):
            validate_canary_evidence(payload)

    def test_rejects_malformed_or_unbounded_gas_estimate(self) -> None:
        for gas_estimate in ("0", "45415 gas", "1234567890123"):
            with self.subTest(gas_estimate=gas_estimate):
                payload = _canary_payload()
                payload["provider_summary"]["gas_estimate"] = gas_estimate  # type: ignore[index]
                with self.assertRaisesRegex(
                    OperatorConsoleError,
                    "INVALID_GAS_ESTIMATE",
                ):
                    validate_canary_evidence(payload)


class MissionValidationTests(unittest.TestCase):
    def test_accepts_exact_network_free_plan(self) -> None:
        payload = _mission_payload()
        self.assertIs(validate_mission_snapshot(payload), payload)

    def test_rejects_provider_activity(self) -> None:
        payload = _mission_payload()
        payload["provider_calls"]["simulation_posts"] = 1  # type: ignore[index]
        with self.assertRaisesRegex(
            OperatorConsoleError,
            "MISSION_PROVIDER_ACTIVITY_PRESENT",
        ):
            validate_mission_snapshot(payload)

    def test_rejects_unexpected_effect_reason(self) -> None:
        payload = _mission_payload()
        payload["effects"][0]["reason"] = "UNREVIEWED_FREE_TEXT"  # type: ignore[index]
        with self.assertRaisesRegex(
            OperatorConsoleError,
            "INVALID_EFFECT_REASON",
        ):
            validate_mission_snapshot(payload)

    def test_provider_loads_both_files_without_private_values(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            canary = root / "canary.json"
            mission = root / "mission.json"
            canary.write_text(json.dumps(_canary_payload()), encoding="utf-8")
            mission.write_text(json.dumps(_mission_payload()), encoding="utf-8")
            snapshot = SnapshotProvider(canary, mission).snapshot()
        serialized = json.dumps(snapshot, sort_keys=True)
        self.assertTrue(snapshot["canary"]["loaded"])
        self.assertTrue(snapshot["mission"]["loaded"])
        self.assertEqual(snapshot["errors"], [])
        self.assertNotIn("0x", serialized)
        self.assertFalse(snapshot["browser_capabilities"]["broadcast"])
        self.assertFalse(
            snapshot["browser_capabilities"]["write_endpoints"]
        )

    def test_empty_snapshot_explicitly_marks_evidence_unloaded(self) -> None:
        snapshot = SnapshotProvider().snapshot()
        self.assertFalse(snapshot["canary"]["loaded"])
        self.assertFalse(snapshot["mission"]["loaded"])
        self.assertEqual(
            snapshot["canary"]["evidence_level"],
            "LIVE_SIMULATION_NOT_LOADED",
        )
        self.assertEqual(
            snapshot["mission"]["evidence_level"],
            "OFFLINE_PLAN_NOT_LOADED",
        )

    def test_empty_snapshot_mode_is_neutral_capability_state(self) -> None:
        snapshot = SnapshotProvider().snapshot()
        self.assertEqual(snapshot["mode"], "LOCAL_READ_ONLY_CONSOLE")
        self.assertNotIn("LIVE", snapshot["mode"])


class LocalServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = build_server(SnapshotProvider(), port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address
        self.assertEqual(host, HOST)
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_health_is_read_only_and_local(self) -> None:
        with urlopen(
            f"{self.base_url}/api/runtime/health",
            timeout=2,
        ) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["host"], HOST)
        self.assertFalse(payload["write_endpoints"])

    def test_post_is_hard_blocked_before_any_action(self) -> None:
        request = Request(
            f"{self.base_url}/api/runtime/snapshot",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 405)
        payload = json.loads(caught.exception.read())
        self.assertEqual(
            payload["reason"],
            "READ_ONLY_CONSOLE_NO_WRITE_ENDPOINTS",
        )
        self.assertEqual(payload["provider_calls"], 0)
        self.assertFalse(payload["funds_moved"])

    def test_path_traversal_is_not_served(self) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.base_url}/../README.md", timeout=2)
        self.assertEqual(caught.exception.code, 404)

    def test_unloaded_shell_does_not_claim_live_evidence(self) -> None:
        with urlopen(f"{self.base_url}/", timeout=2) as response:
            html = response.read().decode("utf-8")
        self.assertIn(
            'id="runtime-badge" class="quiet-badge">LOCAL READ-ONLY CONSOLE',
            html,
        )
        self.assertNotIn("LIVE TESTNET RUNTIME", html)
        self.assertNotIn('class="stage is-active" data-stage="simulate"', html)
        self.assertNotIn("proof-node cyan is-active", html)
        self.assertIn('id="metric-simulation">—</strong>', html)
        self.assertIn('id="mission-total">—</strong>', html)
        self.assertIn("NO PROVIDER CANARY EVIDENCE LOADED", html)

    def test_canary_and_mission_claims_are_visually_separated(self) -> None:
        with urlopen(f"{self.base_url}/", timeout=2) as response:
            html = response.read().decode("utf-8")
        with urlopen(f"{self.base_url}/app.js", timeout=2) as response:
            script = response.read().decode("utf-8")
        self.assertIn("MISSION PLAN EVIDENCE", html)
        self.assertIn("PROVIDER CANARY EVIDENCE", html)
        self.assertIn(
            "Each evidence level proves only what it observed.",
            html,
        )
        self.assertIn("No broadcast claim for Anna + Mark", html)
        self.assertNotIn('class="stage-arrow"', html)
        self.assertIn("NEXT ACTION ·", script)
        self.assertIn("Independent provider canary", script)
        self.assertIn(
            "NOT MISSION EXECUTION OR TRANSACTION EVIDENCE",
            script,
        )


if __name__ == "__main__":
    unittest.main()
