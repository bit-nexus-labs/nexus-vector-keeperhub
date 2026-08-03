from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class _MissionLabInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.scenarios: list[str] = []
        self.presets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(str(attributes["id"]))
        if tag == "button" and attributes.get("data-scenario"):
            self.scenarios.append(str(attributes["data-scenario"]))
        if tag == "button" and attributes.get("data-preset"):
            self.presets.append(str(attributes["data-preset"]))


class MissionResilienceLabUITests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.css = (FRONTEND / "styles.css").read_text(encoding="utf-8")
        self.app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.combined = "\n".join((self.index, self.css, self.app))

    def test_dynamic_mission_builder_and_recovery_controls_exist(self) -> None:
        inventory = _MissionLabInventory()
        inventory.feed(self.index)
        required = {
            "mission-builder",
            "payment-list",
            "add-payment",
            "lock-mission",
            "reset-session",
            "failure-lab",
            "incident-stage",
            "recovery-console",
            "open-black-box",
            "effect-cards",
            "technical-table",
            "verified-proof",
            "treasury-decision",
        }
        self.assertTrue(required.issubset(inventory.ids), required - inventory.ids)
        self.assertEqual(
            inventory.scenarios,
            ["lost-response", "double-submit", "restart", "payload-mutation", "retry-all"],
        )
        self.assertEqual(inventory.presets, ["single", "unequal", "batch", "mixed"])

    def test_effect_count_is_variable_and_safely_bounded(self) -> None:
        self.assertIn("const MAX_EFFECTS = 10", self.app)
        self.assertIn("const MIN_EFFECTS = 1", self.app)
        self.assertIn("payments.length >= MAX_EFFECTS", self.app)
        self.assertIn("payments.length <= MIN_EFFECTS", self.app)
        self.assertIn("1–10 independent payment effects", self.index)
        self.assertIn("1–10 canonical effects", self.index)
        self.assertIn("payments.map", self.app)
        self.assertIn("payments.reduce", self.app)

    def test_public_product_language_has_no_evaluation_framing(self) -> None:
        lowered = self.combined.lower()
        forbidden = (
            "judge",
            "judges",
            "jury",
            "judging mode",
            "evaluation block",
            "for the jury",
            "hackathon demo",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase, lowered, phrase)
        self.assertIn("Mission Resilience Lab", self.index)
        self.assertIn("Failure Scenarios", self.index)
        self.assertIn("Recovery Console", self.index)
        self.assertIn("Verified Testnet Evidence", self.index)

    def test_replay_and_runtime_boundaries_are_explicit(self) -> None:
        self.assertGreaterEqual(self.index.count("NO LIVE TRANSACTION"), 2)
        self.assertIn("SANDBOX REPLAY · NO LIVE TX", self.index)
        self.assertIn("PENDING_RUNTIME", self.index)
        self.assertIn("No real KeeperHub transaction is claimed.", self.index)
        self.assertIn("operator-gated execution boundary", self.index)
        self.assertIn("No provider request, wallet operation, signature or transaction is performed.", self.index)

    def test_interactions_are_local_text_only_and_accessible(self) -> None:
        self.assertIn('addPaymentButton.addEventListener("click"', self.app)
        self.assertIn('lockMissionButton.addEventListener("click"', self.app)
        self.assertIn("scenarioButtons.forEach", self.app)
        self.assertIn('setAttribute("aria-selected"', self.app)
        self.assertIn("element.textContent", self.app)
        self.assertIn("replaceChildren", self.app)
        self.assertNotIn("innerHTML", self.app)
        self.assertNotRegex(self.index, r"<(?:audio|video)\b")
        self.assertNotRegex(self.index, r'type="range"')

    def test_failure_scenarios_map_to_state_machine_safety(self) -> None:
        self.assertIn("EXECUTION_UNKNOWN", self.app)
        self.assertIn("RECONCILE_REQUIRED", self.app)
        self.assertIn("DUPLICATE_SUPPRESSED", self.app)
        self.assertIn("FINGERPRINT_MISMATCH", self.app)
        self.assertIn("SKIP_VERIFIED", self.app)
        self.assertIn("EXECUTE_MISSING", self.app)
        self.assertIn("duplicate economic authority denied", self.app)
        self.assertIn("processEpoch += 1", self.app)

    def test_hero_is_reduced_without_external_font_dependency(self) -> None:
        hero_block = re.search(r"\.hero h1\s*\{(?P<body>.*?)\}", self.css, re.S)
        self.assertIsNotNone(hero_block)
        assert hero_block is not None
        body = hero_block.group("body")
        self.assertIn("clamp(2.65rem, 5vw, 4.65rem)", body)
        self.assertIn("letter-spacing: -0.048em", body)
        self.assertNotIn("6rem", body)
        self.assertIn("font-family: Inter", self.css)
        self.assertNotIn("@import", self.css)
        self.assertNotRegex(self.index, r"https?://")

    def test_no_unsupported_runtime_or_financial_claims(self) -> None:
        forbidden = (
            "MISSION ACCOMPLISHED",
            "100% network chaos",
            "90% connection",
            "Mark: SUCCESS",
            "Leo: SUCCESS",
            "funds moved successfully",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase, self.combined, phrase)
        self.assertNotRegex(self.index, r"\$\s*\d")
        self.assertNotIn("window.ethereum", self.combined)
        self.assertNotIn("walletconnect", self.combined.lower())

    def test_motion_and_mobile_rules_are_present(self) -> None:
        self.assertIn(".telemetry-console", self.css)
        self.assertIn(".scenario-card", self.css)
        self.assertIn(".flight-recorder", self.css)
        self.assertIn(".treasury-gate", self.css)
        self.assertIn("@media (max-width: 620px)", self.css)
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', self.app)
        self.assertIn('behavior: prefersReducedMotion ? "auto" : "smooth"', self.app)


if __name__ == "__main__":
    unittest.main()
