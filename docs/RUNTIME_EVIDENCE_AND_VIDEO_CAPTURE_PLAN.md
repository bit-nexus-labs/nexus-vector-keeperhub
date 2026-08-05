# Runtime Evidence and Video Capture Plan

**Status: PLAN ONLY — NO PROVIDER CALL OR TRANSACTION AUTHORIZATION**

This document defines how the controlled Base Sepolia evidence run is captured once execution is separately approved. It contains no credential, full wallet address, private provider identifier, or transaction claim.

## Flagship proof

The live Mission contains two independent economic effects:

```text
Anna — 0.12 USDC
Mark — 0.07 USDC
Mission total — 0.19 USDC
```

The two effects may use one controlled testnet recipient. Their identities remain separate because canonical Mission context, effect reference, chain, token, recipient, and integer base-unit amount bind each effect.

The target sequence is:

```text
persist both effects
→ execute and independently verify Anna
→ real process stop and new process start
→ Anna = SKIP_VERIFIED
→ Mark = EXECUTE_MISSING
→ execute and independently verify Mark
→ replay the complete immutable Mission
→ zero new KeeperHub POSTs and zero new transactions
```

This proves restart-safe partial continuation. It must not be described as a live lost-response test.

## Isolated simulation canary

Before the flagship Mission uses a provider call, an independent one-base-unit canary may receive one separately approved simulation POST:

```text
chain: Base Sepolia 84532
token: pinned Base Sepolia USDC
amount: 0.000001 USDC
maximum simulation POSTs: 1
maximum broadcast POSTs: 0
```

A failed or ambiguous canary never consumes an Anna or Mark authorization slot. It does not authorize broadcast and does not become transaction evidence.

## Evidence layers

Every important runtime step has four outputs:

1. private raw evidence;
2. sanitized public evidence;
3. a screenshot-ready compact result;
4. a short video clip when motion or restart is material.

Private evidence stays outside Git and synchronized cloud folders until reviewed. Public evidence is generated only after redaction review.

Recommended local structure:

```text
C:\Projects\Nexus_Evidence_Private\
├── 01_readiness\
├── 02_canary\
├── 03_mission_admission\
├── 04_anna\
├── 05_restart\
├── 06_mark\
├── 07_mission_replay\
├── raw_private\
└── sanitized_public\
```

Use unique filenames. Never overwrite an earlier capture:

```text
private_anna_broadcast_YYYYMMDD_HHMMSS.png
public_anna_broadcast_YYYYMMDD_HHMMSS_redacted_v1.png
```

## Required captures

### Readiness

Capture a compact sanitized result showing:

```text
wallet: PASS
chains: PASS
balances: PASS
chain_id: 84532
mainnet_blocked: true
```

The funded testnet wallet view is supporting evidence, not the central proof.

### Mission admission

Capture:

```text
Mission: runtime-evidence-001
Anna  0.12 USDC  PLANNED
Mark  0.07 USDC  PLANNED
Effects persisted: 2
Provider calls: 0
```

### Anna simulation

Capture:

```text
Effect: Anna
Simulation: PASS
wouldRevert: false
Simulation POSTs: 1
Broadcast POSTs: 0
Funds moved: false
```

### Anna broadcast and verification

Capture separate artifacts for:

- one broadcast call and durable provider-reference persistence;
- provider status observation;
- Base Sepolia explorer success;
- independently matched ERC-20 `Transfer` event;
- `Anna → CHAIN_CONFIRMED`.

The independent verifier view should show bounded facts only:

```text
chain: MATCH
token: MATCH
sender: MATCH
recipient: MATCH
amount_base_units: 120000 MATCH
confirmations: sufficient
```

### Partial state before restart

Capture:

```text
Anna  CHAIN_CONFIRMED
Mark  PLANNED
Mission  PARTIALLY_COMPLETED
```

### Real restart

Record a short live clip showing:

```text
Ctrl+C
old process stopped
new terminal command
new process started
same durable SQLite stores reopened
```

Do not replace this with an in-process function call.

### Agent Policy Engine after restart

Capture the central result:

```text
Anna → SKIP_VERIFIED
Mark → EXECUTE_MISSING
new KeeperHub calls for Anna: 0
```

### Mark execution and verification

Capture the same bounded sequence as Anna, including the independently matched amount of `70000` base units and `Mark → CHAIN_CONFIRMED`.

### Mission completion

Capture:

```text
MISSION COMPLETED
Anna  0.12 USDC  CHAIN_CONFIRMED
Mark  0.07 USDC  CHAIN_CONFIRMED
Verified total: 0.19 USDC
Unclassified amount: 0
```

### Complete Mission replay

Capture:

```text
Anna → SKIP_VERIFIED
Mark → SKIP_VERIFIED
new simulation POSTs: 0
new broadcast POSTs: 0
new transactions: 0
```

## Video composition

Use a combination of slides, screenshots, and short live clips:

1. title and tagline;
2. the multi-effect Mission problem;
3. Mission admission with zero provider calls;
4. Anna live proof and explorer evidence;
5. partial Mission state;
6. visible process restart;
7. `SKIP_VERIFIED / EXECUTE_MISSING` decision;
8. Mark live proof;
9. complete Mission replay with zero new action;
10. closing statement.

Closing statement:

> Durable Mission truth survives the process. Partial completion survives restart. Only missing economic effects continue.

## Capture quality

- target 1920×1080 and 16:9;
- use one terminal scale and readable font size;
- keep important output within one viewport;
- record live clips for 5–15 seconds where possible;
- retain the raw source even after producing a redacted copy;
- do not include API keys, authorization headers, seed phrases, private keys, full private configuration, raw provider payloads, or local user paths;
- review full addresses and provider references before public use;
- never claim provider acceptance as chain confirmation.

## Submission floor

Target proof:

```text
Anna live CHAIN_CONFIRMED
→ real restart
→ Anna SKIP_VERIFIED
→ Mark EXECUTE_MISSING
→ Mark live CHAIN_CONFIRMED
→ complete replay with zero new action
```

Minimum honest fallback, used only after the internal cutoff:

```text
Anna live CHAIN_CONFIRMED
→ real restart
→ Anna SKIP_VERIFIED
→ Mark EXECUTE_MISSING
→ Mark remains explicitly PENDING_RUNTIME
→ sandbox demonstrates continuation semantics without a false payment claim
```

No deadline pressure authorizes a blind retry, changed request key, weakened state transition, fabricated proof, or unsupported runtime claim.
