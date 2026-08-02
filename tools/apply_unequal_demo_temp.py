from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_exact(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"MISSING replacement in {path}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# Frontend replay values and evidence fingerprints.
replay = ROOT / "frontend/replay/mission-safe-30.js"
text = replay.read_text(encoding="utf-8")
text = text.replace(
    'amountBaseUnits: 10\n      }),\n      Object.freeze({\n        effectId: "e_44aa90e97b8a20f5284b27e03dbbdb17"',
    'amountBaseUnits: 12\n      }),\n      Object.freeze({\n        effectId: "e_44aa90e97b8a20f5284b27e03dbbdb17"',
    1,
)
text = text.replace(
    'amountBaseUnits: 10\n      }),\n      Object.freeze({\n        effectId: "e_b745469247aab23df29106940f1fa1a4"',
    'amountBaseUnits: 7\n      }),\n      Object.freeze({\n        effectId: "e_b745469247aab23df29106940f1fa1a4"',
    1,
)
text = text.replace(
    'amountBaseUnits: 10\n      })\n    ])',
    'amountBaseUnits: 11\n      })\n    ])',
    1,
)
text = text.replace(
    "amounts: Object.freeze({ skipped: 0, executable: 20, unresolved: 10 })",
    "amounts: Object.freeze({ skipped: 0, executable: 18, unresolved: 12 })",
)
text = text.replace(
    "amounts: Object.freeze({ skipped: 10, executable: 10, unresolved: 10 })",
    "amounts: Object.freeze({ skipped: 12, executable: 7, unresolved: 11 })",
)
anna_fp = hashlib.sha256(
    b"nexus-vector.replay.v1|anna|12|CHAIN_CONFIRMED"
).hexdigest()
leo_fp = hashlib.sha256(
    b"nexus-vector.replay.v1|leo|11|EXECUTION_UNKNOWN"
).hexdigest()
text = re.sub(
    r'(evidenceRef: "ev_anna_exact_transfer"[\s\S]*?fingerprint: ")sha256:[0-9a-f]{64}("\n\s*\}\),)',
    rf"\1sha256:{anna_fp}\2",
    text,
    count=1,
)
text = re.sub(
    r'(evidenceRef: "ev_leo_unknown_outcome"[\s\S]*?fingerprint: ")sha256:[0-9a-f]{64}("\n\s*\}\)\n\s*\]\),)',
    rf"\1sha256:{leo_fp}\2",
    text,
    count=1,
)
text = re.sub(
    r'manifestSha256: "sha256:[0-9a-f]{64}"',
    'manifestSha256: "TO_BE_REPLACED"',
    text,
    count=1,
)
manifest_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
text = text.replace(
    'manifestSha256: "TO_BE_REPLACED"',
    f'manifestSha256: "{manifest_hash}"',
)
replay.write_text(text, encoding="utf-8")

# Mission Control UI.
index = ROOT / "frontend/index.html"
replace_exact(index, "<strong>40 / 30</strong>", "<strong>42 / 30</strong>")
replace_exact(
    index,
    '<span>Already verified</span><strong id="amount-skipped">10</strong>',
    '<span>Already verified</span><strong id="amount-skipped">12</strong>',
)
replace_exact(
    index,
    '<span>Missing</span><strong id="amount-executable">10</strong>',
    '<span>Missing</span><strong id="amount-executable">7</strong>',
)
replace_exact(
    index,
    '<span>Unknown</span><strong id="amount-unresolved">10</strong>',
    '<span>Unknown</span><strong id="amount-unresolved">11</strong>',
)
replace_exact(
    index,
    "ALPHA is a counterfactual risk projection, not an executed transaction. BETA is a curated replay classification, not proof that funds moved.",
    "ALPHA projects a duplicate of Anna's 12-unit effect and is not an executed transaction. BETA is a curated replay classification, not proof that funds moved.",
)

app = ROOT / "frontend/app.js"
replace_exact(
    app,
    'text(byId("decision-budget"), "40 / 30 projected");',
    'text(byId("decision-budget"), "42 / 30 projected");',
)
replace_exact(
    app,
    'text(byId("decision-summary"), "One effect is verified and permanently skipped, one remains missing and eligible only after policy gates, and one stays unresolved for reconciliation.");',
    'text(byId("decision-summary"), "Anna—12 units—is verified and permanently skipped. Mark—7 units—remains missing and eligible only after policy gates. Leo—11 units—stays unresolved for reconciliation.");',
)

# README and product documentation.
readme = ROOT / "README.md"
replace_exact(
    readme,
    "deterministic 10 + 10 + 10 continuation planning;",
    "deterministic unequal-amount 12 + 7 + 11 continuation planning;",
)
replace_exact(
    readme,
    "- dependency-free Simple, Technical, and Evidence replay views;",
    "- dependency-free Mission, Treasury Gate, and Evidence replay views;",
)
replace_exact(readme, "## 10 + 10 + 10 replay", "## 12 + 7 + 11 replay")
replace_exact(
    readme,
    "- **Anna — 10:** independently verified and permanently skipped;",
    "- **Anna — 12:** independently verified and permanently skipped;",
)
replace_exact(
    readme,
    "- **Mark — 10:** missing and only a future execution candidate after policy gates;",
    "- **Mark — 7:** missing and only a future execution candidate after policy gates;",
)
replace_exact(
    readme,
    "- **Leo — 10:** execution outcome unknown and therefore reconciliation-required;",
    "- **Leo — 11:** execution outcome unknown and therefore reconciliation-required;",
)
replace_exact(
    readme,
    "py .\\tools\\check_repository_hygiene.py",
    "py .\\tools\\verify_repository_hygiene.py",
)
replace_exact(
    readme,
    "python tools/check_repository_hygiene.py",
    "python tools/verify_repository_hygiene.py",
)
replace_exact(
    readme,
    "- append-only SQLite provider-reference journal;\n- provider `executionId` persisted before `PROVIDER_ACKNOWLEDGED`;",
    "- append-only SQLite provider-reference journal;\n- provider-reference schema and existing-reference guard checked before the provider call;\n- provider `executionId` persisted before `PROVIDER_ACKNOWLEDGED`;",
)

mc = ROOT / "docs/MISSION_CONTROL_UI_REVIEW.md"
replace_exact(
    mc,
    "The safe path preserves the current 10 + 10 + 10 state:",
    "The safe path preserves the current unequal-amount 12 + 7 + 11 state:",
)
replace_exact(
    mc,
    "- 10 verified and skipped;\n- 10 missing and eligible only after policy gates;\n- 10 unresolved and requiring reconciliation;",
    "- 12 verified and skipped;\n- 7 missing and eligible only after policy gates;\n- 11 unresolved and requiring reconciliation;",
)
replace_exact(
    mc,
    "- total 30 classified exactly once.",
    "- total 30 classified exactly once.\n\nThe counterfactual duplicate of Anna's 12-unit effect is shown as 42 / 30 projected exposure. Unequal amounts make it clear that Nexus Vector protects canonical economic effects rather than equal-sized list positions.",
)

runtime = ROOT / "docs/RUNTIME_READINESS.md"
replace_exact(
    runtime,
    "**Product main at review start:** `3e7e355c356197298217fad01fb969619e6cd093`",
    "**Product main at review start:** `795d86f93b110b3d0ff5b6df91bdc9fc39d06d23`",
)
replace_exact(
    runtime,
    "| Duplicate suppression and 10+10+10 continuation | DONE | deterministic planner tests |",
    "| Duplicate suppression and 12+7+11 continuation | DONE | unequal-amount deterministic planner tests |",
)
replace_exact(
    runtime,
    "| Durable KeeperHub `executionId` | OFFLINE VERIFIED | append-only provider-reference journal and crash tests |",
    "| Durable KeeperHub `executionId` | OFFLINE VERIFIED | append-only provider-reference journal and crash tests |\n| Provider-reference preflight hardening | OFFLINE VERIFIED | schema validation and existing-reference guard before provider call |",
)

submission = ROOT / "docs/SUBMISSION_DRAFT.md"
replace_exact(
    submission,
    "In the curated 10 + 10 + 10 replay:",
    "In the curated 12 + 7 + 11 replay:",
)
replace_exact(
    submission,
    "- Anna: independently verified and skipped — 10;",
    "- Anna: independently verified and skipped — 12;",
)
replace_exact(
    submission,
    "- Mark: missing and a future execution candidate — 10;",
    "- Mark: missing and a future execution candidate — 7;",
)
replace_exact(
    submission,
    "- Leo: execution outcome unknown and reconciliation-required — 10;",
    "- Leo: execution outcome unknown and reconciliation-required — 11;",
)
replace_exact(
    submission,
    "- total: exactly 30, classified once with no overlap.",
    "- total: exactly 30, classified once with no overlap; a blind duplicate of Anna's effect is shown only as a 42 / 30 counterfactual projection.",
)
replace_exact(
    submission,
    "- concurrency, restart, lost-response, cross-store crash, duplicate suppression, and 10 + 10 + 10 SQLite integration tests;",
    "- concurrency, restart, lost-response, cross-store crash, duplicate suppression, and unequal-amount 12 + 7 + 11 SQLite integration tests;",
)
replace_exact(
    submission,
    "- Simple, Technical, and Evidence replay views;",
    "- Mission, Treasury Gate, and Evidence replay views;",
)

states = ROOT / "docs/STATE_MACHINES.md"
replace_exact(states, "## 10 + 10 + 10 acceptance matrix", "## 12 + 7 + 11 acceptance matrix")
replace_exact(states, "| Anna · 10 |", "| Anna · 12 |")
replace_exact(states, "| Mark · 10 |", "| Mark · 7 |")
replace_exact(states, "| Leo · 10 |", "| Leo · 11 |")
replace_exact(
    states,
    "skip 10 + execute 10 + reconcile 10 + manual 0 = Mission total 30",
    "skip 12 + execute 7 + reconcile 11 + manual 0 = Mission total 30",
)

# Replace the video script wholesale so recording matches the final UI.
video = ROOT / "docs/DEMO_VIDEO_SCRIPT.md"
video.write_text(
    '''# Demo Video Script — Mission Control Recovery

Target length: **100–115 seconds**. Record the static replay UI from `frontend/index.html` at 1080p landscape. Every curated frame must keep `REPLAY · SANITIZED` and `NO LIVE TRANSACTION` visible or verbally disclosed.

## 0:00–0:09 — Hook

**Screen:** Mission Control hero and incident telemetry.

**Voiceover:**

> An autonomous agent can retry after a timeout. Money cannot. Nexus Vector gives every business Mission and economic effect durable identity, so uncertainty never becomes permission to pay twice.

## 0:09–0:24 — The Judge's Dilemma

**Screen:** The two decision cards. Keep both choices visible.

**Voiceover:**

> The response vanished after Anna's twelve-unit effect may already have executed. A normal agent may assume failure and send again. The judge now chooses: panic retry, or safe recovery.

## 0:24–0:35 — Counterfactual danger

**Screen:** Select `Panic retry`; show the highlighted ALPHA timeline and `42 / 30 projected`.

**Voiceover:**

> Panic retry is a counterfactual only. Repeating Anna's twelve-unit effect would raise projected exposure from thirty to forty-two. Nexus Vector never executes this branch.

## 0:35–0:51 — Safe recovery and Black Box

**Screen:** Select `Safe recovery`, then `Open black box & reconcile`.

**Voiceover:**

> Safe recovery opens the durable black box: the Mission, canonical attempt, and provider reference survive restart. An unknown outcome stays unknown, and blind resend remains denied.

## 0:51–1:10 — Unequal effects, exact partition

**Screen:** Use `Drop response`, `Process restart`, and `Safe continuation`; finish on the three effect cards.

**Voiceover:**

> Independent evidence verifies Anna, so twelve units are skipped forever. Mark's seven units are still missing and only eligible after policy gates. Leo's eleven units remain unresolved and must be reconciled. Twelve plus seven plus eleven equals the immutable Mission total of thirty—classified exactly once.

## 1:10–1:28 — Zero-Trust Treasury Gate

**Screen:** Open `Treasury gate`; show identity, budget, durable attempt, evidence policy, and continuation checks.

**Voiceover:**

> The treasury gate validates identity, immutable budget, durable attempt state, evidence policy, and continuation authority. Its decision is deterministic: one skip, one execute candidate, one reconcile. Classification is not a live payment authorization.

## 1:28–1:42 — Evidence boundary

**Screen:** Open `Evidence`; pause on the manifest hash and sanitized records.

**Voiceover:**

> The public evidence bundle hashes the exact replay bytes and separates offline-verified guarantees from pending runtime evidence. This recording is REPLAY · SANITIZED and does not claim a live transaction.

## 1:42–1:52 — Close

**Screen:** Return to the hero and tagline.

**Voiceover:**

> Nexus Vector: the agent can retry. The money cannot duplicate.

## Recording checklist

- no browser bookmarks, account avatar, API key, wallet extension, local private path, notifications, chat IDs, balances, raw provider IDs, or real recipients;
- use 1080p landscape for the primary export;
- export a second backup copy before upload;
- verify audio, text readability, decision transitions, tabs, and the replay disclosure;
- do not show the counterfactual branch as an observed event;
- after a real testnet run, add only a separately reviewed evidence segment; never retrofit a replay frame as live proof.
''',
    encoding="utf-8",
)

# Core scenario tests.
planner = ROOT / "tests/test_continuation_planner.py"
replace_exact(
    planner,
    "def test_10_10_10_plan_skips_paid_executes_missing_and_reconciles_unknown(",
    "def test_12_7_11_plan_skips_paid_executes_missing_and_reconciles_unknown(",
)
ptext = planner.read_text(encoding="utf-8")
start = ptext.index(
    "    def test_12_7_11_plan_skips_paid_executes_missing_and_reconciles_unknown("
)
end = ptext.index(
    "    def test_new_request_key_for_same_effect_never_creates_second_attempt",
    start,
)
block = ptext[start:end]
block = block.replace(
    "            10,\n            EffectState.CHAIN_CONFIRMED,",
    "            12,\n            EffectState.CHAIN_CONFIRMED,",
    1,
)
block = block.replace(
    "            10,\n            EffectState.PLANNED,",
    "            7,\n            EffectState.PLANNED,",
    1,
)
block = block.replace(
    "            10,\n            EffectState.EXECUTION_UNKNOWN,",
    "            11,\n            EffectState.EXECUTION_UNKNOWN,",
    1,
)
block = block.replace(
    "self.assertEqual(plan.skipped_amount_base_units, 10)",
    "self.assertEqual(plan.skipped_amount_base_units, 12)",
)
block = block.replace(
    "self.assertEqual(plan.executable_amount_base_units, 10)",
    "self.assertEqual(plan.executable_amount_base_units, 7)",
)
block = block.replace(
    "self.assertEqual(plan.unresolved_amount_base_units, 10)",
    "self.assertEqual(plan.unresolved_amount_base_units, 11)",
)
planner.write_text(ptext[:start] + block + ptext[end:], encoding="utf-8")

sqlite_test = ROOT / "tests/test_continuation_planner_sqlite.py"
replace_exact(
    sqlite_test,
    "def test_real_10_10_10_state_partitions_exactly_once(self) -> None:",
    "def test_real_12_7_11_state_partitions_exactly_once(self) -> None:",
)
replace_exact(
    sqlite_test,
    'mission_ref="continuation-10-10-10",',
    'mission_ref="continuation-12-7-11",',
)
replace_exact(
    sqlite_test,
    '''                effects=tuple(
                    EffectRequest(
                        effect_ref=effect_ref,
                        recipient=RECIPIENTS[effect_ref],
                        amount_base_units=10,
                    )
                    for effect_ref in ("anna", "mark", "leo")
                ),''',
    '''                effects=tuple(
                    EffectRequest(
                        effect_ref=effect_ref,
                        recipient=RECIPIENTS[effect_ref],
                        amount_base_units={"anna": 12, "mark": 7, "leo": 11}[effect_ref],
                    )
                    for effect_ref in ("anna", "mark", "leo")
                ),''',
)
replace_exact(
    sqlite_test,
    "self.assertEqual(continuation.skipped_amount_base_units, 10)",
    "self.assertEqual(continuation.skipped_amount_base_units, 12)",
)
replace_exact(
    sqlite_test,
    "self.assertEqual(continuation.executable_amount_base_units, 10)",
    "self.assertEqual(continuation.executable_amount_base_units, 7)",
)
replace_exact(
    sqlite_test,
    "self.assertEqual(continuation.unresolved_amount_base_units, 10)",
    "self.assertEqual(continuation.unresolved_amount_base_units, 11)",
)

doctor = ROOT / "tests/test_execution_doctor.py"
replace_exact(
    doctor,
    "def test_10_10_10_prioritizes_reconciliation(self):",
    "def test_12_7_11_prioritizes_reconciliation(self):",
)
dtext = doctor.read_text(encoding="utf-8")
start = dtext.index("    def test_12_7_11_prioritizes_reconciliation(self):")
end = dtext.index("    def test_local_verified_chain_not_found_is_manual", start)
block = dtext[start:end]
block = block.replace('decision("anna", "2", 10,', 'decision("anna", "2", 12,', 1)
block = block.replace('decision("mark", "3", 10,', 'decision("mark", "3", 7,', 1)
block = block.replace('decision("leo", "4", 10,', 'decision("leo", "4", 11,', 1)
doctor.write_text(dtext[:start] + block + dtext[end:], encoding="utf-8")

static_test = ROOT / "tests/test_static_replay_ui.py"
replace_exact(
    static_test,
    "def test_replay_has_exact_10_10_10_partition(self) -> None:",
    "def test_replay_has_exact_12_7_11_partition(self) -> None:",
)
replace_exact(
    static_test,
    '        self.assertIn("skipped: 10, executable: 10, unresolved: 10", self.replay)',
    '''        self.assertEqual(
            re.findall(r"amountBaseUnits: (\\d+)", self.replay)[:3],
            ["12", "7", "11"],
        )
        self.assertIn("skipped: 12, executable: 7, unresolved: 11", self.replay)
        self.assertIn("skipped: 0, executable: 18, unresolved: 12", self.replay)''',
)

mc_test = ROOT / "tests/test_mission_control_ui.py"
replace_exact(
    mc_test,
    'self.assertIn("counterfactual risk projection, not an executed transaction", self.index)',
    'self.assertIn("projects a duplicate of Anna\'s 12-unit effect and is not an executed transaction", self.index)',
)
replace_exact(
    mc_test,
    'self.assertIn("40 / 30 projected", self.app)',
    'self.assertIn("42 / 30 projected", self.app)',
)
replace_exact(
    mc_test,
    '        self.assertIn("1 duplicate payout risk", self.app)',
    '''        self.assertIn("1 duplicate payout risk", self.app)
        self.assertIn("42 / 30", self.index)
        self.assertNotIn("40 / 30", self.index + self.app)
        self.assertIn("Anna—12 units", self.app)
        self.assertIn("Mark—7 units", self.app)
        self.assertIn("Leo—11 units", self.app)''',
)
replace_exact(
    mc_test,
    '        self.assertNotIn("$40", combined)',
    '        self.assertNotIn("$40", combined)\n        self.assertNotIn("$42", combined)',
)

# Public evidence claim update and PR27 hardening claim.
manifest_path = ROOT / "evidence/public_manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["generated_at_utc"] = "2026-08-02T22:05:00Z"
for claim in manifest["claims"]:
    if claim["claim_id"] == "deterministic_partial_continuation":
        claim["evidence"] = (
            "The unequal 12 + 7 + 11 case partitions exactly into "
            "skip 12, execute 7 and reconcile 11."
        )
    if claim["claim_id"] == "static_replay_ui":
        claim["evidence"] = (
            "Dependency-free Mission, Treasury Gate and Evidence views render "
            "curated replay data only; exact current bytes are bound by the "
            "artifact digests below."
        )
if not any(
    claim["claim_id"] == "provider_reference_preflight_hardening"
    for claim in manifest["claims"]
):
    index = next(
        position
        for position, claim in enumerate(manifest["claims"])
        if claim["claim_id"] == "durable_provider_reference"
    ) + 1
    manifest["claims"].insert(
        index,
        {
            "claim_id": "provider_reference_preflight_hardening",
            "status": "OFFLINE_VERIFIED",
            "merge_commit": "795d86f93b110b3d0ff5b6df91bdc9fc39d06d23",
            "evidence": (
                "The provider-reference schema and existing-reference guard "
                "are checked before any provider call; known preflight failure "
                "produces zero provider calls."
            ),
        },
    )
for artifact in manifest["artifacts"]:
    target = ROOT / artifact["path"]
    artifact["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

print("UNEQUAL_DEMO_PATCH: PASS")
print("REPLAY_MANIFEST_HASH:", manifest_hash)
