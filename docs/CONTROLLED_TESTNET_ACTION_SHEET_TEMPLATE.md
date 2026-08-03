# Controlled KeeperHub Testnet Action Sheet Template

**Status: TEMPLATE ONLY — NOT A TRANSACTION AUTHORIZATION**

This private worksheet must be completed immediately before a controlled testnet action. A completed sheet records reviewed intent and limits; it still does not authorize a call unless the matching action-specific approval is present.

Never place a completed copy in the public repository. Never include the organization API key, authorization header, private key, seed phrase, raw wallet session, or unredacted private provider payload.

## Authorization envelope

```text
action_sheet_id:
created_at_utc:
reviewed_at_utc:
expires_at_utc:
reviewed_by:
operator:
environment: TESTNET
mainnet_blocked: true

simulation_authorization:
  status: NOT_AUTHORIZED / AUTHORIZED_ONCE / COMPLETED
  approval_reference:

broadcast_authorization:
  status: NOT_AUTHORIZED / AUTHORIZED_ONCE / COMPLETED
  approval_reference:
  required_runtime_flag: --approve-testnet-write
```

Simulation approval does not imply broadcast approval. A broadcast-capable command must fail closed unless the exact `--approve-testnet-write` flag is present.

## KeeperHub and wallet readiness

```text
keeperhub_organization_id_private:
keeperhub_key_identity_reference_private:
keeperhub_wallet_address_private:
wallet_belongs_to_expected_organization:
wallet_is_active:
wallet_readiness_source:
wallet_readiness_observed_at_utc:

chain_catalog_source:
chain_catalog_observed_at_utc:
chain_id:
chain_name:
chain_enabled: true / false
chain_is_testnet: true / false

native_gas_balance_base_units:
native_gas_minimum_required_base_units:
token_balance_base_units:
token_balance_minimum_required_base_units:
balance_source:
balance_observed_at_utc:
```

Any unknown, stale, contradictory, or undocumented readiness value blocks simulation and broadcast.

## Immutable economic intent

```text
token_contract:
token_decimals:
sender_address:
recipient_address:
amount_base_units:
amount_decimal_string:
maximum_testnet_amount_base_units:
maximum_fee_native_base_units:
gas_multiplier_policy:

mission_key:
mission_content_fingerprint:
effect_id:
attempt_id:
request_key:
request_fingerprint:
provider_reference_store_location_private:
minimum_confirmations:
```

Required invariant:

```text
integer base units
→ exact decimal string without float
→ simulation body
→ broadcast body after removing only simulate
```

Changing chain, token, sender, recipient, amount, decimals, gas policy, Mission identity, effect identity, request key, or request fingerprint invalidates the sheet.

## Call budget

```text
maximum_simulation_posts: 1
maximum_broadcast_posts: 1
maximum_mutating_calls: 1
maximum_same_key_recovery_posts_after_ambiguity: 0
maximum_new_request_keys_after_ambiguity: 0
status_read_budget:
status_poll_interval_policy: HONOR_X_POLL_INTERVAL_HINT
```

The simulation POST and broadcast POST are distinct provider calls. `maximum_mutating_calls: 1` refers only to the single broadcast capable of moving testnet value.

A same-key recovery POST after an ambiguous broadcast is forbidden until KeeperHub confirms the exact supported procedure and that procedure is separately reviewed. A new key after ambiguity is always forbidden for the same economic effect.

## Durable state preconditions

```text
mission_state_before_action:
effect_state_before_action:
attempt_state_before_action:
attempt_revision_before_action:
provider_reference_preflight_passed:
existing_provider_reference_absent:
mission_and_effects_read_back_after_restart:
execution_attempt_read_back_after_restart:
```

Required pre-broadcast state:

```text
Mission persisted
→ canonical effect selected as EXECUTE_MISSING
→ canonical attempt PREPARED
→ durable IN_FLIGHT claim
→ provider-reference schema and existing-reference guard pass
```

No provider call is allowed when durable state cannot be read back exactly.

## Simulation receipt

```text
simulation_started_at_utc:
simulation_completed_at_utc:
simulation_http_classification:
simulation_success:
simulation_status:
simulation_would_revert:
simulation_body_fingerprint:
simulation_sanitized_evidence_location_private:
simulation_decision: STOP / ELIGIBLE_FOR_SEPARATE_BROADCAST_APPROVAL
```

Continue toward a separately approved broadcast only when:

```text
success = true
status = simulated
wouldRevert = false
body fingerprint = approved request fingerprint
```

Simulation is not transaction evidence and does not authorize broadcast.

## Broadcast receipt

Complete only after separate one-time broadcast authorization.

```text
broadcast_started_at_utc:
broadcast_completed_at_utc:
idempotency_key_fingerprint:
broadcast_body_fingerprint:
http_classification:
provider_response_classification:
execution_id_private:
execution_id_persisted_before_ack:
attempt_state_after_provider_response:
provider_reference_revision:
```

Exactly one broadcast is permitted. Timeout, disconnect, malformed response, persistence failure, or lost response produces `EXECUTION_UNKNOWN` and no second POST.

## Status and independent verification

```text
status_reads:
last_provider_status:
poll_hint_observed:
transaction_hash_private:
transaction_link_private:

chain_observation_source:
observed_chain_id:
observed_token_contract:
observed_sender:
observed_recipient:
observed_amount_base_units:
observed_event_index:
observed_block_number:
observed_confirmations:
independent_event_fingerprint:
verification_result: NOT_OBSERVED / MISMATCH / INSUFFICIENT_CONFIRMATIONS / VERIFIED
```

Provider `completed`, `executionId`, transaction hash, or explorer link alone is insufficient. Acceptance requires exact independently observed:

```text
chain + token + expected sender + recipient + integer base-unit amount
```

at or above the approved confirmation threshold.

## Recovery and stop record

```text
stop_condition_triggered:
stop_reason_code:
safe_state:
doctor_action:
reconciliation_reference_private:
new_request_authorized: false
second_broadcast_authorized: false
```

Safe terminal handling after ambiguity:

```text
EXECUTION_UNKNOWN
→ preserve exact request identity
→ read durable provider reference when available
→ read provider status according to poll hint
→ independently observe chain
→ reconcile or manual review
```

Never convert uncertainty into a new request key.

## Evidence destinations and publication review

```text
private_evidence_destination:
public_redaction_destination:
public_fields_approved:
public_fields_blocked:
privacy_reviewed_by:
claim_match_reviewed_by:
```

Before publication, remove credentials, authorization material, organization identifiers not approved for publication, private balances, private recipient information, raw provider payloads, internal storage paths, and unredacted provider identifiers.

A public claim may be made only after exact event verification and explicit redaction review. Until then:

```text
PENDING_RUNTIME
No real KeeperHub transaction is claimed.
```
