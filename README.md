# Nexus Vector — Mission-Safe Agent Payments

> **The agent can retry. The money cannot duplicate.**

Nexus Vector is a safety and recovery layer for agent-initiated payments. It gives each business Mission and economic effect durable identity, persists execution intent before a provider call, treats ambiguous outcomes as unknown, reconciles from independent evidence, and continues only work that is still missing.

The repository contains a tested offline product core, an interactive **Mission Resilience Lab**, and a bounded KeeperHub Direct Execution integration path. It still does **not** claim that a real KeeperHub testnet transaction has already been completed.

Public product:

```text
https://bit-nexus-labs.github.io/nexus-vector-keeperhub/
```

## Why it exists

Provider idempotency protects one request key. A business Mission can outlive a process, contain multiple recipients, lose a response after an economic effect may already have occurred, or be submitted concurrently by more than one worker. Nexus Vector keeps durable business intent above request-level details.

```text
Business Mission
  → 1..N canonical effects
  → durable execution attempts
  → simulation-first KeeperHub adapter
  → durable provider execution reference
  → provider status observation
  → independent chain observation
  → reconciliation
  → deterministic continuation
```

## Implemented

### Mission and execution safety

- deterministic, versioned Mission and effect identity;
- changed-payload conflict detection;
- explicit Mission/effect/attempt state machines;
- atomic SQLite persistence of a Mission and all canonical effects;
- durable admission through `RECEIVED → VALIDATED → PERSISTED`;
- one canonical execution attempt per `effect_id`;
- `IN_FLIGHT` persisted before any execution-port call;
- ambiguous, malformed, timed-out, or forged outcomes classified as `EXECUTION_UNKNOWN`;
- single-writer concurrency through non-expiring revision-CAS claims;
- restart and lost-response reconciliation without blind resend;
- deterministic partial continuation across unequal amounts;
- read-only Execution Doctor service and strict sanitized JSON CLI.

### KeeperHub integration boundary

- official Direct Execution contract and testnet constants pinned in documentation;
- immutable integer base-unit to decimal-string conversion without floats;
- testnet-only transfer intent and economic fingerprint revalidation before simulation;
- strict simulation/body parity and one durable `Idempotency-Key` on broadcast;
- append-only SQLite provider-reference journal;
- provider-reference schema and existing-reference guard checked before the provider call;
- provider `executionId` persisted before `PROVIDER_ACKNOWLEDGED`;
- crash-safe recovery when the reference is durable but acknowledgement is incomplete;
- read-only provider status parsing with `X-Poll-Interval-Hint` enforcement;
- provider `completed` remains separate from independent chain verification;
- no-retry/no-redirect standard-library HTTPS transport pinned to the official KeeperHub API;
- strict wallet-readiness and enabled-testnet chain catalog parsers;
- explicit credential injection with no browser, environment, keyring, or automatic credential lookup.

### Mission Resilience Lab

The dependency-free public product provides a local, non-executing resilience sandbox:

- dynamic Mission Builder with **1–10 independent effects**;
- single-effect, unequal-recovery, four-way batch, and five-way batch presets;
- editable recipient aliases and integer demo-unit amounts before Mission persistence;
- deterministic local sandbox checksum and budget-partition checks after persistence;
- controlled lost-response, duplicate-submit, process-restart, payload-mutation, and retry-all scenarios;
- per-effect `SKIP_VERIFIED`, `EXECUTE_MISSING`, `RECONCILE_REQUIRED`, and `MANUAL_REVIEW` decisions;
- safe-versus-counterfactual treasury comparison;
- Execution Black Box with process epoch and deterministic recovery events;
- Treasury Gate, Mission state-machine, and sanitized Evidence views;
- an explicit `PENDING_RUNTIME` boundary for future verified testnet evidence;
- responsive and reduced-motion behavior without external runtime dependencies.

The sandbox checksum is a compact local UI consistency marker, not the canonical cryptographic Mission identity used by the runtime core.

The browser contains no KeeperHub credential, wallet capability, signing path, network transport, transaction broadcast, or hidden storage. All sandbox outcomes are deterministic local classifications.

## Safety invariants

1. One economic effect has one canonical `effect_id`.
2. A Mission and all effects exist durably before execution eligibility is returned.
3. An execution attempt reaches durable `IN_FLIGHT` before the external port is called.
4. The KeeperHub intent must reproduce the durable request fingerprint before simulation.
5. Simulation and broadcast economic fields are identical; only `simulate` is removed.
6. A returned `executionId` is durable before provider acknowledgement.
7. In-flight, acknowledged, submitted, failed-provider, and unknown outcomes never authorize blind retry.
8. Provider completion is not recipient-payment proof; independent chain evidence remains mandatory.
9. A confirmed effect is skipped permanently.
10. Every effect is classified exactly once and all partitions equal the immutable Mission total.
11. Changed recipient, token, amount, or Mission context requires a new canonical identity.
12. Mainnet is blocked by project policy.

## Reference scenario: 12 + 7 + 11

The original unequal-amount scenario remains available as one Mission Builder preset:

- **Anna — 12:** independently verified and permanently skipped;
- **Mark — 7:** missing and only a future execution candidate after policy gates;
- **Leo — 11:** execution outcome unknown and therefore reconciliation-required;
- **Mission total — 30:** classified exactly once with no overlap.

It is one reference scenario, not a fixed product limit. The sandbox can create Missions containing from one to ten effects.

## Verify locally

The project uses the Python standard library only.

### Windows PowerShell

```powershell
$env:PYTHONPATH = "src"
py -m compileall -q src tests tools
py .\tools\verify_repository_hygiene.py
py .\tools\verify_public_evidence.py
py -m unittest discover -s tests -p "test_*.py" -v
```

### Linux / macOS

```bash
PYTHONPATH=src python -m compileall -q src tests tools
python tools/verify_repository_hygiene.py
python tools/verify_public_evidence.py
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

## Repository map

```text
src/nexus_vector/domain/       Mission/effect/attempt/provider-reference rules
src/nexus_vector/persistence/  SQLite Mission, attempt, and provider-reference stores
src/nexus_vector/application/  admission, dispatch, reconciliation, continuation, Doctor
src/nexus_vector/integrations/ KeeperHub intent, status, and bounded HTTPS transport
src/nexus_vector/cli/          strict sanitized Execution Doctor CLI
frontend/                      Mission Resilience Lab and curated replay evidence
evidence/                      public manifest and runtime-claim boundary
tools/                         hygiene and public-evidence verifiers
tests/                         unit, concurrency, restart, transport, UI, and SQLite tests
docs/                          architecture, state machines, integration contract, and runbooks
```

## Current runtime boundary

Already available:

- deployed GitHub Pages product;
- offline Mission, persistence, retry-suppression, restart, continuation, KeeperHub adapter, provider-reference, status-observer, transport, UI, and evidence layers;
- deterministic interactive sandbox with no external action capability.

Still pending and intentionally not claimed:

- locally supplied KeeperHub organization API key;
- authenticated wallet readiness and gas/token balance confirmation;
- exact private action sheet: chain, token, sender, recipient, amount, request key, fee cap, and confirmation policy;
- one separately approved controlled testnet simulation and broadcast;
- public explorer evidence independently matched to the exact intended ERC-20 event;
- recorded video and final submission links.

No API key, wallet material, raw provider payload, real recipient data, private receipt, or unredacted provider identifier belongs in this repository. See [`docs/RUNTIME_READINESS.md`](docs/RUNTIME_READINESS.md), [`docs/TESTNET_EVIDENCE_RUNBOOK.md`](docs/TESTNET_EVIDENCE_RUNBOOK.md), [`docs/MISSION_RESILIENCE_LAB.md`](docs/MISSION_RESILIENCE_LAB.md), and [`evidence/public_manifest.json`](evidence/public_manifest.json).
