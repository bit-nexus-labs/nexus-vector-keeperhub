# KeeperHub MCP OAuth Read Handshake V1

## Purpose

This is a one-shot diagnostic that determines whether the authenticated
KeeperHub account can obtain an OAuth token scoped to `mcp:read` and establish
an org-scoped MCP session independently of the blocked `kh_` Organisation API
key path.

It is not an execution test and does not retry or replace the terminal REST
simulation effect.

## Exact capability boundary

The probe may perform only this fixed sequence:

1. `GET /.well-known/oauth-protected-resource`
2. `GET /.well-known/oauth-authorization-server`
3. `POST /api/oauth/register` for one public PKCE client explicitly scoped to
   `mcp:read`
4. User-interactive browser login and consent
5. `POST /api/oauth/token` for the one-time authorization-code exchange
6. `POST /mcp` with `initialize`
7. `POST /mcp` with `notifications/initialized`
8. `POST /mcp` with `tools/list`
9. At most one `DELETE /mcp` to close a created MCP session

The network budget fails closed before any duplicate stage, different endpoint,
different HTTP method, or different MCP JSON-RPC method.

The probe has no `tools/call`, workflow invocation, Direct Execution,
simulation, signing, broadcast, x402, Marketplace call, mainnet action, or
funds-moving capability.

## Persistent side effects

KeeperHub dynamic client registration creates one OAuth client record. The
probe does not have a verified client-deletion endpoint, so this record may
remain in KeeperHub after the diagnostic.

A successful token exchange also causes KeeperHub to issue a refresh token.
The access token, refresh token, authorization code, client ID, PKCE verifier,
and MCP session ID are kept only in process memory and are never printed or
written to durable state. Python does not guarantee physical zeroization of
immutable strings, but the probe drops all references before exit.

## Consent requirements

Before approving, confirm that the consent screen names the intended active
organisation and that only **Read** is selected. Do not enable Write or Admin.

The probe independently verifies that the token response grants exactly:

```text
mcp:read
```

A broader, empty, or different scope stops before MCP initialization.

When KeeperHub displays **Create an account to continue**, return to the
terminal, type `A`, and press Enter. This records a sanitized terminal outcome
without attempting token exchange.

## Durable one-shot state

Before the first external request, the probe creates:

```text
results_private/keeperhub_mcp_oauth_read_handshake_v1_state.json
```

The path is ignored by Git. Any existing claim blocks all network requests.
Do not delete, rename, edit, or reset this file to rerun the experiment.
Timeouts, disconnections, malformed responses, user cancellation after claim,
and ambiguous provider outcomes require manual recovery and do not permit an
automatic retry.

## Result interpretation

### `MCP_OAUTH_READ_HANDSHAKE_SUCCEEDED`

OAuth and authenticated MCP work with `mcp:read`. The observed blocker is then
localized more narrowly to the Organisation `kh_` API-key authentication path.

### `ACCOUNT_CLASSIFIED_AS_ANONYMOUS_BY_OAUTH_UI`

KeeperHub's OAuth consent surface classifies the current account as anonymous.
This strongly supports a stale or inconsistent backend user record and should
be escalated to KeeperHub support.

### Failure during `TOKEN_EXCHANGE`

The browser consent completed, but KeeperHub refused or malformed the token
exchange. The sanitized HTTP status, response surface, and support request ID
should be supplied to support.

### Failure during `MCP_INITIALIZE`

OAuth token issuance succeeded, but the authenticated MCP endpoint rejected or
malformed initialization. This indicates a broader MCP/auth context problem
rather than only the Organisation-key route.

### Failure during `MCP_TOOLS_LIST`

OAuth and MCP session initialization succeeded, but the read-only tool catalog
could not be listed. The session cleanup is attempted exactly once.

## Run command

From the repository root, after synchronizing a clean `main`:

```powershell
python .\tools\keeperhub_mcp_oauth_read_probe.py
$LASTEXITCODE
```

Run the command exactly once. The existing Organisation API keys are not used
or requested by this probe.
