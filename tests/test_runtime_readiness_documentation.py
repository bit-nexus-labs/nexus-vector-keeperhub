from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RuntimeReadinessDocumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(
            encoding="utf-8"
        )
        self.readiness = (ROOT / "docs" / "RUNTIME_READINESS.md").read_text(
            encoding="utf-8"
        )
        self.manifest = json.loads(
            (ROOT / "evidence" / "public_manifest.json").read_text(
                encoding="utf-8"
            )
        )

    def test_keeperhub_integration_is_documented_as_offline_implemented(self) -> None:
        combined = self.readme + "\n" + self.architecture + "\n" + self.readiness
        for phrase in (
            "durable provider execution reference",
            "simulation-first KeeperHub adapter",
            "provider status observation",
            "no-retry/no-redirect",
            "wallet-readiness",
            "chain catalog",
        ):
            self.assertIn(phrase.casefold(), combined.casefold())
        self.assertNotIn("External ports — not implemented", combined)
        self.assertNotIn("a reviewed KeeperHub execution adapter", self.readme)

    def test_runtime_action_and_mainnet_boundaries_remain_explicit(self) -> None:
        combined = self.readme + "\n" + self.architecture + "\n" + self.readiness
        for phrase in (
            "does **not** claim that a real KeeperHub testnet transaction",
            "WAITING FOR ACTION-SPECIFIC APPROVAL",
            "maximum_broadcasts: 1",
            "Mainnet and blind retry remain blocked",
            "No step authorizes automatic resend after ambiguity",
        ):
            self.assertIn(phrase, combined)
        self.assertNotIn("keeperhub_api_key:", self.readiness)

    def test_public_manifest_contains_current_offline_integration_claims(self) -> None:
        expected = {
            "keeperhub_contract_review": "cbc3b1471e68f7295991ea579a0d76d989083b4f",
            "durable_provider_reference": "d1065510260e77b71ba47cef795e2b2cd195698b",
            "keeperhub_direct_execution_adapter": "dbb8297b2872ee75cbcdc95f3550cf76077c236b",
            "keeperhub_status_observer": "999cf9d07edde251a93f521bdc76323b709c75c6",
            "bounded_keeperhub_https_transport": "3e7e355c356197298217fad01fb969619e6cd093",
        }
        claims = {item["claim_id"]: item for item in self.manifest["claims"]}
        for claim_id, commit in expected.items():
            with self.subTest(claim_id=claim_id):
                self.assertEqual(claims[claim_id]["status"], "OFFLINE_VERIFIED")
                self.assertEqual(claims[claim_id]["merge_commit"], commit)

    def test_exactly_one_runtime_claim_remains_pending(self) -> None:
        pending = [
            item
            for item in self.manifest["claims"]
            if item["status"] == "PENDING_RUNTIME"
        ]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["claim_id"], "keeperhub_testnet_transaction")
        self.assertIsNone(pending[0]["merge_commit"])
        self.assertEqual(
            self.manifest["runtime_evidence"]["status"],
            "NOT_YET_COLLECTED",
        )

    def test_no_external_action_is_claimed(self) -> None:
        self.assertTrue(
            all(
                type(value) is int and value == 0
                for value in self.manifest["external_actions_represented"].values()
            )
        )
        self.assertIsNone(
            self.manifest["runtime_evidence"]["transaction_hash"]
        )
        self.assertIsNone(self.manifest["runtime_evidence"]["explorer_url"])


if __name__ == "__main__":
    unittest.main()
