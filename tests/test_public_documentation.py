from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PublicDocumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        self.states = (ROOT / "docs" / "STATE_MACHINES.md").read_text(encoding="utf-8")
        self.starter = (ROOT / "docs" / "STARTER_GUIDE.md").read_text(encoding="utf-8")
        self.all_text = "\n".join(
            (self.readme, self.architecture, self.states, self.starter)
        )

    def test_stale_bootstrap_claims_are_removed(self) -> None:
        stale = (
            "only implemented product capability is deterministic",
            "persistence, state-machine",
            "PRODUCT-P01 establishes only",
        )
        lowered = self.all_text.lower()
        for phrase in stale:
            self.assertNotIn(phrase.lower(), lowered)

    def test_runtime_limit_is_explicit(self) -> None:
        self.assertIn(
            "does **not** claim that a real KeeperHub testnet transaction",
            self.readme,
        )
        self.assertIn("Mainnet is blocked", self.all_text)
        manifest = (ROOT / "evidence" / "public_manifest.json").read_text(
            encoding="utf-8"
        )
        self.assertIn("PENDING_RUNTIME", manifest)

    def test_core_safety_rules_are_documented(self) -> None:
        for token in (
            "EXECUTION_UNKNOWN",
            "never blind resend",
            "SKIP_VERIFIED",
            "RECONCILE_REQUIRED",
            "immutable Mission total",
        ):
            self.assertIn(token, self.all_text)

    def test_readme_relative_links_exist(self) -> None:
        links = re.findall(r"\[[^]]+\]\(([^)]+)\)", self.readme)
        self.assertTrue(links)
        for link in links:
            self.assertFalse(link.startswith(("http://", "https://")))
            self.assertTrue((ROOT / link).exists(), link)

    def test_commands_match_standard_library_ci(self) -> None:
        self.assertIn("unittest discover", self.readme)
        self.assertIn("unittest discover", self.starter)
        self.assertNotIn("pip install", self.all_text)
        self.assertNotIn("pytest", self.all_text)


if __name__ == "__main__":
    unittest.main()
