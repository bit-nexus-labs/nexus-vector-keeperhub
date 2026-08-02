from __future__ import annotations

import hashlib
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class _HTMLInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(str(attributes["id"]))
        if tag == "script" and attributes.get("src"):
            self.scripts.append(str(attributes["src"]))
        if tag == "link" and attributes.get("rel") == "stylesheet" and attributes.get("href"):
            self.stylesheets.append(str(attributes["href"]))


class StaticReplayUITests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.css = (FRONTEND / "styles.css").read_text(encoding="utf-8")
        self.app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.replay = (FRONTEND / "replay" / "mission-safe-30.js").read_text(encoding="utf-8")

    def test_all_declared_assets_are_local_and_exist(self) -> None:
        inventory = _HTMLInventory()
        inventory.feed(self.index)
        for relative_path in inventory.scripts + inventory.stylesheets:
            self.assertFalse(relative_path.startswith(("http://", "https://", "//")))
            self.assertTrue((FRONTEND / relative_path).is_file(), relative_path)

    def test_required_views_and_controls_exist(self) -> None:
        inventory = _HTMLInventory()
        inventory.feed(self.index)
        required = {
            "view-simple", "view-technical", "view-evidence", "previous-step",
            "next-step", "timeline", "effect-cards", "technical-table",
            "evidence-list", "manifest-hash",
        }
        self.assertTrue(required.issubset(inventory.ids), required - inventory.ids)

    def test_public_boundary_is_explicit(self) -> None:
        self.assertGreaterEqual(self.index.count("NO LIVE TRANSACTION"), 2)
        self.assertIn("REPLAY · SANITIZED", self.index)
        self.assertIn("not proof that funds moved", self.index)

    def test_no_network_wallet_secret_or_dynamic_html_capability(self) -> None:
        combined = "\n".join((self.index, self.app, self.replay, self.css))
        forbidden = (
            "fetch(", "XMLHttpRequest", "WebSocket", "EventSource",
            "navigator.sendBeacon", "window.ethereum", "walletconnect",
            "privateKey", "apiKey", "localStorage", "sessionStorage",
            "document.cookie", "eval(", "innerHTML",
        )
        for token in forbidden:
            self.assertNotIn(token, combined, token)
        self.assertNotRegex(self.index, r"https?://")

    def test_dom_rendering_uses_text_content(self) -> None:
        self.assertIn("element.textContent", self.app)
        self.assertIn("replaceChildren", self.app)
        self.assertNotIn("innerHTML", self.app)

    def test_replay_has_exact_12_7_11_partition(self) -> None:
        effect_refs = re.findall(r'effectRef: "(anna|mark|leo)"', self.replay)
        self.assertEqual(effect_refs[:3], ["anna", "mark", "leo"])
        self.assertEqual(len(set(effect_refs[:3])), 3)
        self.assertIn("totalAmountBaseUnits: 30", self.replay)
        self.assertEqual(
            re.findall(r"amountBaseUnits: (\d+)", self.replay)[:3],
            ["12", "7", "11"],
        )
        self.assertIn("skipped: 12, executable: 7, unresolved: 11", self.replay)
        self.assertIn("skipped: 0, executable: 18, unresolved: 12", self.replay)
        self.assertIn('continuation: "SKIP_VERIFIED"', self.replay)
        self.assertIn('continuation: "EXECUTE_MISSING"', self.replay)
        self.assertIn('continuation: "RECONCILE_REQUIRED"', self.replay)

    def test_replay_contains_five_ordered_steps(self) -> None:
        step_ids = re.findall(r'\n\s*id: "([a-z-]+)",\n\s*title:', self.replay)
        self.assertEqual(
            step_ids,
            ["durable-admission", "anna-in-flight", "lost-response", "restart-verification", "safe-continuation"],
        )

    def test_manifest_hash_matches_public_replay_bytes(self) -> None:
        match = re.search(r'manifestSha256: "(sha256:[0-9a-f]{64})"', self.replay)
        self.assertIsNotNone(match)
        assert match is not None
        normalized = self.replay.replace(match.group(0), 'manifestSha256: "TO_BE_REPLACED"')
        expected = "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        self.assertEqual(match.group(1), expected)

    def test_responsive_and_reduced_motion_rules_exist(self) -> None:
        self.assertIn("@media (max-width: 620px)", self.css)
        self.assertIn("prefers-reduced-motion", self.css)


if __name__ == "__main__":
    unittest.main()
