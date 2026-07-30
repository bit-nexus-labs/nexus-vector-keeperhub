# Nexus Vector — Mission-Safe Agent Payments

> The agent can retry. The money cannot duplicate.

Nexus Vector is in its product-bootstrap phase. PRODUCT-P01 is offline-only
and establishes the first provider-neutral Mission Core domain primitive.

## Product boundary

Mission Core owns business identity and conflict classification independently
of any payment provider. A future KeeperHub adapter will translate provider
requests and observations across a provider-neutral execution port; it will not
own durable Mission or effect identity.

The only implemented product capability is deterministic, versioned Mission
and effect identity with changed-payload conflict detection. The implementation
uses only the Python standard library.

## Not implemented

Payment execution, simulation, wallet operations, persistence, state-machine
transitions, reconciliation, KeeperHub integration, testnet/mainnet
transactions, deployment, and production readiness are not implemented or
authorized by this bootstrap.

## Safety boundary

Do not store secrets, credentials, `.env` content, raw private evidence, wallet
material, or real recipient data in this repository. Provider success must not
be represented as proof that a recipient was paid.

## Offline tests

From the repository root:

```powershell
py -m pytest -q
```
