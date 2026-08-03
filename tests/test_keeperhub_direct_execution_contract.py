from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class KeeperHubDirectExecutionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = (
            DOCS / "KEEPERHUB_DIRECT_EXECUTION_CONTRACT.md"
        ).read_text(encoding="utf-8")
        self.runbook = (
            DOCS / "TESTNET_EVIDENCE_RUNBOOK.md"
        ).read_text(encoding="utf-8")
        self.all_text = self.contract + "\n" + self.runbook

    def test_exact_official_surfaces_are_pinned(self) -> None:
        for endpoint in (
            "GET /api/user/wallet",
            "GET /api/chains",
            "POST /api/execute/transfer",
            "GET /api/execute/{executionId}/status",
        ):
            self.assertIn(endpoint, self.all_text)

    def test_stable_testnet_constants_are_explicit(self) -> None:
        self.assertIn("Base Sepolia", self.contract)
        self.assertIn("`84532`", self.contract)
        self.assertIn(
            "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            self.contract,
        )
        self.assertIn("Ethereum Sepolia", self.contract)
        self.assertIn("`11155111`", self.contract)
        self.assertIn(
            "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
            self.contract,
        )

    def test_simulation_and_broadcast_body_parity_is_required(self) -> None:
        for phrase in (
            '"simulate": true',
            "strict JSON boolean",
            "wouldRevert = false",
            "with only `simulate` removed",
            "no float conversion",
        ):
            self.assertIn(phrase, self.all_text)

    def test_idempotency_is_bounded_and_never_changes_intent(self) -> None:
        for phrase in (
            "same key + same body",
            "idempotency_conflict",
            "idempotency_in_progress",
            "24 hours",
            "A new key must never be generated",
            "maximum_simulation_posts: 1",
            "maximum_broadcast_posts: 1",
            "maximum_mutating_calls: 1",
            "maximum_new_request_keys_after_ambiguity: 0",
        ):
            self.assertIn(phrase, self.all_text)

    def test_provider_reference_is_a_p0_live_gate(self) -> None:
        for phrase in (
            "executionId",
            "persisted durably",
            "before",
            "PROVIDER_ACKNOWLEDGED",
            "P0 implementation gate before live broadcast",
            "EXECUTION_UNKNOWN",
        ):
            self.assertIn(phrase, self.all_text)

    def test_status_and_independent_verification_are_separate(self) -> None:
        for phrase in (
            "X-Poll-Interval-Hint",
            "transactionHash",
            "transactionLink",
            "independently verifies",
            "Provider acceptance alone is insufficient",
            "`executionId` alone is also insufficient",
        ):
            self.assertIn(phrase, self.all_text)

    def test_no_credential_value_or_false_live_claim_is_embedded(self) -> None:
        self.assertNotRegex(self.all_text, r"kh_[A-Za-z0-9]{20,}")
        self.assertNotIn("LIVE TRANSACTION COMPLETED", self.all_text)
        self.assertIn("LIVE ACTION NOT AUTHORIZED", self.contract)
        self.assertIn("PLAN ONLY — NOT A TRANSACTION AUTHORIZATION", self.runbook)
        self.assertIn("Mainnet and blind retry remain blocked", self.contract)

    def test_only_reviewed_public_addresses_are_present(self) -> None:
        addresses = set(re.findall(r"0x[0-9a-fA-F]{40}", self.all_text))
        self.assertEqual(
            addresses,
            {
                "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
            },
        )


if __name__ == "__main__":
    unittest.main()
