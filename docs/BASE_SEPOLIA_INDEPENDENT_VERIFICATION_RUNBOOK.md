# Base Sepolia Independent Verification Runbook

## Purpose

Independently verify a KeeperHub-executed rehearsal transaction from public Base Sepolia chain state and project that verified economic fact into the existing durable Nexus Vector state machine.

KeeperHub `completed` is not treated as payment proof.

The evidence chain is:

```text
durable Mission / Attempt / provider reference
  -> read-only KeeperHub status GET
  -> immutable provider transaction binding
  -> independent Base Sepolia JSON-RPC
  -> successful transaction receipt
  -> exact ERC-20 Transfer evidence
  -> minimum confirmation threshold
  -> ExecutionReconciliationService
  -> Attempt VERIFIED
  -> Effect CHAIN_CONFIRMED
  -> Mission COMPLETED (single-effect rehearsal)
```

## Why the provider transaction binding exists

The independent verifier does **not** accept a transaction hash from an operator CLI argument.

Without an attempt-scoped binding, two separate Missions with the same sender, recipient, token and amount could accidentally be verified against the same old transaction hash. The immutable local binding prevents that attribution error by connecting:

```text
mission_key
effect_id
attempt_id
request_fingerprint
provider_namespace
provider_reference_fingerprint
provider_status = completed
transaction_hash
```

The raw KeeperHub provider reference is not written into the binding; only a one-way SHA-256 mask is stored.

## Phase 1 — capture the provider transaction binding

This phase performs **one read-only KeeperHub execution-status GET** when no binding exists. It does not sign, broadcast, resend, or mutate KeeperHub state.

The Windows wrapper loads the already existing KeeperHub organization API key from the local DPAPI CLIXML credential store, exposes it only to the child Python process, and clears the environment/BSTR afterwards.

From the repository root:

```powershell
$runRef = "rehearsal-a-20260808-01"

.\tools\invoke_keeperhub_provider_binding.ps1 -RunRef $runRef

$LASTEXITCODE
```

The wrapper expects the existing local credential file:

```text
%LOCALAPPDATA%\NexusVector\Secrets\keeperhub_organization_api_key.credential.xml
```

Expected PASS characteristics:

```text
status = PASS
provider_status = completed
status_gets = 1
keeperhub_mutating_calls = 0
broadcast_posts = 0
binding_persisted = true
retry_broadcast = false
```

The immutable private binding is stored at:

```text
%USERPROFILE%\.nexus-vector\keeperhub-rehearsal-execution-v1\<run-ref>\provider_transaction_binding.json
```

If the same valid binding already exists, the command returns `ALREADY_BOUND` with `status_gets = 0`.

## Phase 2 — independent Base Sepolia verification

The chain verifier:

- uses the official public Base Sepolia RPC endpoint `https://sepolia.base.org`;
- requires chain ID `84532`;
- never calls KeeperHub;
- never loads `KEEPERHUB_API_KEY`;
- never signs;
- never broadcasts;
- never sends an execution POST;
- does not accept a transaction hash from the operator;
- requires the immutable attempt-scoped provider transaction binding;
- reads the expected KeeperHub organization wallet and personal recipient wallet from the existing local private wallet registry;
- reads token/recipient/amount identity from the already persisted rehearsal Mission/effect;
- delegates durable state transitions to the existing `ExecutionReconciliationService`.

A repeat verification after `VERIFIED / CHAIN_CONFIRMED` performs zero Base RPC calls because the reconciliation service returns the already verified state without invoking the verifier.

## Exact economic and identity match

Before any Base RPC call, the runner requires the binding identity to match the durable:

```text
run_ref
mission_key
effect_id
attempt_id
request_fingerprint
provider_namespace
provider_reference fingerprint
```

Then chain evidence must match all of:

```text
chain_id
ERC-20 token contract
expected sender
expected recipient
amount in integer base units
bound transaction hash / receipt binding
block hash / log binding
minimum confirmations
```

The runner uses a minimum of **2 Base Sepolia confirmations**.

If one ERC-20 `Transfer` event is present, it is passed to reconciliation and any economic mismatch is classified as `BLOCKED` / manual review.

If multiple expected-token `Transfer` logs exist, exactly one must match the expected sender, recipient and amount. Zero or multiple exact matches are `AMBIGUOUS`; no broadcast is ever retried.

## Local prerequisites

The rehearsal must already have reached KeeperHub provider acknowledgement. The private local files must exist:

```text
%USERPROFILE%\.nexus-vector\keeperhub-rehearsal-execution-v1\<run-ref>\private_action_sheet.json
%USERPROFILE%\.nexus-vector\keeperhub-rehearsal-execution-v1\<run-ref>\missions.sqlite3
%USERPROFILE%\.nexus-vector\keeperhub-rehearsal-execution-v1\<run-ref>\execution_attempts.sqlite3
%USERPROFILE%\.nexus-vector\keeperhub-rehearsal-execution-v1\<run-ref>\provider_references.sqlite3
%USERPROFILE%\.nexus-vector\keeperhub-rehearsal-execution-v1\<run-ref>\provider_transaction_binding.json
%LOCALAPPDATA%\NexusVector\Config\wallets.private-local.json
```

The wallet registry must confirm:

```text
network.chain_id = 84532
network.environment = testnet
safety.mainnet_blocked = true
wallets.keeperhub_organization_wallet = valid EVM address
wallets.personal_recipient_wallet = valid EVM address
```

The registered personal recipient must exactly match the durable rehearsal effect recipient.

## Reconcile the bound transaction

From the repository root:

```powershell
$runRef = "rehearsal-a-20260808-01"

python .\tools\reconcile_keeperhub_rehearsal_chain.py `
  --run-ref $runRef

$LASTEXITCODE
```

No KeeperHub credential is requested or loaded during independent chain verification.

### PASS

Expected verified result:

```text
status = PASS
outcome = VERIFIED
provider_transaction_binding = true
keeperhub_calls = 0
broadcast_posts = 0
attempt_state = VERIFIED
effect_state = CHAIN_CONFIRMED
mission_state = COMPLETED
retry_broadcast = false
```

The output also includes:

- public transaction hash and independently constructed BaseScan link;
- Base Sepolia RPC endpoint;
- RPC call count;
- confirmation count;
- token contract;
- masked sender/recipient;
- amount base units;
- stable evidence fingerprint.

### WAIT

`WAIT / UNRESOLVED` means read-only verification may be repeated later, for example when the receipt is not yet visible, confirmations are below the threshold, or evidence is ambiguous.

It never authorizes another broadcast.

### STOP

`STOP` means fail closed. Examples include:

- missing or mismatched provider transaction binding;
- wrong RPC chain;
- reverted transaction;
- malformed receipt/log;
- transaction/log binding mismatch;
- wallet registry mismatch;
- exact economic mismatch (`BLOCKED` / manual review);
- corrupted local durable identity.

Do not rebroadcast the effect.

## Public evidence boundary

The transaction hash, independently constructed BaseScan link and token contract are public blockchain identifiers. Wallet addresses should remain masked in operator screenshots and public artifacts unless a separately reviewed public-evidence artifact intentionally discloses them.

The private provider transaction binding, wallet registry and DPAPI credential store must never be committed or copied into public evidence.
