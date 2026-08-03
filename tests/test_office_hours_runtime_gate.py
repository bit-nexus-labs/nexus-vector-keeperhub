from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class OfficeHoursRuntimeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.office = (DOCS / "KEEPERHUB_OFFICE_HOURS_20260804.md").read_text(encoding="utf-8")
        self.sheet = (DOCS / "CONTROLLED_TESTNET_ACTION_SHEET_TEMPLATE.md").read_text(encoding="utf-8")
        self.readiness = (DOCS / "RUNTIME_READINESS.md").read_text(encoding="utf-8")
        self.runbook = (DOCS / "TESTNET_EVIDENCE_RUNBOOK.md").read_text(encoding="utf-8")
        self.combined = "\n".join((self.office, self.sheet, self.readiness, self.runbook))

    def test_office_hours_packet_is_narrow_and_scheduled(self) -> None:
        self.assertIn("2026-08-04 13:00 Europe/Kyiv", self.office)
        self.assertIn("QUESTION PACK ONLY — NOT RUNTIME AUTHORIZATION", self.office)
        required_topics = (
            "Hackathon eligibility of the integration path",
            "Exact organization-wallet readiness surface",
            "Gas and ERC-20 test-token balance readiness",
            "Lost response before `executionId` is received",
            "Exact idempotency semantics and retention",
            "Status, transaction evidence, and polling contract",
            "Submission and public evidence requirements",
            "Safe multi-effect failure demonstration",
        )
        for topic in required_topics:
            self.assertIn(topic, self.office)
        self.assertIn("exact UI path, endpoint, response field, status, or documented procedure", self.office)
        self.assertIn("A vague answer remains a blocker", self.readiness)

    def test_action_sheet_separates_simulation_and_broadcast_authority(self) -> None:
        self.assertIn("TEMPLATE ONLY — NOT A TRANSACTION AUTHORIZATION", self.sheet)
        self.assertIn("simulation_authorization", self.sheet)
        self.assertIn("broadcast_authorization", self.sheet)
        self.assertIn("required_runtime_flag: --approve-testnet-write", self.sheet)
        self.assertIn("Simulation approval does not imply broadcast approval", self.sheet)
        self.assertIn("Simulation success never implies permission to broadcast", self.readiness)
        self.assertIn("Simulation requires its own one-time action-specific approval", self.runbook)
        self.assertIn("Broadcast requires a separate one-time approval", self.runbook)

    def test_call_budgets_are_explicit_and_not_conflated(self) -> None:
        required = (
            "maximum_simulation_posts: 1",
            "maximum_broadcast_posts: 1",
            "maximum_mutating_calls: 1",
            "maximum_same_key_recovery_posts_after_ambiguity: 0",
            "maximum_new_request_keys_after_ambiguity: 0",
        )
        for value in required:
            self.assertIn(value, self.sheet)
            self.assertIn(value, self.readiness)
            self.assertIn(value, self.runbook)
        self.assertNotIn("maximum_provider_calls", self.combined)
        self.assertIn("The simulation POST and broadcast POST are distinct provider calls", self.sheet)
        self.assertIn("Only the single broadcast is the mutating call", self.runbook)

    def test_ambiguity_never_authorizes_a_new_key_or_second_broadcast(self) -> None:
        self.assertIn("new_request_authorized: false", self.sheet)
        self.assertIn("second_broadcast_authorized: false", self.sheet)
        self.assertIn("Never convert uncertainty into a new request key", self.sheet)
        self.assertIn("A new key for the same ambiguous economic effect is forbidden", self.readiness)
        self.assertIn("not a new key and not a second POST", self.runbook)
        self.assertIn("Nexus Vector will not generate a new key", self.office)

    def test_documents_preserve_mainnet_and_runtime_claim_boundaries(self) -> None:
        self.assertIn("MAINNET BLOCKED", self.office)
        self.assertIn("mainnet_blocked: true", self.sheet)
        self.assertIn("Mainnet | BLOCKED", self.readiness)
        self.assertIn("No real KeeperHub transaction is claimed", self.sheet)
        forbidden_claims = (
            "KeeperHub paid the recipient",
            "30/30 completed live",
            "funds moved successfully",
        )
        for claim in forbidden_claims:
            self.assertNotIn(claim, self.office)
            self.assertNotIn(claim, self.sheet)

    def test_public_templates_contain_no_live_secret_or_transaction_values(self) -> None:
        self.assertIsNone(re.search(r"\bkh_[A-Za-z0-9_-]{12,}\b", self.combined))
        self.assertIsNone(re.search(r"\b0x[a-fA-F0-9]{40}\b", self.combined))
        self.assertIsNone(re.search(r"\b0x[a-fA-F0-9]{64}\b", self.combined))
        self.assertNotIn("Authorization: Bearer", self.combined)
        self.assertNotIn("private_key:", self.combined.lower())
        self.assertNotIn("seed_phrase:", self.combined.lower())

    def test_cross_document_links_exist(self) -> None:
        linked = (
            "CONTROLLED_TESTNET_ACTION_SHEET_TEMPLATE.md",
            "KEEPERHUB_OFFICE_HOURS_20260804.md",
            "KEEPERHUB_DIRECT_EXECUTION_CONTRACT.md",
        )
        for name in linked:
            self.assertTrue((DOCS / name).is_file(), name)
        self.assertIn("CONTROLLED_TESTNET_ACTION_SHEET_TEMPLATE.md", self.readiness)
        self.assertIn("KEEPERHUB_OFFICE_HOURS_20260804.md", self.readiness)
        self.assertIn("CONTROLLED_TESTNET_ACTION_SHEET_TEMPLATE.md", self.runbook)
        self.assertIn("KEEPERHUB_OFFICE_HOURS_20260804.md", self.runbook)


if __name__ == "__main__":
    unittest.main()
