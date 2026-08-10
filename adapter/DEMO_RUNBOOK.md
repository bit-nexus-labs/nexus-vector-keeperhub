# Nexus Vector live demo runbook

## Start

From the repository root in PowerShell:

```powershell
$env:NEXUS_VECTOR_RUN_REF = "anna-mark-leo-video-demo-$(Get-Date -Format yyyyMMddHHmmss)"
$env:NEXUS_VECTOR_OPERATOR_LOG_ROOT = "$PWD\logs"
python .\adapter\server.py
```

Open `http://127.0.0.1:8765/`.

## Demo sequence

1. **Prepare mission** — show the UI moving from loading to `READY_FOR_EXECUTION` and show that `next_effect` comes from backend status.
2. **Simulate first effect** — approve the simulation challenge. Point out `broadcast_posts=0` and `funds_movement=NONE_FROM_SIMULATION`.
3. **Broadcast** — approve the separate broadcast challenge. Do not click twice. The adapter lock is the concurrency gate.
4. **Bind** — after provider acknowledgement, continue to `provider-bind`; provider ACK is not presented as chain success.
5. **Verify** — chain verification is the terminal gate. Show transaction hash / confirmations from the backend result.
6. Repeat the same state-machine flow for the remaining effects until `MISSION_COMPLETE_ALL_EFFECTS_CHAIN_CONFIRMED`.

## Failure-path shot

Trigger a second mutation while the same effect is still busy and show `409 effect_busy`. If a runner timeout occurs, show `504 UNKNOWN` and the UI message telling the operator to poll status rather than retry the mutation.

## Safety points to say on camera

- Browser is a control plane, not the execution authority.
- Wallet credentials remain in the existing local PowerShell execution path.
- `next_effect` is backend-owned.
- Provider acknowledgement is not success; binding and chain verification are required.
- Ambiguous timeout is never automatically retried.
