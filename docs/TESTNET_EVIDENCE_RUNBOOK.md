# Controlled KeeperHub Testnet Evidence Runbook

**Status: PLAN ONLY — NOT A TRANSACTION AUTHORIZATION**

This runbook defines the evidence and stop conditions required before one controlled testnet write. It does not contain credentials, wallet addresses, token addresses, recipients, amounts, or request keys.

## Required gates

All must be resolved before a POST:

- exact official KeeperHub wallet-readiness surface confirmed;
- organization API key entered locally and never copied into chat, logs, Git, screenshots, or public evidence;
- exact documented endpoint and request/response schema reviewed;
- Base Sepolia `84532` primary path confirmed, or explicit fallback decision;
- token contract, decimals, sender, recipient, amount, Mission key, effect ID, request key, minimum confirmations, and maximum fee defined;
- sender balance and gas readiness checked;
- one-call/no-blind-retry stop policy active;
- durable Mission and execution attempt created before the provider call;
- independent read-only chain verifier configured for the exact ERC-20 event;
- private evidence destination and public redaction plan prepared.

## Exact action sheet

Complete privately immediately before execution:

```text
chain_id:
token_contract:
token_decimals:
sender_address:
recipient_address:
amount_base_units:
mission_key:
effect_id:
keeperhub_request_key:
minimum_confirmations:
maximum_provider_calls: 1
maximum_broadcasts: 1
maximum_testnet_amount:
maximum_fee:
```

## Stop conditions

Stop with no retry when any of these occurs:

- authentication or wallet readiness is not exact;
- schema differs from the reviewed fixture;
- HTTP response is malformed, truncated, non-JSON when JSON is required, or otherwise unsupported;
- timeout, disconnect, process crash, or lost response after the request may have reached KeeperHub;
- provider status is ambiguous;
- request key or canonical effect identity differs from the approved sheet;
- balance, fee, chain, token, sender, recipient, or amount differs;
- local Mission/attempt state cannot be read back durably;
- independent chain observation is unavailable or contradictory.

The resulting state is `EXECUTION_UNKNOWN` or manual review—not a second POST.

## Evidence capture

Private capture:

- exact approved action sheet;
- sanitized request fingerprint, not the API key or raw authorization header;
- local Mission/effect/attempt revisions before and after the call;
- provider response classification;
- transaction hash only when returned or independently discovered;
- exact ERC-20 event fields and confirmation count;
- reconciliation and Doctor output;
- timestamps in UTC.

Public capture after redaction review:

- explorer URL;
- chain ID;
- token contract;
- transaction hash;
- exact intended recipient and amount only if approved for publication;
- independently matched event index/fingerprint;
- Mission/effect state transition summary;
- explicit `TESTNET / LIVE` label.

## Acceptance

A runtime claim is accepted only when the independently observed event matches:

```text
chain + token + expected sender + recipient + integer base-unit amount
```

at or above the approved confirmation threshold. Provider acceptance alone is insufficient.
