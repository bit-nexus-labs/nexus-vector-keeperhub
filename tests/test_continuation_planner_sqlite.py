from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nexus_vector.application.continuation_planner import (
    ContinuationAction,
    ContinuationPlanner,
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
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_mission_store import SQLiteMissionStore

T0 = datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc)
TOKEN = "0x" + "11" * 20
RECIPIENTS = {
    "anna": "0x" + "22" * 20,
    "mark": "0x" + "33" * 20,
    "leo": "0x" + "44" * 20,
}


class SQLiteContinuationPlannerTests(unittest.TestCase):
    def test_real_12_7_11_state_partitions_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            mission_store = SQLiteMissionStore(root / "missions.sqlite3")
            attempt_store = SQLiteExecutionAttemptStore(
                root / "attempts.sqlite3"
            )
            mission_store.initialize()
            attempt_store.initialize()

            request = MissionRequest(
                schema_version=SCHEMA_VERSION,
                mission_namespace="keeperhub-hackathon",
                mission_ref="continuation-12-7-11",
                mission_type="MULTI_RECIPIENT_PAYMENT",
                chain_id=84532,
                asset=AssetSpec(token_address=TOKEN, decimals=6),
                effects=tuple(
                    EffectRequest(
                        effect_ref=effect_ref,
                        recipient=RECIPIENTS[effect_ref],
                        amount_base_units={"anna": 12, "mark": 7, "leo": 11}[effect_ref],
                    )
                    for effect_ref in ("anna", "mark", "leo")
                ),
            )
            mission = mission_store.create(
                create_initial_mission_record(request, T0)
            )
            for target in (
                MissionState.VALIDATED,
                MissionState.PERSISTED,
                MissionState.RECONCILING,
            ):
                mission = mission_store.transition_mission(
                    mission.record.mission_key,
                    mission.revision,
                    target,
                    T0,
                )

            for effect_ref, targets in {
                "anna": (
                    EffectState.RESERVED,
                    EffectState.SUBMITTED,
                    EffectState.CHAIN_CONFIRMED,
                ),
                "leo": (
                    EffectState.RESERVED,
                    EffectState.SUBMITTED,
                    EffectState.EXECUTION_UNKNOWN,
                ),
            }.items():
                for target in targets:
                    mission = mission_store.transition_effect(
                        mission.record.mission_key,
                        effect_ref,
                        mission.revision,
                        target,
                        T0,
                    )

            by_ref = {
                effect.effect_ref: effect
                for effect in mission.record.effects
            }
            for effect_ref, state in {
                "anna": ExecutionAttemptState.VERIFIED,
                "leo": ExecutionAttemptState.EXECUTION_UNKNOWN,
            }.items():
                effect = by_ref[effect_ref]
                plan = build_execution_attempt_plan(
                    mission_key=mission.record.mission_key,
                    effect_id=effect.effect_id,
                    provider_namespace="keeperhub.direct.v1",
                    request_key=f"{effect_ref}-request-1",
                    request_material={
                        "effect_ref": effect_ref,
                        "amount_base_units": effect.amount_base_units,
                    },
                )
                attempt = attempt_store.create(
                    create_initial_execution_attempt(plan, T0)
                )
                for target in (
                    ExecutionAttemptState.IN_FLIGHT,
                    state,
                ):
                    attempt = attempt_store.transition(
                        attempt.record.attempt_id,
                        attempt.revision,
                        target,
                        T0,
                    )

            continuation = ContinuationPlanner(attempt_store).plan(mission)
            actions = {
                decision.effect_ref: decision.action
                for decision in continuation.decisions
            }
            self.assertEqual(
                actions,
                {
                    "anna": ContinuationAction.SKIP_VERIFIED,
                    "leo": ContinuationAction.RECONCILE_REQUIRED,
                    "mark": ContinuationAction.EXECUTE_MISSING,
                },
            )
            self.assertEqual(continuation.total_amount_base_units, 30)
            self.assertEqual(continuation.skipped_amount_base_units, 12)
            self.assertEqual(continuation.executable_amount_base_units, 7)
            self.assertEqual(continuation.unresolved_amount_base_units, 11)
            self.assertEqual(
                continuation.manual_review_amount_base_units,
                0,
            )
            self.assertEqual(
                continuation.executable_effect_ids,
                (by_ref["mark"].effect_id,),
            )


if __name__ == "__main__":
    unittest.main()
