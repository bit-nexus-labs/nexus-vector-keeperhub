# Architecture

Nexus Vector separates durable business intent, provider execution, provider observation, and independent chain verification. KeeperHub is an execution boundary; it is not the authority for Mission identity, retry permission, or proof that the intended recipient was paid.

## Layers

```text
┌──────────────────────────────────────────────────────────────────┐
│ Presentation                                                     │
│ Static replay UI · strict JSON CLI · sanitized evidence          │
├──────────────────────────────────────────────────────────────────┤
│ Application                                                      │
│ Admission · Dispatch · Reconciliation · Continuation · Doctor    │
├──────────────────────────────────────────────────────────────────┤
│ Provider integration                                             │
│ KeeperHub intent · simulation/broadcast mapper · status observer │
│ Bounded HTTPS transport · wallet/chains readiness parsers        │
├──────────────────────────────────────────────────────────────────┤
│ Domain                                                           │
│ Mission identity · effects · attempts · provider references      │
├──────────────────────────────────────────────────────────────────┤
│ Persistence                                                      │
│ Mission store · attempt journal · provider-reference journal     │
├──────────────────────────────────────────────────────────────────┤
│ Independent evidence                                             │
│ Exact chain/token/sender/recipient/base-unit observation port    │
└──────────────────────────────────────────────────────────────────┘
```

The HTTPS transport is implemented but not invoked by repository tests or documentation builds. Credentials are injected explicitly at runtime; the code does not read environment variables, `.env`, keyrings, browser sessions, or local credential managers.

## Mission authority

A Mission is the durable business-level instruction. Its canonical key is derived independently of provider request keys. Every requested economic effect receives one deterministic `effect_id` from immutable economic material.

Changed content under the same Mission identity is a conflict, not an update. Identical admission is idempotent and does not recreate rows or churn revisions.

## Persistence boundaries

### Mission store

The Mission and all canonical effects are created atomically in SQLite before admission returns:

```text
RECEIVED → VALIDATED → PERSISTED
```

Restart resumes from `RECEIVED` or `VALIDATED`. A persisted or later Mission never regresses.

### Execution-attempt journal

Each `effect_id` has one canonical attempt identity. Dispatch persists:

```text
PREPARED → IN_FLIGHT
```

before invoking any execution port. A second dispatcher cannot obtain another writer claim. The claim does not expire automatically; a stuck or unknown attempt goes to reconciliation instead of timeout-based resend.

### Provider-reference journal

KeeperHub returns an `executionId` after accepting a Direct Execution request. Nexus Vector stores it in a separate immutable SQLite journal keyed by canonical `attempt_id`.

```text
IN_FLIGHT
  → provider call
  → durable provider reference
  → PROVIDER_ACKNOWLEDGED
```

A crash after the provider reference is written but before ACK leaves a safe recovery state: the attempt is still a recovery candidate and the exact provider execution can be queried without issuing another transfer.

The sidecar journal avoids a deadline-sensitive migration of the proven attempt schema. The tradeoff is an additional SQLite file; consolidation is a post-hackathon option, not a prerequisite for safety.

## KeeperHub Direct Execution path

### Immutable intent and fingerprint

The provider adapter does not reconstruct economic fields from prose. It receives an immutable transfer intent containing:

- chain ID;
- token contract and decimals;
- recipient;
- integer base-unit amount;
- optional canonical gas multiplier.

Before simulation it recomputes the durable request fingerprint from that intent and the canonical request key. Any changed chain, token, recipient, amount, decimals, or gas policy is blocked before the transport is called.

### Simulation and broadcast

The exact intended transfer body is simulated first with strict boolean `simulate: true`. The broadcast body must be identical after removing only `simulate`.

The durable request key becomes the one `Idempotency-Key` on the broadcast. The transport performs no automatic retries and follows no redirects.

Structured would-revert simulation is a final no-broadcast rejection. Timeout, rate limit, idempotency conflict/in-progress, malformed response, missing provider reference, or network ambiguity becomes unknown—not permission for a new key or a second POST.

### Provider status

The status observer resolves only from a durable provider reference and accepts the official status vocabulary:

```text
pending · running · completed · failed
```

It honors `X-Poll-Interval-Hint`. Provider `completed` must include a canonical transaction hash and matching HTTPS explorer link, but it still does not mark an effect verified. It only supplies a candidate transaction for independent chain observation.

Provider `failed` is sanitized to a boolean error-presence indicator; raw provider prose is not stored in the observation object.

## Bounded HTTPS boundary

The standard-library transport:

- pins `https://app.keeperhub.com/api`;
- injects an organization key explicitly;
- blocks redirects;
- performs no retries;
- bounds timeout and response size;
- requires JSON content type and valid UTF-8 JSON;
- preserves structured HTTP error status for higher-layer classification;
- sanitizes network errors;
- parses official wallet-readiness and bare-array chain schemas;
- never reads environment, keyring, browser, or process credential state.

The wallet parser confirms `hasWallet`, `isActive`, canonical wallet address, and organization ID. The chain parser preserves `chainId`, `chainType`, `isTestnet`, and `isEnabled`; only enabled EVM testnets are eligible for the current project.

## Independent verification and crash ordering

KeeperHub acceptance, `executionId`, provider `completed`, transaction hash, and explorer link are provider observations. None alone proves the intended economic effect.

When exact independent evidence proves an effect occurred:

1. project `CHAIN_CONFIRMED` into the durable Mission/effect store;
2. then mark the execution attempt `VERIFIED`.

A crash between those writes leaves the economic fact confirmed while the attempt remains a recovery candidate. Restart may repeat read-only verification, but it cannot repeat the payment.

## Unknown outcomes

Timeouts, lost responses, malformed results, forged outcomes, verifier errors, insufficient confirmations, and unresolved observations fail closed.

```text
possible execution + no exact proof
    → EXECUTION_UNKNOWN
    → RECONCILE_REQUIRED
    → never blind resend
```

## Continuation planning

For each canonical effect, the planner chooses exactly one class:

- `SKIP_VERIFIED` — independently confirmed and never resend;
- `EXECUTE_MISSING` — strictly planned with no possible prior economic effect;
- `RECONCILE_REQUIRED` — in-flight, acknowledged, submitted, provider-failed, or unknown;
- `MANUAL_REVIEW` — contradiction, terminal durable conflict, or invalid relationship.

The four amount partitions must sum exactly to the immutable Mission total.

## Execution Doctor

The Doctor is a read-only policy engine over the continuation plan plus explicit sanitized provider/chain observations. It returns per-effect diagnosis codes and one conservative overall next action. It cannot mutate product state or call an external service.

## Remaining runtime gates

The code path is ready through the official REST boundary. Still required before any live claim:

1. local organization API key supplied outside Git and chat;
2. authenticated wallet and balance confirmation;
3. live enabled-testnet chain confirmation;
4. exact private action sheet and transaction-specific approval;
5. one controlled simulation and at most one broadcast;
6. durable status observation and independent exact event verification;
7. sanitized explorer evidence publication;
8. frontend deployment, video recording, and final submission.

Mainnet and blind retry remain blocked.
