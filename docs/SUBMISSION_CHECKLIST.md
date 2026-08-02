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
- [ ] Simple, Technical, and Evidence views work on desktop and mobile.
- [ ] `REPLAY · SANITIZED` and `NO LIVE TRANSACTION` are visible.
- [ ] No wallet extension, account avatar, bookmarks, notification, private path, or browser console error appears.
- [ ] The deployed bytes match the reviewed repository version.

## Runtime evidence

- [ ] KeeperHub wallet readiness is confirmed through an official surface.
- [ ] One exact action sheet is approved privately.
- [ ] Maximum provider calls = 1 and maximum broadcasts = 1.
- [ ] The durable Mission and canonical attempt exist before the call.
- [ ] An ambiguous result becomes `EXECUTION_UNKNOWN` with no retry.
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
