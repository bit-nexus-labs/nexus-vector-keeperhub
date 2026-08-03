# KeeperHub Readiness Probe Runbook

## Purpose

Run the three authorized read-only KeeperHub checks from the local machine that already holds the organization API key:

1. `GET /api/user/wallet`
2. `GET /api/chains`
3. `GET /api/user/wallet/balances`

This probe has no simulation, signing, broadcast, Workflow, MCP, x402, Marketplace, or mainnet execution capability.

## Preconditions

- Use the canonical repository checkout on current `main`.
- Confirm `git status --short` is clean.
- Do not paste the API key into chat, Git, Drive, a script file, `.env`, or a command-line argument.
- Do not redirect output to a tracked file.
- Run only once unless the result is an explicit local pre-call validation failure such as `LOCAL_API_KEY_NOT_SET`. A network or provider ambiguity is terminal for this probe session.

## Windows PowerShell

Run from the repository root. The key is entered through a secure prompt, placed in the child process environment only for the probe, then removed.

```powershell
$secret = Read-Host "KeeperHub organization API key" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $env:KEEPERHUB_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    python .\tools\keeperhub_readiness_probe.py
    $probeExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:KEEPERHUB_API_KEY -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    $secret = $null
    $bstr = [IntPtr]::Zero
}
$probeExit
```

The raw key is not part of the command history or JSON output.

## Result interpretation

### PASS

Expected high-level fields:

```json
{
  "probe": "KEEPERHUB_READINESS_V1",
  "status": "PASS",
  "reason": "READINESS_SURFACES_PASS"
}
```

A PASS means:

- the organization wallet exists and is active;
- Base Sepolia `84532` is currently enabled as an EVM testnet;
- the balance endpoint returned a supported JSON envelope;
- each approved surface was called at most once;
- no simulation or transaction was performed.

The output masks wallet and token addresses and redacts organization, wallet and email identifiers. Balance numbers and token symbols may remain visible locally because they are needed for readiness review. Do not publish or paste the complete output into a public channel.

### STOP

Any `STOP` result is fail-closed. Examples:

- `WALLET_NOT_READY`
- `BASE_SEPOLIA_NOT_ELIGIBLE`
- `WALLET_READINESS_UNKNOWN`
- `WALLET_BALANCES_UNKNOWN`
- `CHAIN_CATALOG_UNKNOWN`
- `NETWORK_OUTCOME_UNKNOWN`
- `INVALID_RESPONSE_CONTENT_TYPE`
- `UNEXPECTED_READINESS_FAILURE`

For network, authentication, schema, redirect, or provider ambiguity:

- do not retry automatically;
- do not switch endpoint or credential;
- do not proceed to simulation;
- preserve only the sanitized JSON result and the local timestamp;
- classify the result for manual review.

## Post-probe state

Even after PASS:

- simulation remains limited to one POST per separately agreed canonical effect;
- no recipient, token, amount, or action sheet is implied by this probe;
- signing and broadcast remain unauthorized;
- `--approve-testnet-write` is not accepted or used by this command;
- funds movement remains zero.
