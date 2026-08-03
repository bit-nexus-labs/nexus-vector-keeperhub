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
        self.polish_css = (FRONTEND / "polish.css").read_text(encoding="utf-8")
        self.app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.consistency = (FRONTEND / "presentation-consistency.js").read_text(encoding="utf-8")
        self.combined = "\n".join((self.index, self.css, self.polish_css, self.app, self.consistency))

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
            "mutation-policy",
            "unsafe-outcome-card",
            "gate-evidence-card",
            "gate-evidence-icon",
        }
        self.assertTrue(required.issubset(inventory.ids), required - inventory.ids)
        self.assertEqual(
            inventory.scenarios,
            ["lost-response", "double-submit", "restart", "payload-mutation", "retry-all"],
        )
        self.assertEqual(inventory.presets, ["single", "unequal", "batch", "mixed"])
        self.assertIn("Five-way batch", self.index)

    def test_effect_count_is_variable_and_safely_bounded(self) -> None:
        self.assertIn("const MAX_EFFECTS = 10", self.app)
        self.assertIn("const MIN_EFFECTS = 1", self.app)
        self.assertIn("payments.length >= MAX_EFFECTS", self.app)
        self.assertIn("payments.length <= MIN_EFFECTS", self.app)
        self.assertIn("1–10 independent payment effects", self.index)
        self.assertIn("1–10 canonical effects", self.index)
        self.assertIn("payments.map", self.app)
        self.assertIn("payments.reduce", self.app)

    def test_public_product_language_is_neutral(self) -> None:
        lowered = self.combined.lower()
        forbidden = (
            "ju" + "dge",
            "ju" + "dges",
            "ju" + "ry",
            "ju" + "dging mode",
            "eval" + "uation block",
            "for the " + "ju" + "ry",
            "hackathon " + "demo",
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
        self.assertIn("element.textContent", self.consistency)
        self.assertNotIn("innerHTML", self.combined)
        self.assertNotRegex(self.index, r"<(?:audio|video)\b")
        self.assertNotRegex(self.index, r'type="range"')

    def test_failure_scenarios_map_to_state_machine_safety(self) -> None:
        self.assertIn("EXECUTION_UNKNOWN", self.app)
        self.assertIn("RECONCILE_REQUIRED", self.app)
        self.assertIn("DUPLICATE_SUPPRESSED", self.app)
        self.assertIn('attemptState: "IN_FLIGHT"', self.app)
        self.assertIn('continuation: "RECONCILE_REQUIRED"', self.app)
        double_submit_block = re.search(
            r'if \(activeScenario === "double-submit"\) \{(?P<body>[\s\S]*?)\n    \}\n\n    if \(activeScenario === "restart"\)',
            self.app,
        )
        self.assertIsNotNone(double_submit_block)
        assert double_submit_block is not None
        self.assertNotIn('effectState: "CHAIN_CONFIRMED"', double_submit_block.group("body"))
        self.assertIn("FINGERPRINT_MISMATCH", self.app)
        self.assertIn("SKIP_VERIFIED", self.app)
        self.assertIn("EXECUTE_MISSING", self.app)
        self.assertIn("duplicate economic authority denied", self.app)
        self.assertIn("processEpoch += 1", self.app)

    def test_neutral_state_never_implies_duplicate_exposure_or_execution_authority(self) -> None:
        self.assertIn('<strong id="unsafe-total">NOT EVALUATED</strong>', self.index)
        self.assertIn('<strong id="safe-total">PERSIST FIRST</strong>', self.index)
        self.assertIn("30 awaiting persist · 0 execution authority", self.index)
        self.assertIn("editable until persist", self.index)
        self.assertIn("Draft / awaiting persist", self.index)
        self.assertIn("persist before eligibility", self.index)
        self.assertIn('unsafeCard.classList.toggle("is-neutral", !scenarioActive)', self.consistency)
        self.assertIn('missionLocked ? "new version required" : "editable until persist"', self.consistency)
        self.assertIn('missionLocked ? "Missing / eligible" : "Draft / awaiting persist"', self.consistency)
        self.assertIn('missionLocked ? "immutable demo units" : "configured demo units"', self.consistency)

    def test_evidence_icon_tracks_actual_evidence_state(self) -> None:
        self.assertIn('<span id="gate-evidence-icon" class="gate-icon">✓</span>', self.index)
        self.assertIn('classList.toggle("gate-caution", evidenceWarning)', self.consistency)
        self.assertIn('evidenceWarning ? "!" : "✓"', self.consistency)
        self.assertIn('evidenceText.includes("require")', self.consistency)
        self.assertIn('evidenceText.includes("blocked")', self.consistency)

    def test_hero_and_cta_polish_do_not_add_external_font_dependency(self) -> None:
        self.assertIn("clamp(2.5rem, 4.6vw, 4.2rem)", self.polish_css)
        self.assertIn("letter-spacing: -0.045em", self.polish_css)
        self.assertIn(".primary-action:visited", self.polish_css)
        self.assertIn("color: var(--text)", self.polish_css)
        self.assertIn("font-family: Inter", self.css)
        self.assertIn("Sandbox checksum", self.index)
        self.assertIn("lab-checksum:", self.app)
        self.assertIn("Simulated requests", self.index)
        self.assertIn("Unique authorities", self.index)
        self.assertNotIn('Mission identity</span><strong id="mission-fingerprint', self.index)
        self.assertNotIn("@import", self.combined)
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
        self.assertIn("@media (max-width: 620px)", self.polish_css)
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', self.app)
        self.assertIn('behavior: prefersReducedMotion ? "auto" : "smooth"', self.app)


if __name__ == "__main__":
    unittest.main()
