# KeeperHub Readiness STOP Analysis — 2026-08-03

## Observed sanitized result

```json
{"probe":"KEEPERHUB_READINESS_V1","reason":"WALLET_READINESS_UNKNOWN","retry":"FORBIDDEN","status":"STOP"}
```

## What is known

- The probe reached the first approved read-only surface: `GET /api/user/wallet`.
- No chain-catalog or balance request was attempted after the wallet-readiness failure.
- No simulation, signing, broadcast, Workflow mutation, MCP execution, x402, Marketplace, mainnet action, or funds movement occurred.
- The no-retry policy behaved correctly.

## Root observability defect

The first version of `get_wallet_readiness()` collapsed every non-200 response into `WALLET_READINESS_UNKNOWN`. The decoded HTTP response was already available, but the adapter discarded:

- the HTTP status;
- the stable provider `error` code.

This made authentication rejection, insufficient scope, endpoint absence, rate limiting, and provider outage indistinguishable. It was an observability defect, not evidence that any one of those causes actually occurred.

## Hardened diagnostic contract

A subsequent reviewed diagnostic attempt may expose only:

- a bounded local reason code;
- numeric `http_status`;
- a provider `error` value only when it matches the strict pattern `[a-z][a-z0-9_]{0,63}`.

It must never expose:

- API keys;
- response `detail` or `hint` prose;
- request IDs;
- organization, wallet, user, or provider identifiers;
- raw response bodies.

## Failure mapping

| HTTP status | Local reason suffix |
| --- | --- |
| 401 | `AUTHENTICATION_REJECTED` |
| 403 | `SCOPE_REJECTED` |
| 404 | `ENDPOINT_NOT_FOUND` |
| 409 | `CONFLICT` |
| 429 | `RATE_LIMITED` |
| 5xx | `PROVIDER_UNAVAILABLE` |
| other | `HTTP_REJECTED` |

The surface prefix remains explicit, for example `WALLET_READINESS_SCOPE_REJECTED`.

## Retry rule

The failed first request is not retried blindly. A second request is permitted only after:

1. the diagnostic patch is reviewed and merged;
2. CI is green;
3. the local checkout is synchronized to the reviewed merge commit;
4. the operator intentionally starts one new read-only diagnostic attempt.

The second attempt remains one call per surface, stops at the first failure, and does not authorize simulation.

## Safe state

```text
AUTHENTICATED READINESS: STOPPED
ROOT CAUSE: UNKNOWN — DIAGNOSTIC INFORMATION LOST
BLIND RETRY: FORBIDDEN
SIMULATION: NOT PERFORMED
SIGNING: NOT AUTHORIZED
BROADCAST: NOT AUTHORIZED
MAINNET: BLOCKED
FUNDS MOVEMENT: NONE
```
