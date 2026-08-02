# Mission Control UI Review

## Product objective

Make the duplicate-payment failure mode understandable in under ten seconds without weakening claim accuracy or turning a treasury-safety product into an ungrounded game.

The approved presentation combines:

- Mission Control as the visual shell;
- the Judge's Dilemma as the opening interaction;
- a Black Box flight recorder as the restart/reconciliation metaphor;
- two compact counterfactual timelines;
- a Zero-Trust Treasury Gate for technical depth;
- the existing five-step deterministic replay and Evidence view.

## Judge's Dilemma

The first interaction offers two local-only choices:

- `Panic retry` displays a counterfactual duplicate-payout risk projection;
- `Safe recovery` displays the actual curated Nexus Vector classification.

Neither choice performs a request, reads a credential, accesses a wallet, signs data, broadcasts a transaction, or moves funds.

The unsafe path is always labeled counterfactual and not executed. It must never be presented as a blockchain double spend or as observed runtime evidence.

## Exact replay claim

The safe path preserves the current 10 + 10 + 10 state:

- 10 verified and skipped;
- 10 missing and eligible only after policy gates;
- 10 unresolved and requiring reconciliation;
- total 30 classified exactly once.

It does not claim that Mark or Leo were paid, that 30 token units moved, or that a KeeperHub transaction exists.

## Visual hierarchy

1. Problem and promise.
2. Incident telemetry and signal loss.
3. Judge choice and immediate outcome comparison.
4. Black Box recovery sequence.
5. Deterministic Mission replay.
6. Zero-Trust Treasury Gate.
7. Sanitized evidence.

The visual system uses restrained aerospace telemetry rather than continuous alarm effects. Red is reserved for counterfactual risk, amber for uncertainty, cyan for observed/reviewable state, and green for independently verified or safely classified state.

## Interaction boundary

The page remains dependency-free and static:

- no network APIs;
- no external fonts, analytics, CDN, RPC, wallet or signer;
- no local or session storage;
- no dynamic HTML injection;
- all generated text uses `textContent`;
- no automatic audio;
- reduced-motion support is mandatory;
- only three curated incident presets are exposed.

## Evidence boundary

The public evidence manifest binds the exact current UI bytes. The original static replay implementation commit remains the functional baseline; after merge, the UI claim commit should be synchronized to the final merge commit in a separate exact evidence update if required by freeze review.

The page remains explicitly:

```text
REPLAY / SANITIZED / NO LIVE TRANSACTION
```

## Review gates

Before merge:

1. full CI green on Python 3.12 and 3.14;
2. repository hygiene and public evidence verification pass;
3. exact HTML/CSS/JavaScript diff review;
4. desktop and mobile browser QA;
5. keyboard and reduced-motion QA;
6. no unsupported token, payment-success, double-spend or live-transaction claims;
7. explicit owner approval for the exact PR head.

Deployment and video remain separate gates after the UI merge.
