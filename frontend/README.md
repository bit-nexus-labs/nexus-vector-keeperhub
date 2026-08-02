# Static Mission-Safe Replay UI

This directory is a dependency-free, public-safe replay artifact for the Nexus Vector hackathon demonstration.

## Run locally

Open `index.html` in a browser or serve this directory with any static file server.

## Safety boundary

- curated and sanitized replay data only;
- no live KeeperHub request;
- no wallet, RPC, signer, transaction broadcast, environment secret, analytics or external dependency;
- the displayed manifest digest is not a transaction hash and does not prove funds moved;
- `EXECUTE_MISSING` is a replayed deterministic classification, not a live execution authorization.

The authoritative product state machines and tests live under `src/nexus_vector` and `tests`.
