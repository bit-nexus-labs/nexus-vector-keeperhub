from __future__ import annotations

import ast
import tempfile
import unittest
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from nexus_vector.application.execution_reconciliation import (
    ExecutionReconciliationError,
    ExecutionReconciliationService,
    ReconciliationOutcome,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    build_execution_attempt_plan,
    create_initial_execution_attempt,
)
from nexus_vector.domain.mission_models import EffectState, MissionState
from nexus_vector.domain.verification_evidence import (
    ObservedTransfer,
    VerificationObservation,
    VerificationObservationStatus,
    derive_evidence_fingerprint,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
    SQLiteExecutionAttemptStoreError,
)

T0 = datetime(2026, 8, 2, 11, 0, tzinfo=timezone.utc)
MISSION_KEY = "msn_" + "11" * 32
EFFECT_ID = "eff_" + "22" * 32
EFFECT_REF = "anna"
TOKEN = "0x" + "33" * 20
SENDER = "0x" + "44" * 20
RECIPIENT = "0x" + "55" * 20
TX_HASH = "0x" + "66" * 32
BLOCK_HASH = "0x" + "77" * 32


class FakeStoreError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FakeEffect:
    effect_ref: str = EFFECT_REF
    effect_id: str = EFFECT_ID
    chain_id: int = 84532
    token_address: str = TOKEN
    recipient: str = RECIPIENT
    amount_base_units: int = 10
    state: EffectState = EffectState.PLANNED


@dataclass(frozen=True)
class FakeMissionRecord:
    mission_key: str = MISSION_KEY
    state: MissionState = MissionState.READY_FOR_EXECUTION
    effects: tuple[FakeEffect, ...] = (FakeEffect(),)
    updated_at_utc: datetime = T0


@dataclass(frozen=True)
class FakeStoredMission:
    record: FakeMissionRecord
    revision: int


class FakeMissionStore:
    def __init__(self, record: FakeMissionRecord | None = None):
        self.current = FakeStoredMission(record or FakeMissionRecord(), 1)

    def get(self, mission_key: str):
        return self.current if mission_key == self.current.record.mission_key else None

    def transition_mission(
        self,
        mission_key,
        expected_revision,
        target_state,
        updated_at_utc,
    ):
        if mission_key != self.current.record.mission_key:
            raise FakeStoreError("MISSION_NOT_FOUND")
        if expected_revision != self.current.revision:
            raise FakeStoreError("STALE_REVISION")
        allowed = {
            MissionState.PERSISTED: {MissionState.RECONCILING},
            MissionState.READY_FOR_EXECUTION: {MissionState.RECONCILING},
            MissionState.EXECUTING: {MissionState.RECONCILING},
            MissionState.VERIFYING: {MissionState.RECONCILING},
            MissionState.EXECUTION_UNKNOWN: {MissionState.RECONCILING},
            MissionState.VERIFICATION_FAILED: {MissionState.RECONCILING},
            MissionState.RECONCILING: {
                MissionState.READY_FOR_EXECUTION,
                MissionState.COMPLETED,
                MissionState.MANUAL_REVIEW_REQUIRED,
            },
        }
        if target_state not in allowed.get(self.current.record.state, set()):
            raise FakeStoreError("MISSION_TRANSITION_NOT_ALLOWED")
        self.current = FakeStoredMission(
            replace(
                self.current.record,
                state=target_state,
                updated_at_utc=max(
                    updated_at_utc,
                    self.current.record.updated_at_utc,
                ),
            ),
            self.current.revision + 1,
        )
        return self.current

    def transition_effect(
        self,
        mission_key,
        effect_ref,
        expected_revision,
        target_state,
        updated_at_utc,
    ):
        if mission_key != self.current.record.mission_key:
            raise FakeStoreError("MISSION_NOT_FOUND")
        if expected_revision != self.current.revision:
            raise FakeStoreError("STALE_REVISION")
        effect = next(
            item
            for item in self.current.record.effects
            if item.effect_ref == effect_ref
        )
        allowed = {
            EffectState.PLANNED: {EffectState.RESERVED, EffectState.BLOCKED},
            EffectState.RESERVED: {EffectState.SUBMITTED, EffectState.BLOCKED},
            EffectState.SUBMITTED: {
                EffectState.EXECUTION_UNKNOWN,
                EffectState.CHAIN_CONFIRMED,
                EffectState.FAILED_FINAL,
                EffectState.BLOCKED,
            },
            EffectState.EXECUTION_UNKNOWN: {
                EffectState.CHAIN_CONFIRMED,
                EffectState.FAILED_FINAL,
                EffectState.BLOCKED,
            },
        }
        if target_state not in allowed.get(effect.state, set()):
            raise FakeStoreError("EFFECT_TRANSITION_NOT_ALLOWED")
        effects = tuple(
            replace(item, state=target_state)
            if item.effect_ref == effect_ref
            else item
            for item in self.current.record.effects
        )
        self.current = FakeStoredMission(
            replace(
                self.current.record,
                effects=effects,
                updated_at_utc=max(
                    updated_at_utc,
                    self.current.record.updated_at_utc,
                ),
            ),
            self.current.revision + 1,
        )
        return self.current


class FixedVerifier:
    def __init__(self, observation=None, error=None):
        self.observation = observation
        self.error = error
        self.calls = 0

    def observe(self, attempt):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.observation


def make_plan():
    return build_execution_attempt_plan(
        mission_key=MISSION_KEY,
        effect_id=EFFECT_ID,
        provider_namespace="keeperhub.direct.v1",
        request_key="request-1",
        request_material={"chain_id": 84532, "amount_base_units": 10},
    )


def exact_transfer(
    *,
    confirmations: int = 3,
    sender: str = SENDER,
    amount: int = 10,
):
    return ObservedTransfer(
        chain_id=84532,
        token_address=TOKEN,
        sender=sender,
        recipient=RECIPIENT,
        amount_base_units=amount,
        transaction_hash=TX_HASH,
        block_hash=BLOCK_HASH,
        log_index=1,
        confirmations=confirmations,
    )


def verified_observation(**kwargs):
    return VerificationObservation(
        VerificationObservationStatus.VERIFIED_TRANSFER,
        exact_transfer(**kwargs),
    )


class FailVerifiedOnceStore(SQLiteExecutionAttemptStore):
    def __init__(self, path):
        super().__init__(path)
        self.failed = False

    def transition(
        self,
        attempt_id,
        expected_revision,
        target_state,
        updated_at_utc,
    ):
        if target_state is ExecutionAttemptState.VERIFIED and not self.failed:
            self.failed = True
            raise SQLiteExecutionAttemptStoreError("DATABASE_ERROR")
        return super().transition(
            attempt_id,
            expected_revision,
            target_state,
            updated_at_utc,
        )


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "attempts.sqlite3"
        self.attempt_store = SQLiteExecutionAttemptStore(self.db)
        self.attempt_store.initialize()
        prepared = self.attempt_store.create(
            create_initial_execution_attempt(make_plan(), T0)
        )
        in_flight = self.attempt_store.transition(
            prepared.record.attempt_id,
            prepared.revision,
            ExecutionAttemptState.IN_FLIGHT,
            T0,
        )
        self.unknown = self.attempt_store.transition(
            in_flight.record.attempt_id,
            in_flight.revision,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
            T0,
        )
        self.mission_store = FakeMissionStore()

    def tearDown(self):
        self.temp.cleanup()

    def service(self, attempt_store=None, mission_store=None):
        return ExecutionReconciliationService(
            mission_store or self.mission_store,
            attempt_store or self.attempt_store,
        )

    def test_exact_confirmed_transfer_recovers_unknown_without_resend(self):
        verifier = FixedVerifier(verified_observation())
        result = self.service().reconcile(
            attempt_id=self.unknown.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=2,
            verifier=verifier,
            observed_at_utc=T0,
        )
        self.assertEqual(result.outcome, ReconciliationOutcome.VERIFIED)
        self.assertEqual(
            result.attempt.record.state,
            ExecutionAttemptState.VERIFIED,
        )
        self.assertEqual(result.mission.record.state, MissionState.COMPLETED)
        self.assertEqual(
            result.mission.record.effects[0].state,
            EffectState.CHAIN_CONFIRMED,
        )
        self.assertTrue(result.evidence_fingerprint.startswith("evf_"))
        self.assertEqual(verifier.calls, 1)

    def test_not_found_remains_unknown_and_does_not_change_mission(self):
        verifier = FixedVerifier(
            VerificationObservation(VerificationObservationStatus.NOT_FOUND)
        )
        before = self.mission_store.current
        result = self.service().reconcile(
            attempt_id=self.unknown.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=2,
            verifier=verifier,
            observed_at_utc=T0,
        )
        self.assertEqual(result.outcome, ReconciliationOutcome.UNRESOLVED)
        self.assertEqual(
            result.attempt.record.state,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
        )
        self.assertEqual(self.mission_store.current, before)

    def test_low_confirmations_remains_unknown(self):
        verifier = FixedVerifier(verified_observation(confirmations=1))
        result = self.service().reconcile(
            attempt_id=self.unknown.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=2,
            verifier=verifier,
            observed_at_utc=T0,
        )
        self.assertEqual(result.outcome, ReconciliationOutcome.UNRESOLVED)
        self.assertEqual(
            result.attempt.record.state,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
        )
        self.assertEqual(
            result.mission.record.effects[0].state,
            EffectState.PLANNED,
        )

    def test_economic_mismatch_blocks_attempt_and_mission(self):
        verifier = FixedVerifier(verified_observation(amount=11))
        result = self.service().reconcile(
            attempt_id=self.unknown.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=2,
            verifier=verifier,
            observed_at_utc=T0,
        )
        self.assertEqual(result.outcome, ReconciliationOutcome.BLOCKED)
        self.assertEqual(
            result.attempt.record.state,
            ExecutionAttemptState.BLOCKED,
        )
        self.assertEqual(
            result.mission.record.state,
            MissionState.MANUAL_REVIEW_REQUIRED,
        )
        self.assertEqual(
            result.mission.record.effects[0].state,
            EffectState.PLANNED,
        )

    def test_verifier_exception_is_unknown_and_mission_unchanged(self):
        verifier = FixedVerifier(error=TimeoutError("read timeout"))
        before = self.mission_store.current
        with self.assertRaises(ExecutionReconciliationError) as caught:
            self.service().reconcile(
                attempt_id=self.unknown.record.attempt_id,
                expected_sender=SENDER,
                minimum_confirmations=2,
                verifier=verifier,
                observed_at_utc=T0,
            )
        self.assertEqual(
            caught.exception.code,
            "VERIFICATION_OUTCOME_UNKNOWN",
        )
        self.assertEqual(self.mission_store.current, before)
        durable = self.attempt_store.get(self.unknown.record.attempt_id)
        self.assertEqual(
            durable.record.state,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
        )

    def test_prepared_can_be_resolved_by_exact_external_evidence(self):
        other_db = Path(self.temp.name) / "prepared.sqlite3"
        store = SQLiteExecutionAttemptStore(other_db)
        store.initialize()
        prepared = store.create(
            create_initial_execution_attempt(make_plan(), T0)
        )
        mission = FakeMissionStore()
        result = self.service(store, mission).reconcile(
            attempt_id=prepared.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=1,
            verifier=FixedVerifier(verified_observation()),
            observed_at_utc=T0,
        )
        self.assertEqual(
            result.attempt.record.state,
            ExecutionAttemptState.VERIFIED,
        )
        self.assertEqual(result.mission.record.state, MissionState.COMPLETED)

    def test_crash_after_mission_projection_is_safe_and_restart_finishes_attempt(self):
        failing = FailVerifiedOnceStore(self.db)
        verifier = FixedVerifier(verified_observation())
        with self.assertRaises(SQLiteExecutionAttemptStoreError) as caught:
            self.service(failing).reconcile(
                attempt_id=self.unknown.record.attempt_id,
                expected_sender=SENDER,
                minimum_confirmations=1,
                verifier=verifier,
                observed_at_utc=T0,
            )
        self.assertEqual(caught.exception.code, "DATABASE_ERROR")
        self.assertEqual(
            self.mission_store.current.record.state,
            MissionState.COMPLETED,
        )
        self.assertEqual(
            self.mission_store.current.record.effects[0].state,
            EffectState.CHAIN_CONFIRMED,
        )
        still_unknown = SQLiteExecutionAttemptStore(self.db).get(
            self.unknown.record.attempt_id
        )
        self.assertEqual(
            still_unknown.record.state,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
        )

        restarted_store = SQLiteExecutionAttemptStore(self.db)
        restarted = self.service(restarted_store).reconcile(
            attempt_id=self.unknown.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=1,
            verifier=FixedVerifier(verified_observation()),
            observed_at_utc=T0,
        )
        self.assertEqual(
            restarted.attempt.record.state,
            ExecutionAttemptState.VERIFIED,
        )
        self.assertEqual(
            restarted.mission.record.state,
            MissionState.COMPLETED,
        )

    def test_repeat_after_verified_is_read_only_and_skips_verifier(self):
        first = self.service().reconcile(
            attempt_id=self.unknown.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=1,
            verifier=FixedVerifier(verified_observation()),
            observed_at_utc=T0,
        )
        verifier = FixedVerifier(
            error=AssertionError("must not be called")
        )
        second = self.service().reconcile(
            attempt_id=first.attempt.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=1,
            verifier=verifier,
            observed_at_utc=T0,
        )
        self.assertEqual(second.outcome, ReconciliationOutcome.VERIFIED)
        self.assertEqual(verifier.calls, 0)
        self.assertEqual(second.attempt, first.attempt)

    def test_sender_mismatch_blocks(self):
        wrong_sender = "0x" + "88" * 20
        result = self.service().reconcile(
            attempt_id=self.unknown.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=1,
            verifier=FixedVerifier(
                verified_observation(sender=wrong_sender)
            ),
            observed_at_utc=T0,
        )
        self.assertEqual(result.outcome, ReconciliationOutcome.BLOCKED)

    def test_evidence_fingerprint_is_stable_as_confirmations_grow(self):
        first = exact_transfer(confirmations=2)
        later = exact_transfer(confirmations=20)
        self.assertEqual(
            derive_evidence_fingerprint(first),
            derive_evidence_fingerprint(later),
        )

    def test_other_unknown_effect_keeps_mission_reconciling(self):
        other = FakeEffect(
            effect_ref="mark",
            effect_id="eff_" + "99" * 32,
            recipient="0x" + "aa" * 20,
            amount_base_units=20,
            state=EffectState.EXECUTION_UNKNOWN,
        )
        mission = FakeMissionStore(
            FakeMissionRecord(effects=(FakeEffect(), other))
        )
        result = self.service(mission_store=mission).reconcile(
            attempt_id=self.unknown.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=1,
            verifier=FixedVerifier(verified_observation()),
            observed_at_utc=T0,
        )
        self.assertEqual(result.outcome, ReconciliationOutcome.VERIFIED)
        self.assertEqual(
            result.mission.record.state,
            MissionState.RECONCILING,
        )
        by_ref = {
            item.effect_ref: item
            for item in result.mission.record.effects
        }
        self.assertEqual(
            by_ref[EFFECT_REF].state,
            EffectState.CHAIN_CONFIRMED,
        )
        self.assertEqual(
            by_ref["mark"].state,
            EffectState.EXECUTION_UNKNOWN,
        )

    def test_only_confirmed_and_planned_effects_return_mission_ready(self):
        other = FakeEffect(
            effect_ref="mark",
            effect_id="eff_" + "99" * 32,
            recipient="0x" + "aa" * 20,
            amount_base_units=20,
            state=EffectState.PLANNED,
        )
        mission = FakeMissionStore(
            FakeMissionRecord(effects=(FakeEffect(), other))
        )
        result = self.service(mission_store=mission).reconcile(
            attempt_id=self.unknown.record.attempt_id,
            expected_sender=SENDER,
            minimum_confirmations=1,
            verifier=FixedVerifier(verified_observation()),
            observed_at_utc=T0,
        )
        self.assertEqual(
            result.mission.record.state,
            MissionState.READY_FOR_EXECUTION,
        )
        by_ref = {
            item.effect_ref: item
            for item in result.mission.record.effects
        }
        self.assertEqual(
            by_ref[EFFECT_REF].state,
            EffectState.CHAIN_CONFIRMED,
        )
        self.assertEqual(
            by_ref["mark"].state,
            EffectState.PLANNED,
        )

    def test_invalid_timestamp_fails_before_verifier(self):
        verifier = FixedVerifier(
            error=AssertionError("must not be called")
        )
        with self.assertRaises(ExecutionReconciliationError) as caught:
            self.service().reconcile(
                attempt_id=self.unknown.record.attempt_id,
                expected_sender=SENDER,
                minimum_confirmations=1,
                verifier=verifier,
                observed_at_utc=datetime(2026, 8, 2, 11, 0),
            )
        self.assertEqual(caught.exception.code, "INVALID_TIMESTAMP")
        self.assertEqual(verifier.calls, 0)

    def test_modules_have_no_network_wallet_or_secret_imports(self):
        root = Path(__file__).parents[1] / "src" / "nexus_vector"
        forbidden = {
            "http",
            "urllib",
            "socket",
            "requests",
            "subprocess",
            "os",
            "secrets",
            "web3",
            "eth_account",
            "ccxt",
        }
        for relative in (
            Path("domain/verification_evidence.py"),
            Path("application/execution_reconciliation.py"),
        ):
            tree = ast.parse(
                (root / relative).read_text(encoding="utf-8")
            )
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(
                        alias.name.split(".", 1)[0]
                        for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".", 1)[0])
            self.assertTrue(
                imported.isdisjoint(forbidden),
                (relative, imported & forbidden),
            )


if __name__ == "__main__":
    unittest.main()
