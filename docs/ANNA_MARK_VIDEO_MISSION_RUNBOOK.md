# Anna + Mark Live Video Mission Runbook

## Purpose

Demonstrate the Nexus Vector guarantee with one real Base Sepolia Mission containing two ordered KeeperHub economic effects:

```text
Anna: 1 USDC base unit
Mark: 2 USDC base units
```

The intended live story is:

```text
Mission READY
  -> Anna simulate
  -> separate Anna broadcast approval
  -> Anna broadcast once
  -> provider transaction binding
  -> independent Base Sepolia verification
  -> Anna CHAIN_CONFIRMED
  -> retry/continuation skips Anna forever
  -> Mark becomes the only next eligible effect
  -> Mark simulate
  -> separate Mark broadcast approval
  -> Mark broadcast once
  -> provider transaction binding
  -> independent Base Sepolia verification
  -> Mark CHAIN_CONFIRMED
  -> Mission COMPLETED
```

This is not two unrelated transfers. Anna and Mark are two durable Effects inside one immutable Mission.

## Safety boundary

The runner reuses the existing Nexus Vector domain and application layers:

- `MissionRequest` / deterministic Mission and Effect identity;
- `ExecutionDispatchService`;
- `SQLiteExecutionAttemptStore`;
- KeeperHub simulation and broadcast authorization ledger;
- `ProviderReferencePersistingPort`;
- immutable provider transaction bindings;
- `ExecutionReconciliationService`;
- independent Base Sepolia ERC-20 verification.

It does not add another execution state machine.

Rules:

- Base Sepolia only, chain ID `84532`;
- official Base Sepolia USDC only: `0x036CbD53842c5426634e7929541eC2318f3dCF7e`;
- Anna amount is fixed at `1` base unit (`0.000001 USDC`);
- Mark amount is fixed at `2` base units (`0.000002 USDC`);
- maximum one simulation POST per Effect;
- maximum one broadcast POST per Effect;
- no automatic retry after timeout, disconnect, malformed response, unknown outcome, or provider ambiguity;
- no new request key for the same ambiguous Effect;
- provider `completed` is not chain truth;
- a provider transaction hash must first be durably bound to the exact Attempt/provider reference;
- independent Base Sepolia verification requires exact token, sender, recipient, integer amount, successful receipt, bound transaction/log, and at least 2 confirmations;
- a `CHAIN_CONFIRMED` Effect is terminal and cannot be dispatched again;
- Mark is not dispatchable until Anna is `CHAIN_CONFIRMED`;
- mainnet is blocked.

## Local recipients

The existing private wallet registry supplies:

```text
KeeperHub organization sender
Anna = personal_recipient_wallet
```

Mark must be a second user-controlled Base Sepolia-compatible EVM address. It is stored once in:

```text
%LOCALAPPDATA%\NexusVector\Config\anna_mark_video_recipients.private-local.json
```

The config contains public wallet addresses only, never seed phrases or private keys.

Create/update it with:

```powershell
.\tools\setup_anna_mark_video_recipients.ps1
```

The setup rejects Mark if it equals either the KeeperHub sender or Anna recipient.

## Operator wrapper

The **supported live operator entrypoint** is the PowerShell wrapper:

```text
tools/invoke_anna_mark_video_mission.ps1
```

Do not use the lower-level Python runner directly during the live video Mission.

Before `simulate` or `broadcast`, the wrapper first runs `anna_mark_video_operator_preflight.py`. This preflight reads only local durable SQLite state and performs zero network calls. It runs **before the DPAPI credential is loaded**.

The preflight allows:

- a first simulation only when no simulation authorization has been consumed;
- a durable already-eligible simulation receipt to be read without another simulation POST;
- one broadcast attempt only when simulation is durably eligible, the Attempt is still `PREPARED`, no broadcast authorization has been consumed, and no provider reference exists.

The preflight blocks:

- simulation `REJECTED_FINAL`;
- simulation `CLAIMED` / `OUTCOME_UNKNOWN`;
- any consumed/ambiguous broadcast;
- any Attempt that is no longer fresh `PREPARED` for broadcast.

A blocked preflight does not load the KeeperHub API key and does not enter the execution runner.

For `simulate`, `broadcast`, and `provider-bind`, the wrapper loads the existing KeeperHub organization key from the Windows DPAPI CLIXML store and clears `KEEPERHUB_API_KEY` afterward.

`prepare`, `status`, and `verify` do not need the KeeperHub API key.

## 1. Prepare a fresh video Mission

Use a new run reference. Never reuse Rehearsal A or another completed/ambiguous Mission identity.

Example shape:

```powershell
$runRef = "anna-mark-video-20260809-v1"

.\tools\invoke_anna_mark_video_mission.ps1 `
  -Command prepare `
  -RunRef $runRef
```

Expected properties:

```text
status = PREPARED
mission_state = READY_FOR_EXECUTION
next_effect = anna
network_calls = 0
mainnet_allowed = false
Anna amount_base_units = 1
Mark amount_base_units = 2
```

The output reveals only the Anna simulation approval challenge because Mark is not yet eligible.

## 2. Anna simulation

Use exactly the challenge returned by `prepare` / local `status`:

```powershell
.\tools\invoke_anna_mark_video_mission.ps1 `
  -Command simulate `
  -RunRef $runRef `
  -Effect anna `
  -Approval "SIMULATE-ANNA-..."
```

Expected:

```text
status = PASS
simulation_posts = 1
broadcast_posts = 0
funds_movement = NONE_FROM_SIMULATION
decision = ELIGIBLE_FOR_SEPARATE_BROADCAST_APPROVAL
```

Simulation never authorizes broadcast by itself.

## 3. Anna broadcast — separate explicit approval

Use exactly the broadcast challenge returned by the Anna simulation:

```powershell
.\tools\invoke_anna_mark_video_mission.ps1 `
  -Command broadcast `
  -RunRef $runRef `
  -Effect anna `
  -Approval "BROADCAST-ANNA-..." `
  -ApproveTestnetWrite
```

This is the real economic action. Do not rerun this command regardless of timeout/error output.

Expected clean acknowledgement:

```text
status = PASS
broadcast_posts = 1
provider_reference_present = true
retry_same_effect = false
```

## 4. Bind Anna provider transaction

```powershell
.\tools\invoke_anna_mark_video_mission.ps1 `
  -Command provider-bind `
  -RunRef $runRef `
  -Effect anna
```

This performs at most one read-only KeeperHub status GET when no binding exists. A completed provider observation is immutably bound to Anna's exact Mission/Effect/Attempt/request fingerprint/provider reference.

## 5. Independently verify Anna

```powershell
.\tools\invoke_anna_mark_video_mission.ps1 `
  -Command verify `
  -RunRef $runRef `
  -Effect anna
```

Expected:

```text
status = PASS
outcome = VERIFIED
effect_state = CHAIN_CONFIRMED
mission_state = READY_FOR_EXECUTION
confirmed_effects = [anna]
next_effect = mark
decision = ANNA_CONFIRMED_SKIP_FOREVER_MARK_NOW_ELIGIBLE
broadcast_posts = 0
keeperhub_calls = 0
```

This is the key video moment: Anna is permanently confirmed, and only Mark remains eligible.

A repeat Anna `simulate` or `broadcast` must return a zero-network skip and must not move funds again.

## 6. Mark simulation and separate broadcast

Read local status first:

```powershell
.\tools\invoke_anna_mark_video_mission.ps1 `
  -Command status `
  -RunRef $runRef
```

The status should expose the Mark simulation challenge only after Anna is confirmed.

Then perform Mark simulation and, after reviewing it, the separately approved Mark broadcast exactly as for Anna, using `-Effect mark`.

## 7. Bind and independently verify Mark

After the one Mark broadcast:

```powershell
.\tools\invoke_anna_mark_video_mission.ps1 `
  -Command provider-bind `
  -RunRef $runRef `
  -Effect mark

.\tools\invoke_anna_mark_video_mission.ps1 `
  -Command verify `
  -RunRef $runRef `
  -Effect mark
```

Expected final state:

```text
status = PASS
outcome = VERIFIED
confirmed_effects = [anna, mark]
next_effect = null
mission_state = COMPLETED
decision = MISSION_COMPLETE_ALL_EFFECTS_CHAIN_CONFIRMED
```

## Restart / recovery behavior

At any point, use:

```powershell
.\tools\invoke_anna_mark_video_mission.ps1 `
  -Command status `
  -RunRef $runRef
```

This is local-only and performs zero network calls.

If simulation is `CLAIMED`, `OUTCOME_UNKNOWN`, or `REJECTED_FINAL`, do not retry it. The operator preflight will stop before credential loading or provider transport.

If a broadcast outcome is ambiguous:

- do not rerun broadcast;
- do not create a replacement request key for the same Effect;
- inspect local status and provider reference state;
- reconcile through provider status / independent chain evidence.

If an Effect is already `CHAIN_CONFIRMED`, retry/continuation must skip it without a provider call.

## Video capture recommendation

The strongest live sequence is short:

```text
Mission READY: Anna PLANNED, Mark PLANNED
Anna KeeperHub execution -> independent verification
Anna CHAIN_CONFIRMED / Mark PLANNED / Mission READY
repeat Anna -> SKIP_ALREADY_CHAIN_CONFIRMED (0 network)
Mark KeeperHub execution -> independent verification
Mission COMPLETED
```

Do not expose:

- API keys;
- DPAPI credential contents;
- raw provider execution IDs;
- seed/private keys;
- browser wallet private material;
- unrelated balances or notifications.

Transaction hashes and explorer links are public blockchain evidence. Wallet addresses should remain masked in the operator output and video unless separately reviewed for intentional disclosure.
