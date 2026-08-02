# KeeperHub Direct Execution Integration Contract

**Source review date:** 2026-08-02  
**Status:** OFFICIAL SCHEMA REVIEWED / LIVE ACTION NOT AUTHORIZED

This document pins the KeeperHub surfaces Nexus Vector will integrate against. It is a public implementation contract, not a credential file and not a transaction record.

## Official surfaces

Base URL:

```text
https://app.keeperhub.com/api
```

Authentication uses an organization API key with the `kh_` prefix in:

```text
Authorization: Bearer <organization-api-key>
```

The key value must never be committed, logged, placed in screenshots, copied into evidence, or passed through the Mission payload.

## Wallet readiness

Read-only wallet readiness:

```text
GET /api/user/wallet
```

Proceed only when all are true:

- `hasWallet` is `true`;
- `isActive` is `true`;
- `walletAddress` is a canonical EVM address;
- the returned `organizationId` is the intended active organization.

A response with `hasWallet: false` is a hard stop. Balances are a separate gate through the documented wallet-balance surface; Nexus Vector must not invent or assume its response schema.

## Chain selection

The live source of truth is:

```text
GET /api/chains
```

The selected chain must have both:

```text
isEnabled = true
isTestnet = true
```

Reviewed stable testnet constants:

| Network | chainId | USDC contract |
|---|---:|---|
| Base Sepolia | `84532` | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| Ethereum Sepolia | `11155111` | `0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238` |

Base Sepolia remains the preferred path. Mainnet remains blocked.

## Transfer request

Endpoint:

```text
POST /api/execute/transfer
```

Reviewed request fields:

```json
{
  "chainId": 84532,
  "recipientAddress": "<reviewed-testnet-recipient>",
  "amount": "<exact-human-unit-decimal-string>",
  "tokenAddress": "<reviewed-testnet-token>",
  "gasLimitMultiplier": "<optional-reviewed-decimal-string>"
}
```

Rules:

- `chainId` is canonical; the deprecated `network` alias is not used.
- `amount` is a decimal string in human-readable token units.
- conversion from immutable integer base units must be exact for the reviewed token decimals;
- no float conversion is allowed;
- the exact request material is fingerprinted before any provider write.

## Simulation gate

The exact intended transfer body is first sent with:

```json
{"simulate": true}
```

`simulate` must be the strict JSON boolean `true`, not a string or number. Simulation does not sign or broadcast, does not produce a transaction hash, and does not authorize a later write by itself.

Proceed only when the response is structurally valid and contains:

```text
success = true
status = simulated
wouldRevert = false
```

The broadcast body must be byte-for-byte equivalent in economic fields to the reviewed simulation body, with only `simulate` removed.

## One broadcast and idempotency

The broadcast request uses exactly one new `Idempotency-Key` header. Nexus Vector maps its durable `request_key` to this header.

KeeperHub documents these semantics:

- same key + same body replays the original response without executing again;
- same key + changed body returns `409 idempotency_conflict`;
- a concurrent duplicate can return `409 idempotency_in_progress`;
- stored responses are replayable for 24 hours.

The 24-hour replay window is a recovery aid, not a substitute for durable local provider-reference storage. A new key must never be generated merely because a response was lost.

## Broadcast response and durable provider reference

A successful transfer request returns HTTP `202 Accepted` with an `executionId` and status.

The returned `executionId` must be persisted durably against the canonical attempt **before** the attempt can be classified as `PROVIDER_ACKNOWLEDGED`.

Crash rules:

- response received, `executionId` persisted, state transition not finished: restart may continue from the durable provider reference;
- response may have arrived but `executionId` was not durably persisted: classify `EXECUTION_UNKNOWN`, reuse no new key, and enter reconciliation;
- persistence failure must never be converted into `FAILED_FINAL` or a second POST.

The stable execution-attempt schema remains unchanged. `SQLiteProviderExecutionReferenceStore` now provides a separate append-only journal, and `ProviderReferencePersistingPort` writes the reference before generic dispatch can acknowledge the attempt. This closes the P0 implementation gate before live broadcast at the persistence layer. KeeperHub transport wiring and live wallet readiness remain separate gates.

## Status polling and authoritative evidence

Endpoint:

```text
GET /api/execute/{executionId}/status
```

Honor `X-Poll-Interval-Hint`; do not poll on a fixed aggressive interval. Stop at terminal `completed` or `failed`.

The status response fields:

```text
transactionHash
transactionLink
```

are KeeperHub's authoritative provider-side onchain references. Nexus Vector still independently verifies the exact chain event before projecting the effect to `CHAIN_CONFIRMED` and the attempt to `VERIFIED`.

## Failure classification

| Observation | Nexus Vector action |
|---|---|
| simulation `wouldRevert = true` | block; no broadcast |
| malformed simulation | block/manual review |
| wallet not configured (`422`) | block; no broadcast |
| auth failure (`401`) | block; no broadcast |
| spending cap (`403`) | block; no broadcast |
| rate limit (`429`) before broadcast | wait according to `Retry-After`; no new request key |
| timeout/disconnect after broadcast may have reached KeeperHub | `EXECUTION_UNKNOWN`; no second POST |
| `409 idempotency_in_progress` | wait/reconcile same request key |
| `409 idempotency_conflict` | conflict/manual review; never change body under the same key |
| terminal status with transaction reference | independently verify exact event |

## Live-action gate

No live testnet broadcast is allowed until all are complete:

1. wallet and balances are confirmed locally;
2. chain and token constants are re-read from official surfaces;
3. exact recipient, amount, fee cap, Mission key, effect ID, request key and confirmation policy are approved;
4. durable provider-reference persistence is implemented and tested across restart;
5. simulation passes with the exact economic body;
6. private evidence capture and public redaction destinations are ready;
7. maximum broadcasts remains `1`.

Mainnet and blind retry remain blocked.
