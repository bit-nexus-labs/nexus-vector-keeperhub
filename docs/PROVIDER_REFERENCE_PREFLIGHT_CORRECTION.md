# Provider Reference Preflight Hardening

## Status

P0 hardening for the durable provider-reference boundary.

## Risk addressed

Initializing or validating the local provider-reference journal only after a provider call leaves an avoidable ambiguity window: an external execution may be accepted before a known local schema or database problem is discovered.

## Hardened invariant

Before any provider call:

- canonical provider namespace and lookup identities are validated without raw-value echo;
- the provider-reference journal is initialized and schema-validated;
- an existing durable reference for the canonical attempt blocks another provider call;
- any local preflight failure results in zero provider calls.

After a provider call:

- the returned provider reference must be persisted before generic `PROVIDER_ACKNOWLEDGED`;
- durable-reference persistence failure becomes `EXECUTION_UNKNOWN`;
- neither ambiguity nor restart authorizes a new request key, blind retry, or second POST.

## Safety boundary

This hardening is offline and provider-neutral. It performs no HTTP request, KeeperHub authentication, wallet access, simulation, signing, broadcast, deployment, RPC call, secret access, or funds action.

No live execution is permitted until this hardening is merged, CI is green, exact diff review passes, wallet readiness is independently confirmed, and a separate transaction-specific approval is given.
