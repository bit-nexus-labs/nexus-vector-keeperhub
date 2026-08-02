# Nexus Vector — Mission-Safe Agent Payments

> **The agent can retry. The money cannot duplicate.**

Nexus Vector is a provider-neutral safety and recovery layer for agent-initiated payments. It gives a business Mission and each economic effect durable identity, persists execution intent before a provider call, treats ambiguous outcomes as unknown, reconciles from independent evidence, and continues only work that is still missing.

The current repository contains a tested **offline product core**, a deterministic Execution Doctor, a sanitized replay UI, and a fail-closed public evidence bundle. It does **not** claim that a real KeeperHub testnet transaction has already been completed.

## Why it exists

Provider idempotency protects one request key. A business Mission can outlive a process, use a new request key, contain several recipients, or lose a response after an economic effect may already have occurred. Nexus Vector keeps the durable business intent above those request-level details.

```text
Business Mission
  → canonical effects
  → durable execution attempts
  → provider-neutral execution port
  → independent observation
  → reconciliation
  → deterministic continuation
```

## Implemented

- deterministic, versioned Mission and effect identity;
- changed-payload conflict detection;
- explicit Mission/effect state machines;
- atomic SQLite persistence of a Mission and all canonical effects;
- durable Mission admission through `RECEIVED → VALIDATED → PERSISTED`;
- one canonical execution attempt per `effect_id`;
- `IN_FLIGHT` persisted before any execution-port call;
- ambiguous, malformed, timed-out, or forged outcomes classified as `EXECUTION_UNKNOWN`;
- single-writer concurrency through non-expiring revision-CAS claims;
- exact independent ERC-20 observation contracts;
- restart and lost-response reconciliation without blind resend;
- deterministic 10 + 10 + 10 continuation planning;
- read-only Execution Doctor service and strict JSON CLI;
- dependency-free Simple, Technical, and Evidence replay views;
- sanitized public evidence manifest with artifact hashes and tamper tests.

## Safety invariants

1. One economic effect has one canonical `effect_id`.
2. A Mission and all effects exist durably before execution eligibility is returned.
3. An execution attempt is durable before the external port is called.
4. `IN_FLIGHT`, acknowledged, submitted, and unknown outcomes never authorize blind retry.
5. Independent chain evidence is projected into the Mission store before an attempt becomes `VERIFIED`.
6. A confirmed effect is skipped permanently.
7. Every effect is classified exactly once as skip, execute, reconcile, or manual review.
8. Classified amounts must equal the immutable Mission total.
9. Mainnet is blocked by project policy.

## 10 + 10 + 10 replay

The curated replay demonstrates:

- **Anna — 10:** independently verified and permanently skipped;
- **Mark — 10:** missing and only a future execution candidate after policy gates;
- **Leo — 10:** execution outcome unknown and therefore reconciliation-required;
- **Mission total — 30:** classified exactly once with no overlap.

Open [`frontend/index.html`](frontend/index.html). The interface is explicitly marked `REPLAY / SANITIZED / NO LIVE TRANSACTION` and has no provider, wallet, RPC, signing, broadcast, analytics, or external-CDN capability.

## Verify locally

The project uses the Python standard library only.

### Windows PowerShell

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover -s tests -p "test_*.py" -v
py .\tools\verify_public_evidence.py
```

### Linux / macOS

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
python tools/verify_public_evidence.py
```

GitHub CI runs the standard-library suite on Python 3.12 and 3.14.

## Repository map

```text
src/nexus_vector/domain/       identity, Mission/effect and attempt state rules
src/nexus_vector/persistence/  SQLite Mission and execution-attempt stores
src/nexus_vector/application/  admission, dispatch, reconciliation, continuation, Doctor
src/nexus_vector/cli/          strict sanitized Execution Doctor CLI
frontend/                      dependency-free curated replay UI
evidence/                      public manifest and evidence boundary
tools/                         offline public-evidence verifier
tests/                         unit, concurrency, restart and real SQLite integration tests
docs/                          architecture, state machines and starter guide
```

## Current runtime boundary

Still pending and intentionally not claimed:

- authenticated KeeperHub wallet-readiness confirmation;
- a reviewed KeeperHub execution adapter;
- a controlled testnet transaction;
- public explorer evidence matched to the exact intended ERC-20 event;
- deployment and production readiness.

No API key, wallet material, raw provider payload, real recipient data, private receipt, or unredacted provider identifier belongs in this public repository. See [`evidence/public_manifest.json`](evidence/public_manifest.json) for the current evidence status.
