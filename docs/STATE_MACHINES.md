# State Machines and Recovery Rules

Nexus Vector keeps Mission, effect, and execution-attempt state separate. This prevents a provider response from silently rewriting durable business truth.

## Mission states

Core admission and recovery progression:

```text
RECEIVED
  → VALIDATED
  → PERSISTED
  → RECONCILING
  → READY_FOR_EXECUTION
  → EXECUTING
  → VERIFYING
  → COMPLETED
```

Fail-closed branches include:

```text
EXECUTION_UNKNOWN
VERIFICATION_FAILED
MANUAL_REVIEW_REQUIRED
BLOCKED
```

`BLOCKED` before `PERSISTED` is not admitted. Later states never regress through admission.

## Effect states

```text
PLANNED
  → RESERVED
  → SUBMITTED
  → CHAIN_CONFIRMED
```

An ambiguous effect may become `EXECUTION_UNKNOWN`. A chain-confirmed effect is immutable evidence for `SKIP_VERIFIED` and must never receive another execution attempt.

## Execution-attempt states

```text
PREPARED
  → IN_FLIGHT
  → PROVIDER_ACKNOWLEDGED
  → VERIFIED
```

Alternative terminal or recovery states:

```text
EXECUTION_UNKNOWN
FAILED_FINAL
```

`FAILED_FINAL` is allowed only for a proved pre-effect rejection. Unknown or malformed provider output is not final failure.

## Restart matrix

| Durable state | Safe restart action | Forbidden action |
|---|---|---|
| no attempt, effect `PLANNED` | evaluate policy gates | infer prior execution |
| attempt `PREPARED` | allow the same canonical dispatch claim | create a new attempt identity |
| attempt `IN_FLIGHT` | reconcile | blind resend |
| attempt acknowledged/submitted | reconcile and verify | assume recipient paid |
| attempt/effect unknown | reconcile or manual review | classify as failed from timeout alone |
| effect confirmed, attempt not verified | repeat read-only projection/reconciliation | send again |
| effect confirmed, attempt verified | skip forever | execute |

## 10 + 10 + 10 acceptance matrix

| Effect | Durable evidence | Continuation |
|---|---|---|
| Anna · 10 | `CHAIN_CONFIRMED` + attempt `VERIFIED` | `SKIP_VERIFIED` |
| Mark · 10 | `PLANNED` + no possible prior effect | `EXECUTE_MISSING` |
| Leo · 10 | effect/attempt `EXECUTION_UNKNOWN` | `RECONCILE_REQUIRED` |

The accepted partition is exactly:

```text
skip 10 + execute 10 + reconcile 10 + manual 0 = Mission total 30
```

This is currently verified offline with real SQLite integration tests. Actual KeeperHub testnet completion remains a separate runtime gate.
