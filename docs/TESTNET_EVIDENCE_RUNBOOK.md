# Controlled KeeperHub Testnet Evidence Runbook

**Status: PLAN ONLY — NOT A TRANSACTION AUTHORIZATION**

This runbook defines the evidence and stop conditions required before one controlled testnet write. It does not contain credentials, wallet addresses, recipients, amounts, request keys, or transaction claims.

The reviewed provider contract is pinned in [`KEEPERHUB_DIRECT_EXECUTION_CONTRACT.md`](KEEPERHUB_DIRECT_EXECUTION_CONTRACT.md).

## Confirmed official surfaces

```text
GET  /api/user/wallet
GET  /api/chains
POST /api/execute/transfer
GET  /api/execute/{executionId}/status
```

Authentication requires a local organization API key. No key value may enter Git, chat, logs, screenshots, Mission content, or public evidence.

## Required gates

All must be resolved before a broadcast:

- `GET /api/user/wallet` confirms `hasWallet = true`, `isActive = true`, the intended organization, and a canonical wallet address;
- gas and test-token balances are confirmed through a documented local surface without assuming an undocumented response shape;
- `GET /api/chains` confirms the chosen chain is both enabled and testnet;
- Base Sepolia `84532` primary path is confirmed, or an explicit Ethereum Sepolia fallback is approved;
- exact token contract, decimals, sender, recipient, amount, Mission key, effect ID, request key, minimum confirmations, and maximum fee are defined;
- immutable integer base units convert exactly to the provider decimal-string amount, with no float conversion;
- one-call/no-blind-retry stop policy is active;
- durable Mission and execution attempt exist before the provider write;
- durable provider-reference persistence is implemented so KeeperHub `executionId` survives restart before `PROVIDER_ACKNOWLEDGED`;
- independent read-only chain verifier is configured for the exact token event;
- private evidence destination and public redaction plan are prepared.

## Exact action sheet

Complete privately immediately before execution:

```text
chain_id:
token_contract:
token_decimals:
sender_address:
recipient_address:
amount_base_units:
amount_human_decimal_string:
mission_key:
effect_id:
keeperhub_request_key:
request_fingerprint:
minimum_confirmations:
maximum_provider_calls: 1
maximum_broadcasts: 1
maximum_testnet_amount:
maximum_fee:
```

## Simulation gate

Send the exact reviewed transfer body to `POST /api/execute/transfer` with strict JSON boolean:

```json
{"simulate": true}
```

Continue only when all are true:

```text
success = true
status = simulated
wouldRevert = false
```

Simulation is read-only with respect to broadcast. It is not transaction evidence and does not authorize changing economic fields before the write.

## Broadcast gate

Remove only `simulate`; do not change chain, token, recipient, amount, or gas policy. Send one POST with the durable request key mapped to `Idempotency-Key`.

KeeperHub's same-key/same-body replay is a recovery mechanism. It does not authorize a second economic intent, a changed body, or a new key after ambiguity. The documented replay window is 24 hours, so local durable `executionId` storage is mandatory.

Immediately after HTTP `202 Accepted`:

1. validate `executionId` and provider status;
2. persist `executionId` against the canonical attempt;
3. only then transition to `PROVIDER_ACKNOWLEDGED`;
4. poll `GET /api/execute/{executionId}/status` while honoring `X-Poll-Interval-Hint`;
5. capture `transactionHash` and `transactionLink` only from the authoritative status response;
6. independently verify the exact chain event before `VERIFIED`.

## Stop conditions

Stop with no retry and no blind retry when any of these occurs:

- authentication or wallet readiness is not exact;
- wallet or token balance is insufficient or not reliably known;
- schema differs from the reviewed fixture;
- simulation is malformed, unsuccessful, or would revert;
- HTTP response is malformed, truncated, non-JSON when JSON is required, or otherwise unsupported;
- timeout, disconnect, process crash, or lost response after the request may have reached KeeperHub;
- provider status is ambiguous;
- `executionId` cannot be persisted durably;
- `409 idempotency_conflict` occurs;
- request key or canonical effect identity differs from the approved sheet;
- balance, fee, chain, token, sender, recipient, or amount differs;
- local Mission/attempt state cannot be read back durably;
- independent chain observation is unavailable or contradictory.

The resulting state is `EXECUTION_UNKNOWN`, `BLOCKED`, or manual review—not a new key and not a second POST.

`409 idempotency_in_progress` and `429` do not authorize a changed request. Follow documented waiting hints and reconcile the same intent.

## Evidence capture

Private capture:

- exact approved action sheet;
- sanitized request fingerprint, not the API key or raw authorization header;
- local Mission/effect/attempt revisions before and after the call;
- durable KeeperHub `executionId`;
- provider response classification;
- `transactionHash` and `transactionLink` only when returned by status or independently discovered;
- exact token event fields and confirmation count;
- reconciliation and Doctor output;
- timestamps in UTC.

Public capture after redaction review:

- explorer URL;
- chain ID;
- token contract;
- transaction hash;
- exact intended recipient and amount only when approved for publication;
- independently matched event index/fingerprint;
- Mission/effect state transition summary;
- explicit `TESTNET / LIVE` label.

## Acceptance

A runtime claim is accepted only when the independently observed event matches:

```text
chain + token + expected sender + recipient + integer base-unit amount
```

at or above the approved confirmation threshold. Provider acceptance alone is insufficient; `executionId` alone is also insufficient.
