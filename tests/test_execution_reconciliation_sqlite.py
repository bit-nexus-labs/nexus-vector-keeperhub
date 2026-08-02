from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nexus_vector.application.execution_reconciliation import (
    ExecutionReconciliationService,
    ReconciliationOutcome,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    build_execution_attempt_plan,
    create_initial_execution_attempt,
)
from nexus_vector.domain.mission_identity import SCHEMA_VERSION
from nexus_vector.domain.mission_models import (
    AssetSpec,
    EffectRequest,
    EffectState,
    MissionRequest,
    MissionState,
    create_initial_mission_record,
)
from nexus_vector.domain.verification_evidence import (
    ObservedTransfer,
    VerificationObservation,
    VerificationObservationStatus,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_mission_store import SQLiteMissionStore

T0 = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
TOKEN = "0x" + "11" * 20
SENDER = "0x" + "22" * 20
RECIPIENT = "0x" + "33" * 20
TX_HASH = "0x" + "44" * 32
BLOCK_HASH = "0x" + "55" * 32


class ExactVerifier:
    def __init__(self, transfer: ObservedTransfer) -> None:
        self.transfer = transfer
        self.calls = 0

    def observe(self, attempt):
        self.calls += 1
        return VerificationObservation(
            VerificationObservationStatus.VERIFIED_TRANSFER,
            self.transfer,
        )


class SQLiteReconciliationIntegrationTests(unittest.TestCase):
    def test_unknown_attempt_recovers_across_real_stores_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            mission_path = root / "missions.sqlite3"
            attempt_path = root / "attempts.sqlite3"

            request = MissionRequest(
                schema_version=SCHEMA_VERSION,
                mission_namespace="keeperhub-hackathon",
                mission_ref="integration-001",
                mission_type="TRANSFER",
                chain_id=84532,
                asset=AssetSpec(token_address=TOKEN, decimals=6),
                effects=(
                    EffectRequest(
                        effect_ref="anna",
                        recipient=RECIPIENT,
                        amount_base_units=10,
                    ),
                ),
            )
            mission_store = SQLiteMissionStore(mission_path)
            mission_store.initialize()
            mission = mission_store.create(
                create_initial_mission_record(request, T0)
            )
            mission = mission_store.transition_mission(
                mission.record.mission_key,
                mission.revision,
                MissionState.VALIDATED,
                T0,
            )
            mission = mission_store.transition_mission(
                mission.record.mission_key,
                mission.revision,
                MissionState.PERSISTED,
                T0,
            )
            mission = mission_store.transition_mission(
                mission.record.mission_key,
                mission.revision,
                MissionState.RECONCILING,
                T0,
            )
            mission = mission_store.transition_mission(
                mission.record.mission_key,
                mission.revision,
                MissionState.READY_FOR_EXECUTION,
                T0,
            )
            effect = mission.record.effects[0]

            attempt_store = SQLiteExecutionAttemptStore(attempt_path)
            attempt_store.initialize()
            plan = build_execution_attempt_plan(
                mission_key=mission.record.mission_key,
                effect_id=effect.effect_id,
                provider_namespace="keeperhub.direct.v1",
                request_key="integration-request-1",
                request_material={
                    "chain_id": request.chain_id,
                    "token_address": request.asset.token_address,
                    "sender": SENDER,
                    "recipient": effect.recipient,
                    "amount_base_units": effect.amount_base_units,
                },
            )
            attempt = attempt_store.create(
                create_initial_execution_attempt(plan, T0)
            )
            attempt = attempt_store.transition(
                attempt.record.attempt_id,
                attempt.revision,
                ExecutionAttemptState.IN_FLIGHT,
                T0,
            )
            attempt = attempt_store.transition(
                attempt.record.attempt_id,
                attempt.revision,
                ExecutionAttemptState.EXECUTION_UNKNOWN,
                T0,
            )

            verifier = ExactVerifier(
                ObservedTransfer(
                    chain_id=request.chain_id,
                    token_address=request.asset.token_address,
                    sender=SENDER,
                    recipient=effect.recipient,
                    amount_base_units=effect.amount_base_units,
                    transaction_hash=TX_HASH,
                    block_hash=BLOCK_HASH,
                    log_index=0,
                    confirmations=3,
                )
            )
            result = ExecutionReconciliationService(
                mission_store,
                attempt_store,
            ).reconcile(
                attempt_id=attempt.record.attempt_id,
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
            self.assertEqual(verifier.calls, 1)

            reopened_mission = SQLiteMissionStore(mission_path).get(
                mission.record.mission_key
            )
            reopened_attempt = SQLiteExecutionAttemptStore(attempt_path).get(
                attempt.record.attempt_id
            )
            self.assertIsNotNone(reopened_mission)
            self.assertIsNotNone(reopened_attempt)
            self.assertEqual(
                reopened_mission.record.state,
                MissionState.COMPLETED,
            )
            self.assertEqual(
                reopened_mission.record.effects[0].state,
                EffectState.CHAIN_CONFIRMED,
            )
            self.assertEqual(
                reopened_attempt.record.state,
                ExecutionAttemptState.VERIFIED,
            )


if __name__ == "__main__":
    unittest.main()
