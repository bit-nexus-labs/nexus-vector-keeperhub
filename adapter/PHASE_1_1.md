# Phase 1.1 hardening

- Loopback-only listener remains enforced at `127.0.0.1`.
- Mutating requests require both the per-process adapter token and exact same-origin `Origin`.
- Read-only API requests use the same authorization boundary.
- Static browser assets are served only from an explicit allowlist.
- Browser UI remains a control plane; backend `next_effect` remains authoritative.
- Per-effect concurrency locks prevent simultaneous mutating calls for the same effect.
- Runner timeout is surfaced as `504 UNKNOWN`; callers must poll status and must not retry the mutation automatically.
- Broadcast always carries the existing `-ApproveTestnetWrite` execution gate.
- Smoke test covers prepare/simulate and confirms no broadcast is performed.
