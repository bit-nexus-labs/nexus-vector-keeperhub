from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class _MissionControlInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.preset_steps: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(str(attributes["id"]))
        if tag == "button" and attributes.get("data-step") is not None:
            self.preset_steps.append(int(str(attributes["data-step"])))


class MissionControlUITests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.css = (FRONTEND / "styles.css").read_text(encoding="utf-8")
        self.app = (FRONTEND / "app.js").read_text(encoding="utf-8")

    def test_judge_dilemma_black_box_and_treasury_controls_exist(self) -> None:
        inventory = _MissionControlInventory()
        inventory.feed(self.index)
        required = {
            "panic-retry",
            "safe-recovery",
            "decision-stage",
            "decision-budget",
            "decision-duplicate",
            "decision-route",
            "open-black-box",
            "incident-presets",
            "gate-identity",
            "gate-budget",
            "gate-attempt",
            "gate-evidence",
            "gate-continuation",
            "treasury-decision",
        }
        self.assertTrue(required.issubset(inventory.ids), required - inventory.ids)
        self.assertEqual(inventory.preset_steps, [2, 3, 4])

    def test_counterfactual_and_replay_claims_are_unambiguous(self) -> None:
        combined = "\n".join((self.index, self.app))
        self.assertIn("COUNTERFACTUAL", self.index)
        self.assertIn("counterfactual risk projection, not an executed transaction", self.index)
        self.assertIn("curated replay classification, not proof that funds moved", self.index)
        self.assertIn("Counterfactual — not an executed transaction", self.app)
        self.assertIn("Nexus recovery protocol — curated replay", self.app)
        self.assertGreaterEqual(self.index.count("NO LIVE TRANSACTION"), 2)

    def test_unsafe_path_is_projection_and_safe_path_is_exact_classification(self) -> None:
        self.assertIn("40 / 30 projected", self.app)
        self.assertIn("1 duplicate payout risk", self.app)
        self.assertIn("30 / 30 classified", self.app)
        self.assertIn("0 duplicate authorizations", self.app)
        self.assertIn("skip · execute · reconcile", self.app)
        self.assertIn("1 SKIP · 1 EXECUTE · 1 RECONCILE", self.index)

    def test_no_unsupported_runtime_or_marketing_claims(self) -> None:
        combined = "\n".join((self.index, self.app, self.css))
        forbidden = (
            "DOUBLE SPEND",
            "MISSION ACCOMPLISHED",
            "USDC",
            "100% network chaos",
            "90% connection",
            "Tempo Atomic",
            "Mark: SUCCESS",
            "Leo: SUCCESS",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase, combined, phrase)

        # JavaScript template literals legitimately contain `${...}`. Reject
        # only user-facing currency claims instead of banning the `$` token.
        self.assertNotIn("$30", combined)
        self.assertNotIn("$40", combined)
        self.assertNotRegex(self.index, r"\$\s*\d")

    def test_interactions_are_local_text_only_and_accessible(self) -> None:
        self.assertIn('panicButton.addEventListener("click"', self.app)
        self.assertIn('safeButton.addEventListener("click"', self.app)
        self.assertIn('blackBoxButton.addEventListener("click"', self.app)
        self.assertIn('setAttribute("aria-pressed"', self.app)
        self.assertIn("element.textContent", self.app)
        self.assertIn("replaceChildren", self.app)
        self.assertNotIn("innerHTML", self.app)
        self.assertNotRegex(self.index, r"<(?:audio|video)\b")

    def test_premium_motion_respects_reduced_motion(self) -> None:
        self.assertIn(".telemetry-console", self.css)
        self.assertIn(".decision-button", self.css)
        self.assertIn(".flight-recorder", self.css)
        self.assertIn(".treasury-gate", self.css)
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn("@media (max-width: 620px)", self.css)

    def test_only_curated_incident_presets_are_exposed(self) -> None:
        self.assertNotRegex(self.index, r'type="range"')
        self.assertNotIn("Network Instability", self.index)
        preset_labels = re.findall(r'<button type="button" data-step="[234]">([^<]+)</button>', self.index)
        self.assertEqual(
            preset_labels,
            ["Drop response", "Process restart", "Safe continuation"],
        )


if __name__ == "__main__":
    unittest.main()
