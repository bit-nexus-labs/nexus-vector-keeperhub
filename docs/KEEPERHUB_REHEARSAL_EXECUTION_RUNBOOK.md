# KeeperHub Rehearsal Execution Runbook

## Purpose

Run one real KeeperHub Direct Execution rehearsal on **Base Sepolia** using a fresh Mission/effect identity and a minimal `0.000001 USDC` transfer.

This runner exists for pre-video technical rehearsal. It is intentionally separate from the future Anna + Mark flagship Mission.

The lifecycle is:

```text
prepare (0 network calls)
  -> simulate (maximum 1 POST)
  -> separate broadcast approval
  -> broadcast (maximum 1 POST)
  -> provider-status (read-only GET, one observation per invocation)
  -> independent chain verification (separate gate)
```

A provider `completed` status is **not** converted into `CHAIN_CONFIRMED` by this tool. Independent onchain verification remains required.

## Fixed economic boundary

- chain: Base Sepolia (`84532`);
- token: official Base Sepolia USDC (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`);
- decimals: `6`;
- amount: `1` base unit (`0.000001 USDC`);
- recipient: one locally entered testnet EVM address;
- mainnet: unavailable;
- simulation POSTs: maximum `1` per rehearsal effect;
- broadcast POSTs: maximum `1` per rehearsal effect;
- mutating calls: maximum `1`;
- same-key recovery POSTs after ambiguity: `0`;
- new request keys after ambiguity: `0`.

Each rehearsal requires a new `run-ref`, for example:

```text
rehearsal-a-20260808-01
rehearsal-b-20260809-01
```

Never reuse a completed, rejected, or ambiguous rehearsal identity for another economic effect.

## Safety properties

The tool reuses the existing Nexus Vector safety components:

- durable SQLite Mission store;
- durable execution-attempt journal;
- durable simulation/broadcast authorization ledger;
- separate simulation and broadcast approvals;
- `IN_FLIGHT` persisted before provider broadcast;
- one durable KeeperHub request key mapped to `Idempotency-Key`;
- KeeperHub `executionId` persisted before `PROVIDER_ACKNOWLEDGED`;
- no automatic simulation retry;
- no automatic broadcast retry;
- no automatic status polling;
- mainnet unavailable by construction.

The KeeperHub API key is supplied locally. Do not place it in Git, chat, screenshots, logs, evidence, or command history.

## Step 1 — prepare locally

Choose a fresh rehearsal `run-ref` and a Base Sepolia EVM recipient that is not the KeeperHub sender wallet.

From the repository root:

```powershell
$runRef = "rehearsal-a-20260808-01"
$recipient = Read-Host "Base Sepolia rehearsal recipient address"

try {
    $env:NEXUS_VECTOR_REHEARSAL_RECIPIENT = $recipient
    python .\tools\keeperhub_rehearsal_execution.py prepare --run-ref $runRef
    $prepareExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:NEXUS_VECTOR_REHEARSAL_RECIPIENT -ErrorAction SilentlyContinue
    $recipient = $null
}

$prepareExit
```

Expected result:

```text
status = PREPARED
network_calls = 0
chain_id = 84532
amount = 0.000001
maximum_simulation_posts = 1
maximum_broadcast_posts = 1
broadcast_authorized = false
mainnet_allowed = false
```

Preparation persists the real Mission locally and advances it only to `READY_FOR_EXECUTION`. The effect remains `PLANNED`.

Review the masked preview before simulation.

## Step 2 — execute one simulation

Simulation requires the exact `simulation_approval_challenge` from the prepared preview.

```powershell
$secret = Read-Host "KeeperHub organization API key" -AsSecureString
$approval = Read-Host "Exact simulation approval challenge"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)

try {
    $env:KEEPERHUB_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $env:NEXUS_VECTOR_REHEARSAL_SIM_APPROVAL = $approval

    python .\tools\keeperhub_rehearsal_execution.py simulate --run-ref $runRef
    $simulationExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:KEEPERHUB_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:NEXUS_VECTOR_REHEARSAL_SIM_APPROVAL -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    $secret = $null
    $approval = $null
    $bstr = [IntPtr]::Zero
}

$simulationExit
```

A PASS returns a new **broadcast** challenge but still reports:

```text
broadcast_authorized = false
funds_movement = NONE_FROM_SIMULATION
```

Stop after simulation and review the exact result before authorizing broadcast.

## Step 3 — execute one separately approved broadcast

Broadcast requires both:

1. the exact `broadcast_approval_challenge` produced by the successful simulation;
2. the explicit runtime flag `--approve-testnet-write`.

```powershell
$secret = Read-Host "KeeperHub organization API key" -AsSecureString
$approval = Read-Host "Exact broadcast approval challenge"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)

try {
    $env:KEEPERHUB_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $env:NEXUS_VECTOR_REHEARSAL_BROADCAST_APPROVAL = $approval

    python .\tools\keeperhub_rehearsal_execution.py broadcast `
      --run-ref $runRef `
      --approve-testnet-write
    $broadcastExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:KEEPERHUB_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:NEXUS_VECTOR_REHEARSAL_BROADCAST_APPROVAL -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    $secret = $null
    $approval = $null
    $bstr = [IntPtr]::Zero
}

$broadcastExit
```

A successful KeeperHub acceptance must produce exactly one broadcast POST, persist the provider reference, and advance local state to:

```text
attempt = PROVIDER_ACKNOWLEDGED
Mission = VERIFYING
effect = SUBMITTED
```

At this point funds movement is deliberately classified as:

```text
UNKNOWN_PENDING_CHAIN_VERIFICATION
```

Do not send a second broadcast.

## Step 4 — provider status

After KeeperHub returns an execution reference, query status read-only:

```powershell
$secret = Read-Host "KeeperHub organization API key" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)

try {
    $env:KEEPERHUB_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    python .\tools\keeperhub_rehearsal_execution.py provider-status --run-ref $runRef
    $statusExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:KEEPERHUB_API_KEY -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    $secret = $null
    $bstr = [IntPtr]::Zero
}

$statusExit
```

One invocation performs one status GET. If KeeperHub reports `pending` or `running`, honor `poll_after_seconds` before another status observation.

If KeeperHub reports `completed`, the tool may expose the public transaction hash/link and sets:

```text
requires_independent_chain_verification = true
```

Local Mission/effect state remains `VERIFYING / SUBMITTED` until independent chain evidence is separately validated.

## Local status after restart

This command performs zero network calls:

```powershell
python .\tools\keeperhub_rehearsal_execution.py status --run-ref $runRef
```

It reports only durable local state and masked identifiers.

## Stop conditions

Stop with **no second broadcast** when any of these occur:

- simulation is rejected, malformed, or ambiguous;
- the broadcast response is lost, malformed, timed out, disconnected, or otherwise ambiguous;
- durable provider-reference persistence is uncertain;
- local Mission/attempt state is inconsistent;
- the exact approval challenge does not match;
- the runtime flag is absent;
- recipient, token, chain, amount, request fingerprint, or action sheet differs;
- KeeperHub returns an unsupported response;
- provider status is contradictory or malformed.

The safe continuation is reconciliation/manual review, not a new key and not another POST for the same effect.

## Video boundary

Do not use rehearsal identities as the final Anna + Mark submission Mission. After the rehearsal path is verified, create a fresh Anna + Mark Mission with new Mission/effect/request identities and repeat the already-proven workflow for the recorded demo.
