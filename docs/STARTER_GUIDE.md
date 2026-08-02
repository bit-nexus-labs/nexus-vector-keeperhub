# Starter Guide

## Requirements

- Python 3.12 or 3.14;
- no third-party Python dependency is required;
- a browser for the static replay UI.

## Run the test suite

From the repository root:

### Windows PowerShell

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover -s tests -p "test_*.py" -v
```

### Linux / macOS

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

## Verify the evidence bundle

```powershell
py .\tools\verify_public_evidence.py
```

Expected output begins with:

```text
PUBLIC_EVIDENCE_VERIFY: PASS
```

The verifier checks curated artifact hashes and rejects a false runtime-transaction claim.

## Open the replay

Open `frontend/index.html` directly in a browser. Use:

- **Simple** for the 30-second product story;
- **Technical** for Mission/effect/attempt state;
- **Evidence** for sanitized records and the curated manifest digest;
- left/right arrow keys to move through the five replay steps.

The replay performs no external action.

## Explore the product core

Recommended reading order:

1. `src/nexus_vector/domain/mission_identity.py`
2. `src/nexus_vector/domain/mission_models.py`
3. `src/nexus_vector/domain/mission_transitions.py`
4. `src/nexus_vector/persistence/sqlite_mission_store.py`
5. `src/nexus_vector/application/mission_admission.py`
6. `src/nexus_vector/domain/execution_attempts.py`
7. `src/nexus_vector/persistence/sqlite_execution_attempt_store.py`
8. `src/nexus_vector/application/execution_dispatch.py`
9. `src/nexus_vector/application/execution_reconciliation.py`
10. `src/nexus_vector/application/continuation_planner.py`
11. `src/nexus_vector/application/execution_doctor.py`

## Build a provider adapter safely

Do not begin with a live write. A new adapter should be implemented in this order:

1. define exact documented request and response schemas;
2. add sanitized versioned fixtures;
3. pass offline adapter tests;
4. verify authenticated wallet readiness through an official surface;
5. run simulation without broadcast when supported;
6. approve one exact testnet action with chain, token, sender, recipient, amount, request key, stop conditions, and evidence policy;
7. persist the attempt before the call;
8. never retry an unknown outcome;
9. independently match the exact ERC-20 event;
10. update public evidence only after redaction review.

Mainnet remains blocked.
