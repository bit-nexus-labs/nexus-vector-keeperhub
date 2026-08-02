# Architecture

Nexus Vector separates durable business intent, external execution, and independent verification. KeeperHub is an execution provider boundary; it is not the authority for Mission identity, retry permission, or proof that the intended recipient was paid.

## Layers

```text
┌─────────────────────────────────────────────────────────────┐
│ Presentation                                                │
│ Static replay UI · strict JSON CLI · sanitized evidence     │
├─────────────────────────────────────────────────────────────┤
│ Application                                                 │
│ Admission · Dispatch · Reconciliation · Continuation · Doctor│
├─────────────────────────────────────────────────────────────┤
│ Domain                                                      │
│ Mission identity · effects · attempts · transition rules    │
├─────────────────────────────────────────────────────────────┤
│ Persistence                                                 │
│ SQLite Mission store · SQLite execution-attempt journal     │
├─────────────────────────────────────────────────────────────┤
│ External ports — not implemented in this repository         │
│ KeeperHub execution adapter · read-only chain verifier      │
└─────────────────────────────────────────────────────────────┘
```

## Mission authority

A Mission is the durable business-level instruction. Its canonical key is derived independently of provider request keys. Every requested economic effect receives one deterministic `effect_id` from immutable economic material.

Changed content under the same Mission identity is a conflict, not an update. Identical admission is idempotent and does not recreate rows or churn revisions.

## Persistence boundaries

### Mission store

The Mission and all canonical effects are created atomically in SQLite before admission returns. Admission advances only through revision-CAS transitions:

```text
RECEIVED → VALIDATED → PERSISTED
```

A restart can resume from `RECEIVED` or `VALIDATED`. A persisted or later Mission never regresses.

### Execution-attempt journal

Each `effect_id` has one canonical attempt identity. Dispatch persists:

```text
PREPARED → IN_FLIGHT
```

before invoking the provider-neutral execution port. A second dispatcher cannot obtain another writer claim. The claim does not expire automatically; a stuck or unknown attempt goes to reconciliation instead of timeout-based resend.

Mission and attempt stores are deliberately separate in the hackathon MVP. That reduces migration risk to the stable Mission store. Cross-store recovery is therefore explicit rather than pretending to provide one distributed transaction.

## Crash ordering

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

Only a future adapter that can prove rejection before any economic effect may return a final rejection.

## Continuation planning

For each canonical effect, the planner chooses exactly one class:

- `SKIP_VERIFIED` — independently confirmed and never resend;
- `EXECUTE_MISSING` — strictly planned with no possible prior economic effect;
- `RECONCILE_REQUIRED` — in-flight, acknowledged, submitted, or unknown;
- `MANUAL_REVIEW` — contradiction, terminal failure, or invalid durable relationship.

The four amount partitions must sum exactly to the immutable Mission total.

## Execution Doctor

The Doctor is a read-only policy engine over the continuation plan plus explicit sanitized provider/chain observations. It returns per-effect diagnosis codes and one conservative overall next action. It cannot mutate product state or call an external service.

## External integration boundary

A future KeeperHub adapter may translate provider-neutral execution attempts into documented KeeperHub requests and observations. It must not:

- derive or replace Mission/effect identity;
- decide that ambiguity means failure;
- authorize a new attempt for an unknown effect;
- treat provider acceptance as recipient-payment proof;
- bypass independent event verification;
- access mainnet under the current project policy.

Authenticated wallet readiness, a controlled testnet transaction, and public explorer evidence remain pending runtime gates.
