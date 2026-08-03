# KeeperHub Engineering Office Hours — 2026-08-04

**Scheduled:** 2026-08-04 13:00 Europe/Kyiv  
**Status:** QUESTION PACK ONLY — NOT RUNTIME AUTHORIZATION  
**Policy:** TESTNET FIRST / MAINNET BLOCKED / NO BLIND RETRY

This document contains the narrow unresolved questions that block a controlled Nexus Vector testnet proof. It contains no API key, wallet address, recipient, transaction hash, execution identifier, balance, or private provider payload.

## Desired outcome

Leave the session with written, implementation-level answers that either:

1. unblock a separately approved testnet simulation and one broadcast; or
2. keep the runtime gate closed with an exact unresolved dependency.

A broad product explanation is not sufficient. For every answer, request the exact UI path, endpoint, response field, status, or documented procedure.

## Priority questions

### 1. Hackathon eligibility of the integration path

**Yes-or-no question:** Does a project that uses KeeperHub Direct Execution on testnet, reads execution status, and publishes independently verified explorer evidence satisfy the KeeperHub integration requirement?

Follow-ups:

- Is a KeeperHub visual workflow, MCP integration, or mainnet execution required?
- Would one controlled live testnet transfer plus an interactive multi-effect recovery product be accepted as a real integration?
- Is more than one live transaction expected for judging, or is one exact live proof sufficient when the multi-effect behavior is demonstrated safely through replay and tested runtime code?

### 2. Exact organization-wallet readiness surface

The authenticated key check succeeded previously, while the attempted wallet-readiness request returned an unsupported `403` non-JSON response. We will not retry an undocumented surface.

Please provide one exact supported source of truth:

- KeeperHub UI navigation path; or
- API method and path;
- authentication scope required;
- current response schema;
- fields that prove the wallet belongs to the intended organization, is active, and is ready to execute.

Also clarify whether the public documentation for `GET /api/user/wallet` is current for organization API keys.

### 3. Gas and ERC-20 test-token balance readiness

What is the officially supported way to verify, before simulation:

- native gas balance on Base Sepolia;
- intended ERC-20 test-token balance;
- token contract and decimals;
- whether KeeperHub sponsors gas or requires the organization wallet to hold gas;
- whether any faucet or KeeperHub-provided test token is recommended for a hackathon proof?

We need a deterministic preflight surface, not an assumption from an undocumented response.

### 4. Lost response before `executionId` is received

Assume the exact broadcast POST may have reached KeeperHub, but the client receives no trustworthy response and therefore has no `executionId`.

What is the supported reconciliation procedure?

- Is there a read-only lookup by `Idempotency-Key`?
- Is same-key/same-body replay the intended recovery action after an ambiguous response?
- Which response proves that the original execution already exists?
- Can same-key/same-body replay ever create another execution?
- What must the client do after the documented idempotency-retention window expires?

Nexus Vector will not generate a new key or issue a blind second economic intent.

### 5. Exact idempotency semantics and retention

Please confirm:

- server retention duration for `Idempotency-Key`;
- same-key/same-body behavior while pending and after completion;
- same-key/changed-body behavior;
- exact semantics of `idempotency_in_progress` and `idempotency_conflict`;
- whether the retention window is measured from first acceptance, completion, or last replay;
- whether a repeated same-key request after expiry may produce a new execution.

This answer determines whether provider idempotency can be used only as short-term request recovery or as a durable business guarantee. Nexus Vector assumes only short-term request recovery.

### 6. Status, transaction evidence, and polling contract

For `GET /api/execute/{executionId}/status`, please confirm:

- the complete current status vocabulary;
- which status is terminal success;
- whether `transactionHash` and `transactionLink` are authoritative only in terminal success;
- whether one execution can expose more than one transaction hash;
- exact `X-Poll-Interval-Hint` units and maximum recommended polling duration;
- recovery procedure when status remains pending beyond the expected duration;
- whether a failed provider status can still have an onchain transaction that must be independently checked.

Provider completion will not be treated as recipient-payment proof without exact chain-event verification.

### 7. Submission and public evidence requirements

Please provide the exact expected public evidence format:

- is a Base Sepolia explorer URL accepted;
- must the link point to a transaction, KeeperHub execution, workflow run, or all of them;
- may `executionId` be redacted or hashed publicly;
- are wallet and recipient addresses expected to remain visible;
- must the video show a live transaction, or may it show a previously verified testnet transaction plus the recovery product;
- is a transaction performed before submission but independently verifiable at review time acceptable?

### 8. Safe multi-effect failure demonstration

Nexus Vector models a Mission containing several independent effects. One may already be verified, one may be missing, and one may be ambiguous.

What KeeperHub-supported testnet method can demonstrate a partial or ambiguous execution without risking repeated funds movement?

Acceptable answers must identify a controlled testnet mechanism. We will not intentionally use an unsafe recipient, random malformed body, wallet depletion, mainnet, or an uncontrolled network fault.

## Answer capture

Record each answer in this form:

```text
question_id:
answered_by:
answered_at_utc:
answer_summary:
exact_ui_path_or_endpoint:
exact_fields_or_statuses:
official_doc_or_message_reference:
confirmed:
remaining_unknown:
changes_runtime_gate: YES / NO
```

Do not record credentials, raw authorization headers, private wallet data, or unredacted provider payloads.

## Gate decision after the session

### Runtime gate may advance only when all are resolved

- Direct Execution testnet eligibility is explicit;
- exact wallet readiness source is known and successfully checked;
- gas and token balances are known through a reviewed source;
- exact chain, token, sender, recipient, integer amount, request key, fee cap, and confirmation policy are defined privately;
- simulation and one broadcast have separate action-specific authorizations;
- lost-response recovery does not require a blind new request;
- independent exact event verification is ready;
- public evidence and redaction requirements are known.

### Runtime gate remains closed when any answer is vague

Examples:

- “wallet readiness is available in KeeperHub” without an exact surface;
- “retry the request” without same-key/same-body semantics;
- “check the explorer” without exact event-matching requirements;
- “mainnet is more impressive” without a requirement ruling;
- “use a workflow” without confirming whether Direct Execution is eligible.

The safe outcome of an unresolved session is a documented blocker, not a speculative testnet write.
