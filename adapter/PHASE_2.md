# Phase 2

The hackathon UI is now wired to the same localhost adapter contract as Mission Control. The browser calls `NexusAdapter.getStatus/prepare/simulate/broadcast/bind/verify`; it does not implement state transitions itself.
