# Runtime Evidence Simulation Canary

**Status: operator path prepared — no live request is authorized by this document.**

This runbook defines one isolated KeeperHub Direct Execution simulation used to confirm that the provider-side simulation permission fix and the current request contract are live before the Anna/Mark flagship Mission is touched.

## Fixed effect

```text
purpose: POST_FIX_PROVIDER_REGRESSION_VALIDATION_V1
mission_ref: simulation-canary-20260806-v1
effect_ref: provider-canary
chain: Base Sepolia
chain_id: 84532
asset: pinned Base Sepolia USDC
amount: 1 base unit / 0.000001 USDC
maximum_simulation_posts: 1
maximum_broadcast_posts: 0
```

The canary is independent from `runtime-evidence-001`. A rejection or ambiguous response cannot consume Anna or Mark state.

## Capability boundary

`tools/keeperhub_runtime_evidence_canary.py` exposes only:

```text
prepare
execute
status
```

It has no broadcast command, no broadcast approval flag, no signing path, no Workflow/MCP/x402/Marketplace path, no mainnet option, and no automatic retry.

The default runtime composition is:

```text
KeeperHubHttpTransport
→ KeeperHubSimulationOnlyTransport
→ KeeperHubControlledSimulationService
→ durable SIMULATION authorization claim
→ at most one POST with simulate=true
```

Simulation never sends an `Idempotency-Key`. Any timeout, disconnect, malformed response, or other ambiguity finalizes the authorization as `OUTCOME_UNKNOWN`; a second invocation performs zero provider calls.

## Local state

The tool uses the same private runtime root prepared by the network-free planning step:

```text
%LOCALAPPDATA%\NexusVector\RuntimeEvidence\
```

Private files include:

```text
missions.sqlite3
execution-attempts.sqlite3
canary-authorizations.sqlite3
canary.private-action-sheet.json
```

Do not copy these files into Git, public evidence, screenshots, chat, Google Drive, or the submission bundle.

## Phase 1 — prepare with zero network calls

After syncing the exact merged code, run:

```powershell
python .\tools\keeperhub_runtime_evidence_canary.py prepare
```

Expected safe facts:

```text
status: PREPARED
purpose: POST_FIX_PROVIDER_REGRESSION_VALIDATION_V1
mission_ref: simulation-canary-20260806-v1
effect_ref: provider-canary
amount: 0.000001
maximum_simulation_posts: 1
maximum_broadcast_posts: 0
network_calls_performed: 0
funds_moved: false
```

The command also returns a private approval challenge. Preparing the action sheet does not authorize or execute the simulation.

## Phase 2 — exact approval and one simulation

Execution requires both local environment values:

```text
KEEPERHUB_API_KEY
NEXUS_VECTOR_CANARY_APPROVAL
```

The API key must be loaded from the existing Windows DPAPI store and removed from the environment after the process exits. The approval value must exactly equal the challenge produced by `prepare`.

The live `execute` command must not be run until the exact action is separately approved in chat after green CI and local `status` review.

## PASS acceptance

A PASS requires all of:

```text
HTTP 200
success = true
status = simulated
wouldRevert = false
simulation_posts = 1
broadcast_posts = 0
broadcast_authorized = false
funds_moved = false
authorization_state = ELIGIBLE_FOR_BROADCAST_APPROVAL
action_sheet_binding = MATCH
request_fingerprint_binding = MATCH
```

A PASS proves only that the exact canary transfer would simulate successfully through the current KeeperHub provider path. It is not transaction evidence and does not authorize Anna, Mark, or any broadcast.

## Evidence capture

Save a sanitized terminal screenshot containing only:

```text
probe
status
decision
chain / chain_id
asset / amount
simulation_posts
broadcast_posts
authorization_state
provider_summary safe fields
broadcast_authorized
funds_moved
```

Do not capture the API key, approval challenge, full addresses, Mission/effect/attempt IDs, request key, request fingerprint, action sheet contents, raw provider response, headers, or local user paths.

## Stop policy

Stop with no second POST when:

- the provider response is ambiguous;
- the authorization is already consumed;
- the action sheet or durable store is missing, corrupt, or mismatched;
- the response contains execution/broadcast evidence;
- `simulate` is not the strict JSON boolean `true`;
- an idempotency key is present;
- any chain, token, recipient, amount, or request binding changes.

The old historical `REJECTED_FINAL` simulation remains immutable and unrelated to this new post-fix canary Mission.
