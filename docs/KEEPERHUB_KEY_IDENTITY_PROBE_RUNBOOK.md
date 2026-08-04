# KeeperHub organization-key identity probe

## Purpose

This diagnostic proves whether KeeperHub's backend recognizes the exact local
`kh_` credential as one of the non-revoked API keys in the active
organization.

It performs exactly one request:

```text
GET https://app.keeperhub.com/api/keys
```

It cannot transfer funds, simulate a transaction, sign, broadcast, execute a
workflow, call MCP, use x402/Marketplace, create/revoke a key, or change wallet
state.

## Preconditions

- Repository `main` contains the reviewed probe.
- The local key-prefix comparison already returned:

```json
{"key_format":"PASS","organization_key_match":"MATCH","network_calls":0}
```

- The operator has the exact existing Organisation key locally.
- Do not create, rotate, revoke, paste, or publish another key.

## Request budget

```text
maximum_get_requests: 1
maximum_post_requests: 0
maximum_simulation_posts: 0
maximum_broadcast_posts: 0
funds_movement: impossible
```

The probe has no automatic retry. A timeout or network ambiguity requires
review before any repeat, even though the surface is read-only, so the support
evidence remains unambiguous.

## Windows PowerShell execution

Run from the repository root. Paste the key only into the secure prompt and
without quotes.

```powershell
$secret = Read-Host "KeeperHub Organisation API key" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)

try {
    $env:KEEPERHUB_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)

    python .\tools\keeperhub_key_identity_probe.py
    $probeExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:KEEPERHUB_API_KEY -ErrorAction SilentlyContinue

    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }

    $secret = $null
    $bstr = [IntPtr]::Zero
}

$probeExit
```

## Expected PASS

A successful identity proof has these properties:

```text
probe: KEEPERHUB_KEY_IDENTITY_V1
status: PASS
endpoint: GET /api/keys
get_requests: 1
post_requests: 0
simulation_posts: 0
broadcast_posts: 0
organization_key_match: MATCH
reason: ORGANIZATION_KEY_VISIBLE_TO_BACKEND
funds_moved: false
```

The output may include the safe key name and lifecycle timestamps. It never
includes the full API key, returned key prefixes, key IDs, creator identity,
wallet address, organization ID, raw response body, headers, or backend detail.

`support_request_id` is a generated correlation identifier intended for a
private support ticket. Do not publish it in README, screenshots, demo video,
or public submission artifacts.

## STOP outcomes

### HTTP rejection

```text
status: STOP
reason: ORGANIZATION_KEY_IDENTITY_HTTP_REJECTED
```

Preserve the `http_status`, optional allowlisted provider code, and
`support_request_id`. Do not expose raw `detail`, `hint`, response payload, or
headers.

### Key not visible

```text
status: STOP
reason: KEY_NOT_VISIBLE_IN_ACTIVE_ORGANIZATION
organization_key_match: MISMATCH
```

This means the credential authenticated enough to list an organization's
keys, but its returned prefix did not identify the local key. Treat this as an
organization-context contradiction and review with KeeperHub before further
runtime actions.

### Network ambiguity

```text
status: OUTCOME_UNKNOWN
reason: NETWORK_OUTCOME_UNKNOWN
```

Do not immediately repeat. Record the support request ID and inspect local
connectivity first.

## Interpretation matrix

| Result | Meaning | Next action |
|---|---|---|
| PASS | Backend sees the exact key in the active organization | Proceed to separately reviewed read-only MCP authentication test |
| 401 | Credential rejected | Review revocation/expiry/backend mapping; no new key by default |
| 403 `insufficient_scope` | `/api/keys` contradicts the documented organization-key scope | Escalate with support request ID |
| 200 but MISMATCH | Active organization context differs from the key identity | Stop and reconcile organization mapping |
| OUTCOME_UNKNOWN | Request result is ambiguous | No blind repeat; inspect connectivity and preserve evidence |

## Safety boundary

A PASS does not authorize simulation, transfer, broadcast, funding, or
mainnet. It proves only that the exact `kh_` credential is visible to the
backend under the active organization-key listing surface.
