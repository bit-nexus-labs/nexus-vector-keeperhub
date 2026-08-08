# Base Sepolia Independent Verification Runbook

## Purpose

Independently verify a KeeperHub-executed rehearsal transaction from public Base Sepolia chain state and project that verified economic fact into the existing durable Nexus Vector state machine.

This path does **not** trust KeeperHub `completed` as payment proof.

The evidence chain is:

```text
KeeperHub provider status + transaction hash
  -> independent Base Sepolia JSON-RPC
  -> successful transaction receipt
  -> exact ERC-20 Transfer evidence
  -> minimum confirmation threshold
  -> ExecutionReconciliationService
  -> Attempt VERIFIED
  -> Effect CHAIN_CONFIRMED
  -> Mission COMPLETED (single-effect rehearsal)
```

## Independence boundary

The verifier:

- uses the official public Base Sepolia RPC endpoint `https://sepolia.base.org`;
- requires chain ID `84532`;
- never calls KeeperHub;
- never loads `KEEPERHUB_API_KEY`;
- never signs;
- never broadcasts;
- never sends an execution POST;
- reads the expected KeeperHub organization wallet and personal recipient wallet from the existing local private wallet registry;
- reads token/recipient/amount identity from the already persisted rehearsal Mission/effect;
- delegates durable state transitions to the existing `ExecutionReconciliationService`.

A repeat verification after `VERIFIED / CHAIN_CONFIRMED` performs zero RPC calls because the reconciliation service returns the already verified state without invoking the verifier.

## Exact economic match

For the rehearsal, chain evidence must match all of:

```text
chain_id
ERC-20 token contract
expected sender
expected recipient
amount in integer base units
transaction hash / receipt binding
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

## Reconcile one known transaction

From the repository root:

```powershell
$runRef = "rehearsal-a-20260808-01"
$txHash = "0x..."

python .\tools\reconcile_keeperhub_rehearsal_chain.py `
  --run-ref $runRef `
  --transaction-hash $txHash

$LASTEXITCODE
```

No KeeperHub credential is requested or loaded.

### PASS

Expected verified result:

```text
status = PASS
outcome = VERIFIED
keeperhub_calls = 0
broadcast_posts = 0
attempt_state = VERIFIED
effect_state = CHAIN_CONFIRMED
mission_state = COMPLETED
retry_broadcast = false
```

The output also includes:

- public transaction hash/link;
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

- wrong RPC chain;
- reverted transaction;
- malformed receipt/log;
- transaction/log binding mismatch;
- wallet registry mismatch;
- exact economic mismatch (`BLOCKED` / manual review);
- corrupted local durable identity.

Do not rebroadcast the effect.

## Public evidence boundary

The transaction hash/link and token contract are public blockchain identifiers. Wallet addresses should remain masked in operator screenshots and public artifacts unless a separately reviewed public-evidence artifact intentionally discloses them.

The private wallet registry and DPAPI credential store must never be committed or copied into public evidence.
