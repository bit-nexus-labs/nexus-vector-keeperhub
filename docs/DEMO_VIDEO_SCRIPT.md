# Demo Video Script — Mission-Safe Agent Payments

Target length: **90–120 seconds**. Record the static replay UI from `frontend/index.html`. Every frame that uses curated data must keep `REPLAY · SANITIZED` and `NO LIVE TRANSACTION` visible or verbally disclosed.

## 0:00–0:10 — Hook

**Screen:** Hero and safety badge.

**Voiceover:**

> An agent can retry after a timeout or restart. Money cannot. Nexus Vector gives every business Mission and payment effect durable identity, so uncertainty never becomes a duplicate payment.

## 0:10–0:25 — The Mission

**Screen:** Simple view, step 1.

**Voiceover:**

> This curated Mission contains three ten-unit effects: Anna, Mark, and Leo. The Mission and all three effects are persisted before any execution is eligible.

## 0:25–0:42 — The dangerous failure

**Screen:** Advance to `Attempt persisted first`, then `Response lost`.

**Voiceover:**

> Before the external port is called, Nexus Vector persists one canonical attempt. Then the response is lost. A conventional agent may retry. Nexus Vector records execution unknown and blocks a blind resend.

## 0:42–0:58 — Restart and independent verification

**Screen:** Advance to `Restart + independent verification`.

**Voiceover:**

> After restart, Nexus Vector reconciles from independent evidence. Anna is confirmed and becomes permanently skipped. The system does not trust provider acceptance alone as proof of payment.

## 0:58–1:15 — Safe continuation

**Screen:** Final replay step and effect cards.

**Voiceover:**

> The result is deterministic: Anna—skip ten. Mark—missing ten, eligible only after policy gates. Leo—unknown ten, reconcile first. Every unit is classified once: ten plus ten plus ten equals the immutable Mission total of thirty.

## 1:15–1:35 — Technical proof

**Screen:** Technical view.

**Voiceover:**

> Under the interface are separate Mission, effect, and execution-attempt state machines; SQLite durability; revision-CAS concurrency; restart recovery; and a Doctor that returns one conservative next action.

## 1:35–1:50 — Evidence boundary

**Screen:** Evidence view.

**Voiceover:**

> The public evidence bundle hashes this replay and separates offline-verified claims from pending runtime evidence. This recording uses sanitized replay data and does not claim a live transaction.

## 1:50–2:00 — Close

**Screen:** Return to hero.

**Voiceover:**

> Nexus Vector: the agent can retry. The money cannot duplicate.

## Recording checklist

- no browser bookmarks, account avatar, API key, wallet extension, local private path, notifications, chat IDs, balances, raw provider IDs, or real recipients;
- use 1080p landscape for the primary export;
- export a second backup copy before upload;
- verify audio, text readability, tab transitions, and the safety disclosure;
- after a real testnet run, add only a separately reviewed final evidence segment; do not retrofit a replay frame as live proof.
