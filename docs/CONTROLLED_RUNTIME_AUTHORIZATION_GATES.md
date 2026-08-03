# Controlled KeeperHub Runtime Authorization Gates

**Status:** OFFLINE IMPLEMENTATION / NO RUNTIME AUTHORIZATION  
**Mainnet:** BLOCKED  
**Live KeeperHub transaction:** NOT PERFORMED

This document defines the action-specific boundary between the already tested
Nexus Vector product core and any future KeeperHub testnet runtime.

The current combined `KeeperHubDirectExecutionPort` remains useful as an offline
contract harness: one method validates intent, simulates, and then broadcasts.
It must **not** be wired directly into a live operator command because a
successful simulation would immediately continue to broadcast inside the same
call. The controlled runtime path instead separates those actions into two
independently authorized phases.

## Required flow

```text
durable Mission/effects/attempt plan
  → completed private action sheet
  → one simulation-specific approval
  → KeeperHubControlledSimulationService
  → sanitized KeeperHubSimulationReceipt
  → operator/reviewer inspects the result
  → separate broadcast-specific approval
  → exact --approve-testnet-write flag
  → durable IN_FLIGHT claim
  → KeeperHubApprovedBroadcastPort
  → ProviderReferencePersistingPort
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

## One-shot budgets

Each controlled object is one-shot:

```text
maximum_simulation_posts_per_authorization = 1
maximum_broadcast_posts_per_authorization = 1
maximum_mutating_calls_per_effect = 1
maximum_same_key_recovery_posts_after_ambiguity = 0
maximum_new_request_keys_after_ambiguity = 0
```

The budget is consumed **before** the injected transport is called. Timeout,
disconnect, malformed response, classifier failure, or any other ambiguity does
not restore the budget. Recovery proceeds through durable state, provider-status
reads when an `executionId` exists, and independent chain observation.

## Simulation phase

`KeeperHubControlledSimulationService`:

1. accepts an immutable `ExecutionAttemptPlan` and `KeeperHubTransferIntent`;
2. recomputes the request fingerprint before transport access;
3. requires an exact `KeeperHubSimulationAuthorization`;
4. performs at most one simulation POST without an idempotency key;
5. stores no raw provider payload in its receipt;
6. returns only a sanitized decision and exact body fingerprint.

Eligible simulation response:

```text
HTTP 200
success = true
status = simulated
wouldRevert = false
```

Any structured final rejection produces `REJECTED_FINAL`. Any ambiguous or
malformed outcome produces `SIMULATION_OUTCOME_UNKNOWN`; it never authorizes a
broadcast.

## Broadcast phase

`KeeperHubApprovedBroadcastPort`:

1. accepts only an eligible simulation receipt;
2. requires a separate `KeeperHubBroadcastAuthorization`;
3. rejects approval created before the simulation;
4. requires the exact runtime flag `--approve-testnet-write`;
5. re-hashes the current simulation body and compares it with the approved
   receipt before any external call;
6. accepts only the matching canonical `IN_FLIGHT` attempt;
7. performs exactly one broadcast POST using the durable request key as
   `Idempotency-Key`;
8. never performs another simulation;
9. never retries.

The port is intended to be wrapped by `ProviderReferencePersistingPort` and
invoked through `ExecutionDispatchService`, preserving this order:

```text
PREPARED
  → durable IN_FLIGHT
  → one broadcast POST
  → durable provider reference
  → PROVIDER_ACKNOWLEDGED
```

If the broadcast response is ambiguous, generic dispatch must persist
`EXECUTION_UNKNOWN`. A second POST is forbidden.

## Three-effect live Mission sequencing

For the first controlled 12 + 7 + 11 Mission:

```text
1. Admit and read back the complete Mission and all three effects.
2. Reconcile every effect before selecting execution work.
3. Anna 12:
   - if independently verified, classify SKIP_VERIFIED;
   - never create execution authority.
4. Mark 7:
   - only EXECUTE_MISSING may enter the controlled simulation gate.
5. Leo 11:
   - if any prior outcome is possible, classify RECONCILE_REQUIRED;
   - do not simulate or broadcast.
6. Keep maximum_concurrent_mutating_effects = 1.
7. Verify the exact event for one effect before advancing to another effect.
8. Recompute the Mission partition and total after every reconciliation.
```

The first live proof should normally use a fresh three-effect Mission whose
exact recipient/amount plan is private and reviewed. The public Anna/Mark/Leo
scenario may remain a sanitized replay unless revealing its real addresses and
amounts is explicitly approved.

## Remaining P0 work

This split gate closes the in-process action-specific approval gap. It does not
by itself authorize a KeeperHub call and does not replace these external gates:

- exact supported wallet-readiness surface;
- native gas and ERC-20 balance confirmation;
- chain/token/decimals confirmation;
- completed private action sheet;
- local credential injection;
- simulation-specific approval;
- later broadcast-specific approval;
- bounded status observation;
- independent exact event verifier connected to an approved read-only source;
- redaction and claim-match review.

A mission-level runtime command still must compose the existing admission,
continuation, dispatch, provider-reference, status, verification, and Doctor
services without bypassing their state machines.

## P1 surface-exclusivity requirement

Direct Execution, KeeperHub Workflow, and MCP are separate provider surfaces.
The same canonical effect must never be executed through more than one surface.

Before enabling Workflow or MCP, add a durable effect-to-surface binding:

```text
effect_id → DIRECT_EXECUTION | WORKFLOW | MCP
```

Required rules:

- binding is created before the first surface-specific mutating call;
- identical rebinding is idempotent;
- a different surface for the same effect is a terminal conflict;
- restart reads the binding before selecting a provider;
- orchestration may use MCP as a control plane, but MCP cannot create a second
  economic authority for an effect already bound to Direct Execution or
  Workflow;
- no fallback from one mutating surface to another after ambiguity.

Until that persistence layer is reviewed and merged, P1 Workflow/MCP execution
remains blocked. Read-only planning and documentation are allowed.
