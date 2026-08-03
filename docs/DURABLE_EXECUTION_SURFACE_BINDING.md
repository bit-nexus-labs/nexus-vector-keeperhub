# Durable Execution Surface Binding

**Status:** OFFLINE IMPLEMENTATION / P1 MUTATION STILL BLOCKED  
**Mainnet:** BLOCKED  
**Workflow / MCP execution:** NOT PERFORMED

Nexus Vector treats Direct Execution, KeeperHub Workflow, and MCP as separate
provider surfaces. They may expose different request, retry, and recovery
semantics, but they must never create separate economic authority for the same
canonical effect.

## Invariant

```text
one canonical effect_id → exactly one mutating provider surface
```

Allowed surfaces:

```text
DIRECT_EXECUTION
WORKFLOW
MCP
```

A surface binding is durable, immutable, and created before the first
surface-specific mutating call. Restart, a new approval reference, or a proposed
fallback cannot change it.

## Persistence

`SQLiteExecutionSurfaceBindingStore` is a separate fail-closed sidecar with:

- `effect_id` as the primary economic identity;
- canonical `mission_key`;
- one `ExecutionSurface`;
- one opaque `binding_reference`;
- UTC binding timestamp;
- WAL, `synchronous = FULL`, and `BEGIN IMMEDIATE` writes;
- exact schema validation;
- no credential, wallet, transaction, recipient, token, amount, provider
  payload, or execution evidence.

Rules:

```text
same effect + same surface → idempotent read-back
same effect + different surface → SURFACE_BINDING_CONFLICT
same binding reference + different effect → BINDING_REFERENCE_CONFLICT
corrupt or unexpected schema → action blocked
```

Concurrent processes attempting different surfaces for the same effect produce
one durable winner. Every loser fails before its delegate is called.

## Execution gate

`SurfaceBoundExecutionPort` wraps a provider port. It accepts only a canonical
`IN_FLIGHT` attempt, persists or reads back the surface binding, verifies the
binding against the attempt, and only then calls the delegate.

Recommended Direct Execution ordering:

```text
ExecutionDispatchService
  → durable IN_FLIGHT
  → ProviderReferencePersistingPort preflight
  → SurfaceBoundExecutionPort(DIRECT_EXECUTION)
  → KeeperHubApprovedBroadcastPort
  → one KeeperHub broadcast POST
```

The surface binding does not replace:

- the canonical attempt journal;
- the split simulation/broadcast authorization ledger;
- provider-reference persistence;
- independent chain verification;
- reconciliation after ambiguity.

It prevents cross-surface duplication. Same-surface duplicate suppression remains
owned by the attempt journal and the action-specific authorization ledger.

## Workflow and MCP policy

Before a Workflow or MCP mutating action is enabled:

1. the canonical effect must be selected by the continuation planner;
2. reconciliation must show no possible prior effect;
3. a private action sheet must name the exact surface;
4. the effect must be durably bound to that surface;
5. every provider reference and evidence artifact must retain surface identity;
6. no error or ambiguity may trigger fallback to another surface;
7. an existing Direct Execution binding permanently blocks Workflow and MCP for
   that effect;
8. an existing Workflow binding permanently blocks Direct Execution and MCP;
9. an MCP control plane must not create a second mutating authority beneath an
   already-bound effect.

Read-only orchestration may inspect several surfaces. Mutating authority may use
only the single durable binding.

## Advantages

- prevents one effect from being paid through Direct Execution and then repeated
  through Workflow or MCP;
- survives restart and concurrent workers;
- makes provider selection auditable before the mutating call;
- permits future surfaces without weakening the current state machine;
- keeps economic identity separate from provider-specific identifiers.

## Drawbacks

- adds another SQLite sidecar to backup and recovery procedures;
- a mistaken approved binding cannot be changed in place;
- cross-surface fallback is intentionally unavailable even when another surface
  appears operational.

## Risks

- wiring a provider directly without `SurfaceBoundExecutionPort` would bypass
  this protection;
- loss of the binding database must trigger a full stop, not reconstruction from
  guesswork;
- the opaque binding reference must never contain API keys, wallet data, or raw
  provider payloads;
- a surface binding alone does not prove that a provider call happened.

## Alternatives

1. Store the surface in the existing attempt table. This reduces file count but
   requires a migration of a proven P0 journal.
2. Infer the surface from `provider_namespace`. This is weaker because future
   namespaces and orchestration layers can drift or alias.
3. Permit fallback after provider failure. This increases availability but can
   duplicate funds after an ambiguous outcome.

## Recommendation

Keep the durable binding as a mandatory wrapper for every future mutating
surface. Add explicit composition tests in the mission-level runtime
orchestrator. Do not enable real Workflow or MCP mutation until that orchestrator,
provider-specific contracts, status observation, recovery, and action-specific
approval gates are separately reviewed.
