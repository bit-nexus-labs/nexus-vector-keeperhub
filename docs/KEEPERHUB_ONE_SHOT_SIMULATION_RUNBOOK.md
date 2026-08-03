# KeeperHub One-Shot Simulation Runbook

## Purpose

Prepare and execute one diagnostic-only KeeperHub Direct Execution simulation for a fixed Base Sepolia USDC effect. The tool cannot broadcast, cannot accept an `Idempotency-Key`, and cannot retry after ambiguity.

The fixed economic template is:

- chain: Base Sepolia (`84532`);
- asset: USDC (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`);
- decimals: `6`;
- amount: `1` base unit (`0.000001 USDC`);
- recipient: one user-controlled testnet EVM address entered locally;
- surface: `DIRECT_EXECUTION`;
- simulation budget: `1` POST;
- broadcast budget: `0` POSTs.

A successful result proves only that KeeperHub accepted the exact frozen request as a simulation. It does not move funds and does not authorize a later broadcast.

## Safety boundaries

- Never paste the KeeperHub key or full recipient into chat, Git, Drive, screenshots, logs, or command history.
- Never run the Claude `testnet_mission_demo.js` file.
- Run `prepare` first. It performs zero network calls and creates an exclusive private action sheet under the current user's home directory.
- Review the sanitized preview before running `execute`.
- `execute` consumes the durable SIMULATION authorization before the POST.
- A timeout, disconnect, malformed response, anomalous response, or persistence uncertainty consumes the slot and becomes `OUTCOME_UNKNOWN` / no retry.
- `401`, `403`, `422`, or an explicit simulated revert is final for this effect.
- There is no broadcast subcommand, broadcast flag, mainnet option, Workflow/MCP path, signing path, or funds-moving capability.

## Step 1 — synchronize reviewed main

```powershell
git switch main
git pull --ff-only
git status --short
git rev-parse HEAD
```

Proceed only with a clean worktree and the reviewed merge commit that contains this runbook.

## Step 2 — prepare locally, without network

From the repository root:

```powershell
$recipient = Read-Host "Your own Base Sepolia EVM recipient address"
try {
    $env:NEXUS_VECTOR_SIMULATION_RECIPIENT = $recipient
    python .\tools\keeperhub_one_shot_simulation.py prepare
    $prepareExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:NEXUS_VECTOR_SIMULATION_RECIPIENT -ErrorAction SilentlyContinue
    $recipient = $null
}
$prepareExit
```

Expected status: `PREPARED`. Share only that sanitized JSON for review. It contains a masked recipient, canonical identifiers, fingerprints, and the approval challenge; it never contains the full recipient.

Do not run `execute` until the exact preview is reviewed.

## Step 3 — execute exactly one simulation after preview review

Use the exact `approval_challenge` from the prepared preview. Enter both values through prompts so the API key is not stored in command history:

```powershell
$secret = Read-Host "KeeperHub organization API key" -AsSecureString
$approval = Read-Host "Exact simulation approval challenge"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)

try {
    $env:KEEPERHUB_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $env:NEXUS_VECTOR_SIMULATION_APPROVAL = $approval

    python .\tools\keeperhub_one_shot_simulation.py execute
    $simulationExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:KEEPERHUB_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:NEXUS_VECTOR_SIMULATION_APPROVAL -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    $secret = $null
    $approval = $null
    $bstr = [IntPtr]::Zero
}

$simulationExit
```

Share only the sanitized JSON result. Do not share the private action sheet, SQLite files, key, full addresses, or raw provider response.

## Result interpretation

### PASS

Required properties include:

- `status = PASS`;
- `decision = ELIGIBLE_FOR_BROADCAST_APPROVAL`;
- `simulation_posts = 1`;
- `authorization_state = ELIGIBLE_FOR_BROADCAST_APPROVAL`;
- `provider_summary.http_status = 200`;
- `provider_summary.success = true`;
- `provider_summary.provider_status = simulated`;
- `provider_summary.would_revert = false`;
- `broadcast_authorized = false`;
- `funds_moved = false`.

STOP after PASS. Do not run the tool again and do not broadcast.

### Final rejection

A supported final rejection produces `STOP`, `REJECTED_FINAL`, and no retry for the same effect. Record the result and review whether the organization wallet, gas, USDC balance, credential scope, or request assumptions require a new separately authorized effect.

### Ambiguity

`OUTCOME_UNKNOWN`, network ambiguity, malformed/anomalous response, or database uncertainty is terminal for this effect:

```text
NO RETRY
NO NEW KEY
NO CHANGED PAYLOAD
NO BROADCAST
MANUAL REVIEW
```

## Read-only status after restart

```powershell
python .\tools\keeperhub_one_shot_simulation.py status
```

`status` performs zero network calls and reports only the masked preview plus durable authorization state.
