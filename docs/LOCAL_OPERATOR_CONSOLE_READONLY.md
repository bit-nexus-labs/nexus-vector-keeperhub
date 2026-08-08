# Local Operator Console — Read-Only Phase

**Status: local visualization only. No KeeperHub execution capability.**

The Local Operator Console is a visually distinct companion to the public Mission Resilience Lab. It is designed for live video capture and operator review without weakening the project’s execution boundaries.

## Surface split

| Surface | Purpose | Execution authority |
|---|---|---|
| Public Mission Resilience Lab | Product explanation, sandbox replay, sanitized public evidence | None |
| Local Operator Console | Local read-only visualization of sanitized runtime evidence | None in this phase |

The local console binds strictly to:

```text
127.0.0.1
```

It does not bind to `0.0.0.0`, does not expose CORS, and does not accept remote network traffic.

## Capability boundary

The read-only console has:

- no KeeperHub transport;
- no API-key loading;
- no wallet signing;
- no broadcast path;
- no POST, PUT, PATCH or DELETE endpoint;
- no browser filesystem picker;
- no browser-selected evidence path;
- no mainnet option;
- no automatic retry.

The process removes inherited KeeperHub and approval environment variables before serving the UI.

The browser receives only strictly validated sanitized JSON snapshots selected by the local operator when the server starts. The backend `mode` describes the console capability (`LOCAL_READ_ONLY_CONSOLE`); evidence state is represented separately and never inferred from the console being running.

## Visual language

The operator console intentionally differs from the public site:

- graphite runtime surface;
- restrained cyan neon for validated provider-canary evidence;
- blue for offline/read-only state;
- amber for future approval gates;
- purple for future provider acknowledgement;
- green only for independently chain-verified state;
- red for STOP, ambiguity or manual review.

Neon is used only for an active evidence transition or authority boundary. It is not decorative and never depicts funds movement during simulation.

## Launch

From the repository root:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python .\tools\local_operator_console.py `
  --canary-evidence "C:\path\to\sanitized-canary.json" `
  --mission-snapshot "C:\path\to\sanitized-mission-plan.json" `
  --open-browser
```

The server prints a sanitized startup record and opens:

```text
http://127.0.0.1:8765/
```

Both evidence arguments are optional. Missing files do not trigger network calls; the console displays a fail-closed `NOT LOADED` state.

## Accepted canary evidence

The canary input must be schema:

```text
nexus-vector.keeperhub-simulation-evidence.v1
```

It must prove exactly:

```text
status: PASS
provider_status: simulated
would_revert: false
simulation_posts: 1
broadcast_posts: 0
broadcast_authorized: false
funds_moved: false
claim_boundary: SIMULATION_ONLY_NOT_TRANSACTION_EVIDENCE
```

`gas_estimate` must be a positive decimal string of at most 12 digits. Unexpected fields, address-shaped values, unsafe field names, malformed or unbounded gas values, broadcast activity, funds movement, malformed provider status or weakened claim boundaries are rejected.

The provider canary is independent from the Anna + Mark Mission plan. A canary PASS proves only the bounded provider simulation evidence for the fixed `0.000001 USDC` canary effect. It does not prove that the Anna + Mark Mission was simulated, approved, broadcast or chain-confirmed.

## Accepted Mission snapshot

The Mission input must be the existing network-free snapshot:

```text
NEXUS_VECTOR_RUNTIME_EVIDENCE_PLAN_V1
```

It must contain only the fixed flagship Mission:

```text
runtime-evidence-001
Anna: 0.12 USDC
Mark: 0.07 USDC
Mission total: 0.19 USDC
effect state: PLANNED
continuation action: EXECUTE_MISSING
reason: PLANNED_EFFECT_NOT_DISPATCHED
provider calls: 0
funds moved: false
```

This phase deliberately accepts only the pre-execution `READY_FOR_EXECUTION` plan. Later runtime phases require separate schemas and separate review before they can be displayed.

## Stop policy

Stop and do not weaken validation when:

- an evidence file contains an address;
- any unexpected field is present;
- the canary contains malformed or unbounded gas-estimate text;
- the canary reports broadcast activity or funds movement;
- a Mission effect contains any reason other than the exact reviewed value;
- a Mission snapshot contains provider activity;
- the server cannot bind to localhost;
- the static asset path is unsafe;
- a browser attempts any write method.

## Future phases

Execution controls are not part of this change. They require separate PRs and reviews in this order:

1. read-only durable-store adapter;
2. exact-effect simulation capability;
3. separate broadcast authorization module;
4. independent chain-verification module;
5. restart/replay visualization.

Simulation and broadcast must remain separate capabilities, records, approval gates and UI states.
