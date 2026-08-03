# Submission and Internal Freeze Checklist

Use this checklist before replacing any `PENDING_*` value or declaring the project submission-ready.

## Repository

- [ ] `main` is the reviewed source of truth.
- [ ] GitHub CI is green on Python 3.12 and 3.14.
- [ ] Standard-library tests pass from a clean checkout.
- [ ] `tools/verify_public_evidence.py` reports PASS.
- [ ] No open superseded PR is presented as the current implementation.
- [ ] No `.env`, key, credential, wallet material, private receipt, raw provider payload, real recipient data, database, log, or local private path is tracked.
- [ ] Mainnet remains blocked.

## Frontend

- [ ] The deployed URL opens in a clean/incognito browser.
- [ ] Mission Builder, Failure Scenarios, Recovery Console, Treasury Gate, and Evidence views work on desktop and mobile.
- [ ] `SANDBOX REPLAY`, `SANITIZED`, and `NO LIVE TRANSACTION` are visible.
- [ ] Draft state shows `NOT EVALUATED`, `PERSIST FIRST`, and zero execution authority before persistence.
- [ ] Representative persisted and failure scenarios preserve the documented state-machine decisions.
- [ ] No wallet extension, account avatar, bookmarks, notification, private path, or browser console error appears.
- [ ] The deployed bytes match the reviewed repository version.

## Runtime evidence

- [ ] KeeperHub wallet readiness is confirmed through an exact supported surface.
- [ ] Gas and token balances are confirmed through a reviewed source.
- [ ] One exact private action sheet is reviewed and unexpired.
- [ ] Simulation and broadcast have separate action-specific approvals.
- [ ] Maximum simulation POSTs = 1, maximum broadcast POSTs = 1, and maximum mutating calls = 1.
- [ ] Same-key recovery POSTs after ambiguity = 0 until KeeperHub confirms and we review the exact procedure.
- [ ] New request keys after ambiguity = 0.
- [ ] A broadcast-capable command requires `--approve-testnet-write`.
- [ ] The durable Mission, canonical effect, and canonical attempt exist before any provider write.
- [ ] An ambiguous result becomes `EXECUTION_UNKNOWN` with no second broadcast.
- [ ] Exact ERC-20 event fields are independently matched at the approved confirmation threshold.
- [ ] The public explorer URL opens and matches the claimed chain, token, sender, recipient, and integer amount.
- [ ] Public evidence is redacted and separately reviewed.

## Video

- [ ] Primary 1080p landscape export opens and plays completely.
- [ ] Backup export exists.
- [ ] Audio is clear and synchronized.
- [ ] Text is readable at normal playback speed.
- [ ] Replay/live labels match the evidence shown.
- [ ] Video URL opens without authentication.

## DoraHacks submission

- [ ] Repository URL opens.
- [ ] Frontend URL opens.
- [ ] Video URL opens.
- [ ] Public evidence URL opens.
- [ ] Exact public explorer URL opens.
- [ ] Project description matches the current runtime evidence and makes no unsupported claim.
- [ ] All `PENDING_*` placeholders are removed only after their links pass verification.

## Freeze

- [ ] Final clean-install verification completed before 2026-08-10 20:00 Europe/Kyiv.
- [ ] Secret and privacy scan completed.
- [ ] No new feature work begins after freeze; only submission-blocking corrections are allowed.
