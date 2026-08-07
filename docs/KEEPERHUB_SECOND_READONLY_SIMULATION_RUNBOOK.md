# KeeperHub Second Read-Only Key Simulation Validation

## Purpose

Validate the KeeperHub production read-only simulation permission fix with a second existing read-only organization key, using a fresh Nexus Vector Mission/effect/attempt namespace.

This path is independent from:

- the historical terminal `REJECTED_FINAL` effect;
- `simulation-canary-20260806-v1`;
- the Anna/Mark flagship Mission;
- PR #50 local operator console.

## Required preflight

A successful sanitized `KEEPERHUB_KEY_IDENTITY_SURFACE_V1` result must already exist locally and must prove:

```text
GET /api/keys: 1
HTTP: 200
response_surface: APPLICATION_JSON
organization_key_match: MATCH
POSTs: 0
simulation_posts: 0
broadcast_posts: 0
funds_moved: false
```

The simulation action sheet stores only the SHA-256 binding of that sanitized preflight JSON. It does not store the API key or returned key prefix.

Because the preflight intentionally did not persist a credential fingerprint, the operator must re-enter the same second read-only key at execution time. This is an explicit residual operator-control boundary, not a cryptographic credential binding.

## Fixed validation identity

```text
Mission: readonly-key2-validation-20260807-v1
Effect: readonly-key2-simulation-v1
Purpose: READ_ONLY_SECOND_KEY_PERMISSION_VALIDATION_V1
Chain: Base Sepolia
Chain ID: 84532
Asset: official Base Sepolia USDC
Amount: 0.000001 USDC
maximum simulation POSTs: 1
maximum broadcast POSTs: 0
```

## Safety boundary

- simulation only;
- canonical shared KeeperHub HTTP transport, which sends `User-Agent: NexusVector-KeeperHub/1.0`;
- no `Idempotency-Key` on simulation;
- no broadcast command;
- no broadcast port;
- no signing;
- no mainnet;
- no automatic retry;
- durable simulation authorization is claimed before the provider call;
- timeout/disconnect/ambiguity consumes the slot and becomes `OUTCOME_UNKNOWN`;
- a consumed authorization blocks another provider POST after restart;
- no API key, full address, request fingerprint, action-sheet ID, or raw provider body is printed.

## Phase 1 — prepare (network free)

From repository root:

```powershell
python .\tools\keeperhub_second_readonly_simulation.py prepare
```

Expected high-level result:

```text
status: PREPARED
network_calls_performed: 0
preflight_binding: MATCH
maximum_simulation_posts: 1
maximum_broadcast_posts: 0
broadcast_authorized: false
funds_moved: false
```

The output also contains an approval challenge. Preserve it privately. Preparing does not authorize or perform the simulation.

## Phase 2 — exact approval gate

Before execution, review and record:

- exact branch;
- exact commit SHA;
- exact Mission/effect shown above;
- same second read-only key used for the successful preflight;
- exactly one simulation POST;
- zero broadcast POSTs;
- zero signing;
- zero funds movement.

Execution is not authorized by PR creation, green CI, preparation, or this runbook. It requires separate operator approval for the exact reviewed head and approval challenge.

## Phase 3 — execute one simulation

Enter the same second read-only key through a secure prompt and provide the exact prepared approval challenge only after the separate approval gate is satisfied.

The execution command reads:

```text
KEEPERHUB_API_KEY
NEXUS_VECTOR_SECOND_READONLY_APPROVAL
```

from the child-process environment and removes both values from the process environment before exit.

Expected success boundary:

```text
status: PASS
decision: ELIGIBLE_FOR_BROADCAST_APPROVAL
simulation_posts: 1
broadcast_posts: 0
funds_moved: false
claim_boundary: SIMULATION_ONLY_NOT_TRANSACTION_EVIDENCE
```

`ELIGIBLE_FOR_BROADCAST_APPROVAL` does **not** authorize broadcast and does not prove a transaction.

## Stop policy

Any provider error, timeout, disconnect, malformed response, authorization rejection, action-sheet mismatch, changed preflight evidence, or local-state inconsistency is fail-closed.

After a claimed provider call:

```text
retry: FORBIDDEN
```

Do not delete SQLite state, create a replacement effect, change the request key, or rerun the same Mission/effect merely to obtain a different provider result.

## Public evidence boundary

A successful result may later be summarized publicly only after redaction review. Never publish:

- API key or prefix;
- support request ID unless separately approved;
- full wallet/token addresses;
- action-sheet ID;
- attempt/effect IDs;
- request key/fingerprint;
- raw provider payload.

The result is simulation evidence only, never transaction evidence.
