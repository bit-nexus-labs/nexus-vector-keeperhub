# Controlled KeeperHub Runtime Authorization Gates

**Status:** OFFLINE IMPLEMENTATION / NO RUNTIME AUTHORIZATION  
**Mainnet:** BLOCKED  
**Live KeeperHub transaction:** NOT PERFORMED

This document defines the action-specific boundary between the tested Nexus
Vector product core and any future KeeperHub testnet runtime.

The combined `KeeperHubDirectExecutionPort` remains an offline contract harness:
one method validates intent, simulates, and then broadcasts. It must **not** be
wired directly into a live operator command because a successful simulation
would continue to broadcast inside the same call. The controlled runtime path
separates those actions and makes their POST authority durable across restart.

## Required flow

```text
durable Mission/effects/attempt plan
  → durable canonical attempt in PREPARED
  → completed private action sheet
  → one simulation-specific approval
  → durable SIMULATION claim
  → one simulation POST
  → durable sanitized simulation receipt
  → operator/reviewer inspects the result
  → separate broadcast-specific approval
  → exact --approve-testnet-write flag
  → durable IN_FLIGHT attempt claim
  → durable BROADCAST claim
  → one broadcast POST
  → durable executionId before PROVIDER_ACKNOWLEDGED
  → bounded status reads
  → independent exact ERC-20 event verification
  → reconciliation
```

A simulation approval never authorizes broadcast. A broadcast authorization is
invalid unless it is bound to the same:

- private action-sheet identity;
- canonical `attempt_id`;
- durable request fingerprint;
- exact simulation-body fingerprint;
- separately recorded approval reference;
- explicit authorization window.

## Durable one-shot ledger

`SQLiteKeeperHubAuthorizationLedger` is a separate fail-closed SQLite sidecar.
It records only opaque identities, fingerprints, phase, state, and UTC
timestamps. It contains no API key, wallet secret, raw provider payload,
recipient address, token address, or amount.

The unique business authority is:

```text
(phase, attempt_id)
```

where phase is exactly:

```text
SIMULATION | BROADCAST
```

A claim is written with `BEGIN IMMEDIATE`, WAL, `synchronous = FULL`, and exact
schema validation **before** transport access. Therefore:

- a second process cannot acquire the same phase for the same effect;
- a new approval reference cannot create another POST for that effect;
- restart cannot reset an already consumed budget;
- timeout or malformed response cannot restore authority;
- concurrent workers produce one claim winner;
- a corrupt or unexpected schema blocks the action.

The ledger states are:

```text
CLAIMED
ELIGIBLE_FOR_BROADCAST_APPROVAL
REJECTED_FINAL
OUTCOME_UNKNOWN
ACCEPTED
```

`CLAIMED` itself is a safe recovery state. If the process stops after the claim
but before a final ledger transition, the action remains consumed and must be
reviewed or reconciled. It is never repeated automatically.

The ledger is intentionally separate from the Mission, attempt, and provider
reference stores. This avoids changing already proven schemas before the
hackathon. The tradeoff is another SQLite file that must be backed up,
permissioned, and recovered together with the other runtime journals.

## Call budgets

The durable limits are:

```text
maximum_simulation_posts_per_effect = 1
maximum_broadcasts_per_effect = 1
maximum_total_broadcasts_per_mission = N_approved
maximum_mutating_calls_per_effect = 1
maximum_new_request_keys_after_ambiguity = 0
maximum_concurrent_mutating_effects = 1 initially
```

Same-key recovery POST after ambiguity remains forbidden until KeeperHub
confirms an exact supported procedure and that procedure is separately
reviewed.

## Simulation phase

`KeeperHubControlledSimulationService`:

1. accepts an immutable `ExecutionAttemptPlan` and
   `KeeperHubTransferIntent`;
2. recomputes the durable request fingerprint before transport access;
3. creates or reads back the exact canonical attempt in `PREPARED`;
4. blocks if that attempt already left `PREPARED`;
5. requires an exact `KeeperHubSimulationAuthorization`;
6. validates its UTC authorization window;
7. atomically consumes the SIMULATION slot for the canonical effect;
8. performs at most one simulation POST without an idempotency key;
9. stores no raw provider payload;
10. durably records a sanitized decision and exact body fingerprint.

Eligible response:

```text
HTTP 200
success = true
status = simulated
wouldRevert = false
```

A structured final rejection becomes `REJECTED_FINAL`. Timeout, disconnect,
malformed response, classifier failure, or persistence ambiguity becomes
`OUTCOME_UNKNOWN`; it never authorizes broadcast.

A completed receipt can be reconstructed after restart only from a durable
final ledger record. A `CLAIMED` or `OUTCOME_UNKNOWN` simulation is not an
eligible receipt.

## Broadcast phase

`KeeperHubApprovedBroadcastPort`:

1. accepts only a durable eligible simulation receipt;
2. requires a different, separate
   `KeeperHubBroadcastAuthorization`;
3. rejects approval created before the simulation;
4. requires the exact runtime flag `--approve-testnet-write`;
5. validates the approval window against the durable `IN_FLIGHT` timestamp;
6. re-hashes the current simulation body and compares it with the durable
   receipt before external access;
7. accepts only the matching canonical attempt and request fingerprint;
8. atomically consumes the BROADCAST slot for the canonical effect;
9. performs exactly one broadcast POST using the durable request key as
   `Idempotency-Key`;
10. never performs another simulation and never retries.

The port is wrapped by `ProviderReferencePersistingPort` and invoked through
`ExecutionDispatchService`, preserving:

```text
PREPARED
  → durable IN_FLIGHT
  → durable BROADCAST claim
  → one broadcast POST
  → durable provider reference
  → PROVIDER_ACKNOWLEDGED
```

If a response is accepted but the final authorization-ledger transition fails,
the initial `CLAIMED` row still blocks another POST. The provider result is
returned so `ProviderReferencePersistingPort` can persist `executionId` before
acknowledgement.

If the provider result or transport is ambiguous, generic dispatch persists
`EXECUTION_UNKNOWN`. A second POST is forbidden.

## Three-effect live Mission sequencing

For the first controlled three-effect Mission:

```text
1. Admit and read back the complete Mission and all three effects.
2. Reconcile every effect before selecting execution work.
3. Classify independently verified effects as SKIP_VERIFIED.
4. Permit only EXECUTE_MISSING effects to enter simulation.
5. Keep possible prior outcomes in RECONCILE_REQUIRED.
6. Keep maximum_concurrent_mutating_effects = 1.
7. Complete simulation review and separate broadcast approval per effect.
8. Verify the exact ERC-20 event before advancing to another effect.
9. Recompute all Mission partitions and immutable total after reconciliation.
```

The public Anna 12 / Mark 7 / Leo 11 scenario remains a sanitized reference
preset. The first live proof should use a fresh private three-effect Mission
whose exact addresses, amounts, token, fee cap, and confirmation policy are
reviewed in the action sheet.

Recommended first proof shape:

```text
effect A → one controlled simulation + one broadcast + exact verification
effect B → only after A is VERIFIED
effect C → only after A and B are reconciled
```

This is intentionally serial. Parallel mutating effects add no judging value
before single-effect recovery is proven and increase duplicate-funds risk.

## Remaining P0 work

This patch closes the action-specific approval, durable PREPARED provenance,
and restart-reset gaps. It does not authorize a KeeperHub call and does not
replace these gates:

- exact supported wallet-readiness surface;
- native gas and ERC-20 balance confirmation;
- enabled testnet, token, and decimals confirmation;
- completed private action sheet;
- local credential injection outside Git and chat;
- one simulation-specific approval;
- later one broadcast-specific approval;
- bounded status observation;
- independent exact event verifier connected to an approved read-only source;
- redaction and claim-match review.

A mission-level runtime command still must compose the existing admission,
continuation, dispatch, provider-reference, status, verification, and Doctor
services without bypassing their state machines.

The runtime command must fail closed when it detects the combined offline
`KeeperHubDirectExecutionPort` instead of the split controlled path.

## P1 surface-exclusivity requirement

Direct Execution, KeeperHub Workflow, and MCP are separate provider surfaces.
The same canonical effect must never execute through more than one surface.

Before enabling Workflow or MCP, add a durable binding:

```text
effect_id → DIRECT_EXECUTION | WORKFLOW | MCP
```

Required rules:

- binding is created before the first surface-specific mutating call;
- identical rebinding is idempotent;
- a different surface for the same effect is a terminal conflict;
- restart reads the binding before provider selection;
- MCP may orchestrate control-plane work but cannot create a second economic
  authority for an effect already bound to Direct Execution or Workflow;
- no fallback from one mutating surface to another after ambiguity;
- all provider references retain the chosen surface identity.

Until that persistence layer is reviewed and merged, P1 Workflow/MCP mutation
remains blocked. Read-only planning, schema work, and tests are allowed.

## Decision review

### Advantages

- approvals are action-specific instead of implied by one method call;
- duplicate POST authority survives restart and concurrency;
- simulation evidence is durable and sanitized;
- accepted execution references can still be persisted if the auxiliary ledger
  finalization fails;
- existing proven Mission and attempt schemas remain untouched.

### Drawbacks

- one more SQLite sidecar must be managed and backed up;
- the flow requires two explicit operator review points;
- one simulation per effect is intentionally strict and may require a new
  Mission version after a final rejection.

### Risks

- wiring the old combined port into a future CLI would bypass the split gate;
- losing the authorization ledger while keeping other runtime DBs would force a
  full stop and manual recovery;
- approval references must remain opaque and must not contain credentials or
  private provider payloads;
- a durable `CLAIMED` state may require manual review even when no external call
  actually occurred.

### Alternatives

1. Put approval consumption into the existing attempt table. This reduces the
   number of DB files but requires a higher-risk migration of proven state.
2. Keep an in-memory one-shot flag. This is simpler but unsafe after restart.
3. Rely only on KeeperHub idempotency. This cannot enforce separate approvals,
   has bounded retention, and does not protect Mission-level authority.

### Recommendation

Merge the durable split gate only after full green CI and exact diff review.
Then implement the mission-level offline runtime orchestrator in a separate PR.
Implement P1 surface binding in another separate PR before any Workflow or MCP
mutation is enabled.
