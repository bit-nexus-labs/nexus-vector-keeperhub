# Runtime Readiness

**Reviewed:** 2026-08-03
**Product main at review start:** `cf2f43ce981e0a0158511fc9990dbf88d2ae5904`
**Policy:** TESTNET FIRST / MAINNET BLOCKED / NO BLIND RETRY

This file distinguishes implemented code from runtime evidence. `OFFLINE VERIFIED` does not mean that a transaction occurred.

## Readiness matrix

| Capability | Status | Evidence / boundary |
|---|---|---|
| Mission/effect identity and conflict detection | DONE | deterministic domain tests |
| Atomic Mission + effects persistence | DONE | real SQLite integration |
| Durable admission before execution | DONE | `RECEIVED → VALIDATED → PERSISTED` |
| One canonical attempt per effect | DONE | identity and conflict tests |
| `IN_FLIGHT` before provider call | DONE | dispatch and concurrency tests |
| Lost-response/restart reconciliation | DONE | real SQLite cross-store tests |
| Duplicate suppression and 12+7+11 continuation | DONE | unequal-amount deterministic planner tests |
| Execution Doctor | DONE | read-only policy/CLI tests |
| KeeperHub official schema contract | OFFLINE VERIFIED | documented official endpoints and testnet constants |
| Durable KeeperHub `executionId` | OFFLINE VERIFIED | append-only provider-reference journal and crash tests |
| Provider-reference preflight hardening | OFFLINE VERIFIED | schema validation and existing-reference guard before provider call |
| Direct Execution transfer mapper | OFFLINE VERIFIED | testnet-only intent, fingerprint, simulation/body parity, one idempotency key |
| KeeperHub status observer | OFFLINE VERIFIED | poll-hint and transaction-reference tests |
| Bounded HTTPS transport | OFFLINE VERIFIED | pinned host, no redirect/retry, bounded JSON, injected credentials |
| Wallet readiness parsing | OFFLINE VERIFIED | official `GET /api/user/wallet` fixture tests |
| Chain readiness parsing | OFFLINE VERIFIED | official bare-array `GET /api/chains` fixture tests |
| Authenticated wallet readiness | BLOCKED / EXACT SURFACE REQUIRED | prior authenticated attempt returned unsupported `403` non-JSON; no undocumented retry |
| Gas and token balances | WAITING FOR EXTERNAL STATE | balance must be read locally from an official/documented surface |
| Controlled simulation | WAITING FOR ACTION-SPECIFIC APPROVAL | exact private action sheet and separate simulation approval required |
| KeeperHub testnet broadcast | WAITING FOR ACTION-SPECIFIC APPROVAL | one-time broadcast approval and `--approve-testnet-write` required |
| Independent onchain verification | WAITING FOR TRANSACTION | exact ERC-20 event and confirmations required |
| Public live evidence | WAITING FOR TRANSACTION | explorer link and sanitized evidence update required |
| Mission Resilience Lab | MERGED / REPLAY / DEPLOY VERIFY PENDING | PR #32 bytes are on `main`; push-run, clean-browser behavior, and deployed hashes remain separate verification gates |
| Public evidence verifier | DONE | runtime false claims fail closed |
| Demo video | READY FOR RECORDING | runtime scene depends on real evidence or must be labeled pending |
| Submission draft | READY / LINKS PENDING | video and explorer links remain external gates; deployed frontend link requires final clean-browser verification |
| Mainnet | BLOCKED | not authorized for the hackathon path |

## Code-complete execution sequence

The implemented composition is:

```text
Mission admission
  → continuation policy selects EXECUTE_MISSING
  → execution attempt PREPARED
  → durable IN_FLIGHT CAS claim
  → immutable KeeperHub transfer intent fingerprint check
  → exact simulation (no idempotency key)
  → one broadcast (durable request key = Idempotency-Key)
  → durable provider execution reference
  → PROVIDER_ACKNOWLEDGED
  → read-only provider status observation
  → independent exact chain-event observation
  → effect CHAIN_CONFIRMED
  → attempt VERIFIED
  → continuation replans remaining effects
```

No step authorizes automatic resend after ambiguity.

## Required private action sheet

Use [`CONTROLLED_TESTNET_ACTION_SHEET_TEMPLATE.md`](CONTROLLED_TESTNET_ACTION_SHEET_TEMPLATE.md) outside the public repository immediately before a testnet action.

Minimum private fields:

```text
keeperhub_organization_id:
keeperhub_wallet_address:
chain_id:
chain_enabled_and_testnet:
token_contract:
token_decimals:
native_gas_balance:
token_balance_base_units:
sender_address:
recipient_address:
amount_base_units:
amount_decimal_string:
mission_key:
effect_id:
attempt_id:
request_key:
request_fingerprint:
minimum_confirmations:
maximum_testnet_amount:
maximum_fee:
maximum_simulation_posts: 1
maximum_broadcast_posts: 1
maximum_mutating_calls: 1
maximum_same_key_recovery_posts_after_ambiguity: 0
maximum_new_request_keys_after_ambiguity: 0
private_evidence_destination:
public_redaction_destination:
```

The simulation POST and broadcast POST are distinct provider calls. Only the broadcast is the single mutating call capable of moving testnet value.

The organization API key is not written into the action sheet. It is supplied locally and kept out of shell history, Git, chat, screenshots, logs, and evidence.

Simulation authorization and broadcast authorization are separate. Simulation success never implies permission to broadcast.

## Runtime stop conditions

Stop without a second broadcast when:

- wallet, organization, chain, balance, recipient, token, amount, fingerprint, fee, or confirmation policy is not exact;
- the live chain catalog differs from the reviewed plan;
- simulation fails, is malformed, or would revert;
- the request may have reached KeeperHub but the response is lost or ambiguous;
- the provider reference cannot be persisted;
- `idempotency_conflict` or `idempotency_in_progress` is returned;
- status identity, poll hint, transaction hash, or explorer link is malformed;
- independent event verification is unavailable, insufficiently confirmed, ambiguous, or contradictory;
- local Mission/attempt/provider-reference state cannot be read back after restart.

The safe state is `EXECUTION_UNKNOWN`, reconciliation, or manual review—not a new request key.

A same-key recovery POST after an ambiguous broadcast remains blocked until KeeperHub confirms the exact supported procedure and that procedure is separately reviewed. A new key for the same ambiguous economic effect is forbidden.

## Office-hours gate

The focused question pack for 2026-08-04 is [`KEEPERHUB_OFFICE_HOURS_20260804.md`](KEEPERHUB_OFFICE_HOURS_20260804.md).

The runtime gate may advance only after exact answers resolve:

- Direct Execution testnet eligibility;
- organization-wallet readiness surface and schema;
- gas and token balance readiness;
- lost-response recovery when no `executionId` was received;
- idempotency retention and replay semantics;
- status/polling/transaction evidence contract;
- required public submission evidence.

A vague answer remains a blocker.

## Claims allowed now

Safe public claims:

- the offline Mission, persistence, attempt, reconciliation, continuation, provider-reference, adapter, status, transport, UI, and evidence layers are implemented and tested;
- KeeperHub integration follows the reviewed simulation-first and idempotent Direct Execution contract;
- the Mission Resilience Lab is a replay product with no transaction capability; PR #32 deployment verification is still pending;
- no real KeeperHub transaction is claimed yet;
- mainnet is blocked.

Claims not allowed yet:

- “KeeperHub paid the recipient”;
- “30/30 completed live”;
- “the explorer evidence proves the replay”;
- “deployment is production-ready”;
- any transaction hash, request ID, balance, wallet, or recipient not captured and reviewed from the actual controlled run.
