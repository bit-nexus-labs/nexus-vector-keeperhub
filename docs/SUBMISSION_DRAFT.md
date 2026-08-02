# DoraHacks Submission Draft

## Project name

**Nexus Vector — Mission-Safe Agent Payments**

## Tagline

**The agent can retry. The money cannot duplicate.**

## One-sentence description

Nexus Vector is a durable business-Mission, reconciliation, and continuation layer that prevents an autonomous payment agent from duplicating economic effects after timeouts, restarts, changed request keys, or partial execution.

## Problem

Request-level idempotency is not the same as business-level safety. A multi-recipient Mission may span several requests, survive a process restart, lose a provider response, or be retried with changed JSON or a new request key. An agent that treats timeout as failure can pay the same effect twice.

## Solution

Nexus Vector places a provider-neutral Mission Core above execution:

1. derive deterministic Mission and effect identity;
2. persist the Mission and all effects atomically;
3. persist one canonical execution attempt before a provider call;
4. classify ambiguous outcomes as `EXECUTION_UNKNOWN`;
5. reconcile from exact independent evidence;
6. permanently skip verified effects;
7. execute only effects that are provably missing;
8. surface one deterministic safe next action through Execution Doctor.

## Demonstrated scenario

In the curated 10 + 10 + 10 replay:

- Anna: independently verified and skipped — 10;
- Mark: missing and a future execution candidate — 10;
- Leo: execution outcome unknown and reconciliation-required — 10;
- total: exactly 30, classified once with no overlap.

## Architecture

- Python standard library;
- deterministic Mission/effect/attempt state models;
- SQLite Mission store and execution-attempt journal;
- revision-CAS single-writer execution claim;
- provider-neutral execution and observation contracts;
- exact ERC-20 evidence matching contracts;
- deterministic continuation planner and read-only Doctor;
- dependency-free static replay UI;
- fail-closed sanitized evidence verifier.

## What is complete

- durable offline product core;
- concurrency, restart, lost-response, cross-store crash, duplicate suppression, and 10 + 10 + 10 SQLite integration tests;
- Simple, Technical, and Evidence replay views;
- public evidence manifest with reviewed commit references and artifact hashes;
- current README, architecture, state-machine, and starter documentation;
- GitHub CI on Python 3.12 and 3.14.

## KeeperHub and runtime status

The product is designed for a KeeperHub execution adapter, with Base Sepolia as the primary testnet and Ethereum Sepolia as fallback. The public repository currently **does not claim a completed KeeperHub transaction**. Authenticated wallet readiness, the exact reviewed adapter, one controlled testnet execution, and independently matched public explorer evidence remain runtime gates.

## Safety

- testnet first;
- mainnet blocked;
- no blind retry after an unknown outcome;
- no provider success equals payment-proof claim;
- no secrets, wallet material, raw private receipts, real recipients, or unredacted provider identifiers in the public repository;
- live, simulation, and replay evidence must be explicitly labeled.

## Links — fill only after verification

- Repository: `https://github.com/bit-nexus-labs/nexus-vector-keeperhub`
- Demo video: `PENDING_VERIFIED_VIDEO_URL`
- Frontend: `PENDING_DEPLOYED_FRONTEND_URL`
- Public evidence manifest: `https://github.com/bit-nexus-labs/nexus-vector-keeperhub/blob/main/evidence/public_manifest.json`
- KeeperHub testnet transaction: `PENDING_EXACT_PUBLIC_EXPLORER_URL`

## Submission gate

Do not replace a `PENDING_*` value until the exact URL opens in a clean browser session, contains no secret/private material, and matches the claim made beside it.
