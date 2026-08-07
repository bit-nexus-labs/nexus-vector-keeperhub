# KeeperHub Onboarding Findings — Nexus Vector

**Project:** Nexus Vector — Mission-Safe Agent Payments  
**Validation window:** 2026-08-03 through 2026-08-07  
**Environment:** testnet only  
**Primary execution surface:** KeeperHub Direct Execution  
**Safety boundary:** simulation-only for runtime validation; no broadcast and no funds movement in the findings below

## Executive summary

During KeeperHub onboarding, Nexus Vector identified two distinct integration findings:

1. **Read-only simulation permission mismatch — KeeperHub-confirmed and fixed.**  
   A Direct Execution request with `simulate: true` returned HTTP 403 when using a read-only credential. KeeperHub support confirmed that simulation authorization was being checked against `write` instead of `read`, causing read-only tokens to be rejected before the dry run started. KeeperHub later confirmed that the fix reached production.

2. **Python client request-signature interoperability — reproducible same-key A/B observation.**  
   In a fresh bounded test on 2026-08-07, the same read-only credential and the same `GET /api/keys` endpoint returned HTTP 403 when the request relied on Python urllib's default request signature, but returned HTTP 200 when the explicit application User-Agent `NexusVector-KeeperHub/1.0` was supplied. This is an observed interoperability result, not a KeeperHub-confirmed root cause.

The first issue affected least-privilege onboarding directly: a dry-run simulation temporarily required broader permission than the operation itself should need. The second finding showed that a stable application identifier materially improved reliability for our Python client.

## Finding 1 — Read-only simulation permission mismatch

### Pre-fix observation

Nexus Vector prepared one isolated Direct Execution simulation with:

- Base Sepolia (`84532`);
- official Base Sepolia USDC;
- minimal test amount;
- `simulate: true`;
- maximum simulation POSTs: 1;
- maximum broadcast POSTs: 0.

The provider returned HTTP 403. Nexus Vector classified that exact effect as terminal locally and did not retry it.

Observed safety result:

```text
simulation_posts: 1
broadcast_posts: 0
signing: 0
transaction_hash: none
funds_moved: false
local_state: REJECTED_FINAL
retry: FORBIDDEN
```

The exact provider-side error body and stable provider error code for this request were not preserved by our sanitized diagnostic runner at the time; the durable evidence retained records HTTP 403 and the resulting local `REJECTED_FINAL` classification.

`REJECTED_FINAL` was a Nexus Vector client state, not a KeeperHub execution record.

### KeeperHub confirmation

KeeperHub support confirmed that:

- the issue was on the KeeperHub side rather than an account configuration problem;
- simulations were being checked against `write` permission instead of `read`;
- a read-only token was rejected before the dry run began;
- the request itself was correct;
- Direct Execution did not require an additional entitlement, role, or dashboard setting for this case;
- simulation did not require a funded wallet or consume spending cap;
- the permission fix later reached production.

### Post-fix validation

After KeeperHub confirmed production deployment, Nexus Vector ran an isolated read-only simulation canary.

Result:

```text
http_status: 200
provider_status: simulated
success: true
would_revert: false
gas_estimate: 45415
simulation_posts: 1
broadcast_posts: 0
funds_moved: false
```

A second independent read-only credential was then validated through a fresh isolated Mission/effect namespace. It first passed a bounded `GET /api/keys` identity preflight and then passed exactly one simulation POST:

```text
preflight_http_status: 200
organization_key_match: MATCH

simulation_http_status: 200
provider_status: simulated
success: true
would_revert: false
gas_estimate: 45415

simulation_posts: 1
broadcast_posts: 0
broadcast_authorized: false
funds_moved: false
```

This independently reproduced the corrected read-only simulation behavior without reusing the historical rejected effect or the first post-fix canary.

### Impact

Before the fix, the permission mismatch undermined least-privilege onboarding: a read-only credential could not perform a dry-run simulation even though the simulation itself did not broadcast a transaction.

The production fix restored the expected separation between read-only simulation and write-authorized execution.

## Finding 2 — Python urllib request-signature interoperability

This finding is separate from the permission bug above.

On 2026-08-07, after the read-only simulation fix had already been validated, Nexus Vector performed a bounded same-key A/B control against:

```text
GET /api/keys
Python 3.14
same read-only credential
maximum GET requests per run: 1
POST requests: 0
```

### A — Python urllib default request signature

The request did not set an explicit application User-Agent.

Result:

```text
http_status: 403
response_surface: APPLICATION_JSON
status: STOP
```

### B — Explicit application User-Agent

The same bounded request was repeated with:

```text
User-Agent: NexusVector-KeeperHub/1.0
```

Result:

```text
http_status: 200
response_surface: APPLICATION_JSON
organization_key_match: MATCH
status: PASS
```

### What this supports

Claim-safe conclusion:

> In our bounded same-key test against `GET /api/keys`, Python urllib's default request signature returned HTTP 403, while the same request with the explicit `NexusVector-KeeperHub/1.0` User-Agent returned HTTP 200 and matched the organization key.

Nexus Vector therefore keeps a stable application User-Agent on all direct KeeperHub Python request sites and protects that behavior with regression tests.

### What this does **not** establish

This A/B result does not establish:

- that KeeperHub intentionally blocks Python urllib;
- that Cloudflare or a WAF caused the 403;
- that KeeperHub confirmed a User-Agent bug;
- that KeeperHub deployed a server-side User-Agent fix.

The observed 403 response surface in the fresh control was `APPLICATION_JSON`, not a Cloudflare HTML challenge.

## Safety behavior demonstrated during onboarding

The integration work intentionally treated provider ambiguity as a financial-safety problem rather than a retry problem.

For mutating operations Nexus Vector enforced:

- durable authorization claim before provider transport;
- one simulation POST per exact effect;
- no automatic retry after a claimed POST;
- timeout or disconnect becomes outcome-unknown rather than a blind resend;
- simulation and broadcast have separate authority;
- a successful simulation does not authorize broadcast;
- no restart path recreates economic authority for an already-consumed effect;
- mainnet remained blocked throughout these validations.

This preserved the core project invariant:

> **The agent can retry. The money cannot duplicate.**

## Recommendations

### 1. Keep simulation permission explicitly read-compatible

The production fix should remain covered by a provider-side regression test ensuring `simulate: true` accepts the intended read-level credential while broadcast remains separately write-authorized.

### 2. Document the expected application identifier for Python examples

If a stable application User-Agent is expected or recommended, include it directly in Python onboarding examples and quickstarts.

### 3. Prefer actionable machine-readable rejections

When a client request is rejected before reaching business logic, a stable documented JSON error code describing the client requirement would reduce onboarding ambiguity.

### 4. Preserve simulation/broadcast separation

The corrected read-only simulation behavior is valuable because it lets agents validate intent and execution feasibility without receiving transaction-broadcast authority.

## Evidence and disclosure boundary

The public report intentionally excludes:

- API keys and key prefixes;
- organization identifiers;
- support request IDs;
- full wallet addresses;
- private action-sheet identifiers;
- attempt/effect identifiers;
- request fingerprints;
- raw provider payloads;
- private email metadata.

The runtime results above are **simulation evidence only**. They are not transaction evidence and do not claim that an onchain transfer occurred.

## Final status

As of 2026-08-07:

```text
read-only identity preflight: PASS
read-only simulation after production fix: PASS
second independent read-only simulation: PASS
default urllib same-key GET control: HTTP 403
explicit NexusVector-KeeperHub/1.0 same-key GET control: HTTP 200 / MATCH
broadcast performed for these findings: 0
funds moved for these findings: false
```

KeeperHub network diagnostics for these two findings are complete. Further provider calls are not required to support the claims above.
