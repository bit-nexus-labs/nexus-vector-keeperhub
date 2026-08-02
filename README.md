# Nexus Vector — Mission-Safe Agent Payments

> **The agent can retry. The money cannot duplicate.**

Nexus Vector is a safety and recovery layer for agent-initiated payments. It gives each business Mission and economic effect durable identity, persists execution intent before a provider call, treats ambiguous outcomes as unknown, reconciles from independent evidence, and continues only work that is still missing.

The repository now contains a tested offline product core **and a bounded KeeperHub Direct Execution integration path**. It still does **not** claim that a real KeeperHub testnet transaction has already been completed.

## Why it exists

Provider idempotency protects one request key. A business Mission can outlive a process, use a new request key, contain several recipients, or lose a response after an economic effect may already have occurred. Nexus Vector keeps durable business intent above request-level details.

```text
Business Mission
  → canonical effects
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
- deterministic 10 + 10 + 10 continuation planning;
- read-only Execution Doctor service and strict sanitized JSON CLI.

### KeeperHub integration boundary

- official Direct Execution contract and testnet constants pinned in documentation;
- immutable integer base-unit to decimal-string conversion without floats;
- testnet-only transfer intent and economic fingerprint revalidation before simulation;
- strict simulation/body parity and one durable `Idempotency-Key` on broadcast;
- append-only SQLite provider-reference journal;
- provider `executionId` persisted before `PROVIDER_ACKNOWLEDGED`;
- crash-safe recovery when the reference is durable but ACK is incomplete;
- read-only provider status parsing with `X-Poll-Interval-Hint` enforcement;
- provider `completed` remains separate from independent chain verification;
- no-retry/no-redirect standard-library HTTPS transport pinned to the official KeeperHub API;
- strict wallet-readiness and enabled-testnet chain catalog parsers;
- explicit credential injection with no environment, keyring, or local credential lookup.

### Presentation and evidence

- dependency-free Simple, Technical, and Evidence replay views;
- sanitized public evidence manifest with artifact hashes and tamper tests;
- demo video script, submission draft, testnet runbook, office-hours questions, and freeze checklist;
- repository-wide compile, hygiene, evidence, and standard-library tests on Python 3.12 and 3.14.

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
11. Mainnet is blocked by project policy.

## 10 + 10 + 10 replay

The curated replay demonstrates:

- **Anna — 10:** independently verified and permanently skipped;
- **Mark — 10:** missing and only a future execution candidate after policy gates;
- **Leo — 10:** execution outcome unknown and therefore reconciliation-required;
- **Mission total — 30:** classified exactly once with no overlap.

Open [`frontend/index.html`](frontend/index.html). It is explicitly marked `REPLAY / SANITIZED / NO LIVE TRANSACTION` and has no live wallet or provider action.

## Verify locally

The project uses the Python standard library only.

### Windows PowerShell

```powershell
$env:PYTHONPATH = "src"
py -m compileall -q src tests tools
py .\tools\check_repository_hygiene.py
py .\tools\verify_public_evidence.py
py -m unittest discover -s tests -p "test_*.py" -v
```

### Linux / macOS

```bash
PYTHONPATH=src python -m compileall -q src tests tools
python tools/check_repository_hygiene.py
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
frontend/                      dependency-free curated replay UI
evidence/                      public manifest and evidence boundary
tools/                         hygiene and public-evidence verifiers
tests/                         unit, concurrency, restart, transport, and SQLite integration tests
docs/                          architecture, state machines, integration contract, and runbooks
```

## Current runtime boundary

Code-complete offline, but still pending and intentionally not claimed:

- locally supplied KeeperHub organization API key;
- authenticated wallet readiness and gas/token balance confirmation;
- exact private action sheet: chain, token, sender, recipient, amount, request key, fee cap, and confirmation policy;
- one separately approved controlled testnet simulation and broadcast;
- public explorer evidence independently matched to the exact intended ERC-20 event;
- deployed frontend URL, recorded video, and final DoraHacks submission.

No API key, wallet material, raw provider payload, real recipient data, private receipt, or unredacted provider identifier belongs in this repository. See [`docs/RUNTIME_READINESS.md`](docs/RUNTIME_READINESS.md), [`docs/TESTNET_EVIDENCE_RUNBOOK.md`](docs/TESTNET_EVIDENCE_RUNBOOK.md), and [`evidence/public_manifest.json`](evidence/public_manifest.json).
