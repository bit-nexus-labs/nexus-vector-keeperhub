# Provider Reference Preflight Correction

## Status

P0 corrective change after provider-reference persistence was merged before final safety review completed.

## Problem

The merged wrapper initialized and validated the local provider-reference journal only after the provider call. A known local schema or database failure could therefore be discovered too late, after an external execution might already have been accepted.

## Correction

- validate canonical provider namespace and lookup identities without raw-value echo;
- initialize and validate the provider-reference journal before the provider call;
- prove that preflight failure results in zero provider calls;
- preserve `EXECUTION_UNKNOWN` when the provider call occurred but durable reference persistence failed;
- keep restart recovery fail-closed and prevent any blind retry authorization.

## Safety boundary

This correction is offline and provider-neutral. It performs no HTTP request, KeeperHub authentication, wallet access, simulation, signing, broadcast, deployment, RPC call, secret access, or funds action.

No live execution is permitted until this correction is merged, CI is green, exact diff review passes, wallet readiness is independently confirmed, and a separate transaction-specific approval is given.
