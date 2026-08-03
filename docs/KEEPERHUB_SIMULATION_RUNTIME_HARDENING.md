# KeeperHub Simulation Runtime Hardening

## Scope

This change prepares the first authenticated KeeperHub runtime simulation without granting signing, broadcast, Workflow mutation, MCP execution, x402, Marketplace, mainnet, or funds authority.

The approved runtime sequence is:

1. Read wallet readiness.
2. Read wallet balances.
3. Read the current chain catalog.
4. Select an enabled testnet chain.
5. Freeze one private canonical effect and action sheet.
6. Consume the durable simulation authorization claim.
7. Perform at most one simulation POST for that effect.
8. Persist a sanitized receipt and stop for review.

No broadcast follows automatically.

## Capability boundaries

`KeeperHubReadOnlyRuntimeClient` exposes only:

- `GET /api/user/wallet`
- `GET /api/user/wallet/balances`
- `GET /api/chains`

It has no transfer or execution-status method.

`KeeperHubSimulationOnlyTransport` exposes only a transfer-shaped simulation call. It requires:

- `simulate` to be the boolean `true`;
- the approved transfer field set only;
- no `Idempotency-Key`;
- exactly one underlying HTTP call with no retry.

The response is rejected fail-closed if it contains execution or broadcast evidence such as:

- `executionId`;
- `transactionHash`;
- `transactionLink`;
- signed/raw transaction material;
- audit or reservation identifiers;
- broadcast lifecycle statuses.

## Ambiguity policy

A timeout, disconnect, invalid content type, malformed JSON, oversized response, or anomalous simulation response is not classified as a safe failure. The durable authorization layer must record `OUTCOME_UNKNOWN`, consume the effect's simulation authority, and prohibit automatic retry.

## Credential policy

The organization API key remains locally injected into `KeeperHubHttpTransport` and is never read by this module. It must not be placed in chat, Git, Drive, logs, command history, exception messages, or serialized evidence.

## First runtime evidence

The first runtime attempt must use a fresh private testnet effect. Public demo recipient addresses must not be reused as real runtime recipients. The action sheet records the exact chain, token contract, recipient, amount, request fingerprint, and simulation approval reference before the POST.

A successful simulation is evidence only that the frozen request is eligible for a later, separately approved broadcast. It is not settlement proof and does not authorize funds movement.
