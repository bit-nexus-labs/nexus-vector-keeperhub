# High-Level Architecture

PRODUCT-P01 establishes only the pure Mission Identity foundation. The rest of
this document defines intended boundaries, not implemented capabilities.

## Directional boundaries

```text
Mission Core -> provider-neutral execution port -> KeeperHub adapter
KeeperHub observation -> adapter classification -> Mission Core reconciliation
```

Mission Core is the authority for business-level safety. It will own:

- durable Mission and effect identity;
- Mission state rules and allowed transitions;
- persistence policy and atomicity boundaries;
- restart recovery;
- business-level deduplication;
- retention and tombstone policy;
- reconciliation policy;
- independent verification decisions.

The KeeperHub adapter will own only translation between provider-neutral
requests or observations and KeeperHub-specific representations. It must not
become the authority for Mission identity, state, retry permission, or payment
proof.

## Evidence and retry rules

Adapter or provider success alone is not proof that the intended recipient was
paid. Mission Core must make independent verification decisions from evidence
appropriate to the future execution design.

An unknown provider outcome never authorizes automatic retry. Future retry
decisions must fail closed until reconciliation determines whether a prior
effect may already have occurred.

## Implemented in PRODUCT-P01

The repository currently contains only deterministic, versioned Mission and
effect identity derivation plus conflict classification. This code is pure,
provider-neutral, standard-library-only, and performs no I/O or external
action.

## Planned, not implemented

The following are explicitly deferred:

- MissionRequest and other application-facing contracts;
- the Mission state machine;
- durable persistence and transaction boundaries;
- restart recovery;
- retention and tombstones;
- provider-neutral execution ports;
- the KeeperHub adapter;
- observation classification and reconciliation;
- independent onchain verification;
- execution orchestration and user interfaces.

No element of PRODUCT-P01 executes, simulates, signs, broadcasts, or verifies a
payment, and no product-readiness claim follows from this bootstrap.
