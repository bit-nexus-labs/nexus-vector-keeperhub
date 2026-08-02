from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class SubmissionMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.video = (DOCS / "DEMO_VIDEO_SCRIPT.md").read_text(encoding="utf-8")
        self.submission = (DOCS / "SUBMISSION_DRAFT.md").read_text(encoding="utf-8")
        self.runbook = (DOCS / "TESTNET_EVIDENCE_RUNBOOK.md").read_text(encoding="utf-8")
        self.questions = (DOCS / "OFFICE_HOURS_QUESTIONS_20260804.md").read_text(encoding="utf-8")
        self.checklist = (DOCS / "SUBMISSION_CHECKLIST.md").read_text(encoding="utf-8")
        self.all_text = "\n".join((self.video, self.submission, self.runbook, self.questions, self.checklist))

    def test_replay_and_runtime_are_not_confused(self) -> None:
        self.assertIn("REPLAY · SANITIZED", self.video)
        self.assertIn("does not claim a live transaction", self.video)
        self.assertIn("does not claim a completed KeeperHub transaction", self.submission)
        self.assertIn("PLAN ONLY — NOT A TRANSACTION AUTHORIZATION", self.runbook)

    def test_submission_keeps_required_links_pending(self) -> None:
        placeholders = set(re.findall(r"PENDING_[A-Z_]+", self.submission))
        self.assertEqual(
            placeholders,
            {
                "PENDING_VERIFIED_VIDEO_URL",
                "PENDING_DEPLOYED_FRONTEND_URL",
                "PENDING_EXACT_PUBLIC_EXPLORER_URL",
            },
        )

    def test_runbook_is_one_call_fail_closed(self) -> None:
        for token in (
            "maximum_provider_calls: 1",
            "maximum_broadcasts: 1",
            "Stop with no retry",
            "EXECUTION_UNKNOWN",
            "Provider acceptance alone is insufficient",
        ):
            self.assertIn(token, self.runbook)

    def test_no_secret_values_or_real_addresses_are_embedded(self) -> None:
        self.assertNotRegex(self.all_text, r"kh_[A-Za-z0-9]{12,}")
        self.assertNotRegex(self.all_text, r"0x[0-9a-fA-F]{40}")
        self.assertNotRegex(self.all_text, r"0x[0-9a-fA-F]{64}")
        self.assertNotIn("BEGIN PRIVATE KEY", self.all_text)

    def test_mainnet_remains_blocked(self) -> None:
        self.assertIn("mainnet blocked", self.submission.lower())
        self.assertNotIn("mainnet transaction completed", self.all_text.lower())

    def test_freeze_checklist_requires_link_and_secret_verification(self) -> None:
        for phrase in (
            "GitHub CI is green",
            "verify_public_evidence.py",
            "Mainnet remains blocked",
            "clean/incognito browser",
            "Maximum provider calls = 1",
            "All `PENDING_*` placeholders",
            "2026-08-10 20:00",
        ):
            self.assertIn(phrase, self.checklist)

    def test_office_hours_targets_current_runtime_blockers(self) -> None:
        for phrase in (
            "Direct Execution API",
            "Wallet readiness",
            "Lost response",
            "Idempotency retention",
            "Evidence requirement",
        ):
            self.assertIn(phrase, self.questions)


if __name__ == "__main__":
    unittest.main()
