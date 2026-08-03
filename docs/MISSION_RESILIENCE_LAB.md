# Mission Resilience Lab

## Product purpose

Mission Resilience Lab is an interactive, dependency-free product surface for exploring how autonomous payment Missions behave under retries, ambiguous provider outcomes, concurrent submissions, payload conflicts, and process restarts.

Its purpose is to expose Nexus Vector's safety model as an understandable workflow:

```text
BUILD
  → STRESS
  → RECOVER
  → VERIFY
```

The lab is a local deterministic sandbox. It does not call KeeperHub, access a wallet, sign data, broadcast a transaction, or claim that funds moved.

## Mission model

A Mission contains from one to ten canonical effects in the public sandbox:

```text
Mission
  └── effects[1..10]
```

Each effect is independently classified. Equal amounts do not make two effects interchangeable. Canonical runtime identity conceptually binds:

```text
Mission context
recipient
asset
integer amount
canonical effect identity
```

The public sandbox uses recipient aliases and demo-unit integers only. Real addresses, token contracts, balances, provider references, transaction hashes, and credentials are excluded.

## Mission Builder

Before persistence, the user may:

- add or remove effects within the `1..10` limit;
- edit a sanitized recipient alias;
- edit a positive integer demo-unit amount;
- load one of the product presets;
- reset the local session.

After `Persist Mission`:

- the effect set becomes immutable for that session;
- aliases and amounts are locked;
- a deterministic local sandbox checksum is displayed;
- mutation requires a new Mission version;
- failure scenarios become available.

The sandbox checksum is a compact UI consistency marker. It is not the cryptographic canonical Mission identity used by the runtime core.

The UI lock models the domain rule that a changed economic payload must not reuse an existing canonical effect identity.

## Failure scenarios

### Drop provider response

Models a request that may have reached the provider while the client received no trustworthy result.

Safe classification:

```text
EXECUTION_UNKNOWN
→ RECONCILE_REQUIRED
→ no new economic authority
```

### Submit duplicate request

Models concurrent pressure on the same canonical effect.

Safe classification:

```text
one canonical writer
→ duplicate suppressed
→ one canonical authority
→ original attempt remains IN_FLIGHT
→ RECONCILE_REQUIRED before continuation
```

Duplicate suppression does not prove that a transfer completed and must never be projected directly to `CHAIN_CONFIRMED`.

### Restart the agent

Models process-memory loss after an effect crossed the provider boundary.

Safe recovery reads durable Mission, attempt, and provider-reference state. The process epoch changes, while canonical economic identity does not.

### Mutate the amount

Models reuse of an existing identity with changed economic content.

Safe classification:

```text
fingerprint mismatch
→ conflict
→ manual review
→ no continuation authority
```

### Retry the full Mission

Models a naive batch retry after partial completion.

Safe continuation is calculated per effect:

```text
VERIFIED → SKIP_VERIFIED
MISSING → EXECUTE_MISSING
UNKNOWN → RECONCILE_REQUIRED
CONFLICT → MANUAL_REVIEW
```

The Mission is not treated as one opaque resendable request.

## Recovery Console

The Recovery Console displays four amount partitions:

- Mission total;
- verified and permanently skipped;
- missing and potentially eligible;
- unknown or blocked and therefore not resendable.

The invariant is:

```text
skipped + executable + unresolved + review = immutable Mission total
```

No effect may appear in more than one partition.

The counterfactual route projects what a blind retry could expose. It is explicitly labeled as a non-executed risk model. The Nexus Vector route displays deterministic continuation only and does not grant live transaction authority.

## Execution Black Box

The black box visualizes durable recovery concepts:

- Mission validation and persistence;
- controlled failure selection;
- process epoch changes;
- unknown-outcome classification;
- duplicate suppression;
- fingerprint conflict;
- per-effect repartitioning.

Displayed simulated-request and unique-authority counters are deterministic sandbox telemetry, not external-call or transaction evidence.

## Runtime boundary

The public browser surface must remain incapable of initiating a KeeperHub action.

Forbidden browser capabilities include:

- KeeperHub organization credentials;
- direct KeeperHub transfer calls;
- wallet connection or wallet signing;
- secret or key storage;
- arbitrary recipient addresses;
- live transaction broadcast;
- automatic retry after ambiguity.

A future verified testnet execution requires a separate operator-controlled backend and action-specific approval. The public Evidence view therefore remains:

```text
PENDING_RUNTIME
No real KeeperHub transaction is claimed.
```

## Verified testnet progression

A future controlled runtime proof may replace the pending state only after all gates pass:

1. authenticated wallet and organization readiness;
2. enabled testnet chain and exact token confirmation;
3. exact recipient, integer amount, fee cap, request identity, and confirmation policy;
4. successful simulation of the exact economic body;
5. maximum one approved broadcast per canonical effect;
6. durable provider reference before provider acknowledgement;
7. independent exact chain-event verification;
8. sanitized public evidence and explicit redaction review.

A timeout, disconnect, lost response, persistence failure, or identity conflict never authorizes a new request key.

## Accessibility and resilience requirements

- keyboard-operable controls;
- visible focus treatment;
- semantic labels for dynamic fields;
- no automatic sound;
- reduced-motion support;
- mobile layouts without horizontal page overflow;
- local assets only;
- no dependency on third-party fonts or runtime scripts;
- text-only DOM updates without dynamic HTML injection.

## Public claim boundary

Allowed:

- the product demonstrates deterministic local Mission recovery classifications;
- Missions may contain variable numbers of effects;
- failure scenarios map to the documented state-machine rules;
- the sandbox cannot move funds;
- runtime KeeperHub evidence is pending.

Not allowed until a controlled run is completed and independently verified:

- a KeeperHub payment completed;
- a recipient received funds;
- a transaction hash proves the sandbox scenario;
- a multi-effect Mission completed live;
- any runtime identifier, wallet, balance, or recipient not captured from the actual reviewed run.
