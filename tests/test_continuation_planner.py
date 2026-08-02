from __future__ import annotations

import ast
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nexus_vector.application.continuation_planner import (
    ContinuationAction,
    ContinuationPlanner,
    ContinuationPlanningError,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    build_execution_attempt_plan,
    create_initial_execution_attempt,
)
from nexus_vector.domain.mission_models import EffectState, MissionState
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
)

T0 = datetime(2026, 8, 2, 13, 0, tzinfo=timezone.utc)
MISSION_KEY = "msn_" + "11" * 32


def effect_id(byte: str) -> str:
    return "eff_" + byte * 64


@dataclass(frozen=True)
class FakeEffect:
    effect_ref: str
    effect_id: str
    amount_base_units: int
    state: EffectState
    mission_key: str = MISSION_KEY


@dataclass(frozen=True)
class FakeMissionRecord:
    mission_key: str
    state: MissionState
    effects: tuple[FakeEffect, ...]


@dataclass(frozen=True)
class FakeStoredMission:
    record: FakeMissionRecord
    revision: int = 1


def make_plan(effect: FakeEffect, request_key: str):
    return build_execution_attempt_plan(
        mission_key=MISSION_KEY,
        effect_id=effect.effect_id,
        provider_namespace="keeperhub.direct.v1",
        request_key=request_key,
        request_material={
            "effect_ref": effect.effect_ref,
            "amount_base_units": effect.amount_base_units,
        },
    )


class ContinuationPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteExecutionAttemptStore(
            Path(self.temp.name) / "attempts.sqlite3"
        )
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def store_attempt(
        self,
        effect: FakeEffect,
        state: ExecutionAttemptState,
        request_key: str,
    ) -> None:
        stored = self.store.create(
            create_initial_execution_attempt(
                make_plan(effect, request_key),
                T0,
            )
        )
        if state is ExecutionAttemptState.PREPARED:
            return
        path = {
            ExecutionAttemptState.IN_FLIGHT: (
                ExecutionAttemptState.IN_FLIGHT,
            ),
            ExecutionAttemptState.PROVIDER_ACKNOWLEDGED: (
                ExecutionAttemptState.IN_FLIGHT,
                ExecutionAttemptState.PROVIDER_ACKNOWLEDGED,
            ),
            ExecutionAttemptState.EXECUTION_UNKNOWN: (
                ExecutionAttemptState.IN_FLIGHT,
                ExecutionAttemptState.EXECUTION_UNKNOWN,
            ),
            ExecutionAttemptState.VERIFIED: (
                ExecutionAttemptState.IN_FLIGHT,
                ExecutionAttemptState.VERIFIED,
            ),
            ExecutionAttemptState.FAILED_FINAL: (
                ExecutionAttemptState.IN_FLIGHT,
                ExecutionAttemptState.FAILED_FINAL,
            ),
            ExecutionAttemptState.BLOCKED: (
                ExecutionAttemptState.BLOCKED,
            ),
        }[state]
        for target in path:
            stored = self.store.transition(
                stored.record.attempt_id,
                stored.revision,
                target,
                T0,
            )

    def test_12_7_11_plan_skips_paid_executes_missing_and_reconciles_unknown(
        self,
    ):
        anna = FakeEffect(
            "anna",
            effect_id("2"),
            12,
            EffectState.CHAIN_CONFIRMED,
        )
        mark = FakeEffect(
            "mark",
            effect_id("3"),
            7,
            EffectState.PLANNED,
        )
        leo = FakeEffect(
            "leo",
            effect_id("4"),
            11,
            EffectState.EXECUTION_UNKNOWN,
        )
        self.store_attempt(
            anna,
            ExecutionAttemptState.VERIFIED,
            "anna-1",
        )
        self.store_attempt(
            leo,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
            "leo-1",
        )

        plan = ContinuationPlanner(self.store).plan(
            FakeStoredMission(
                FakeMissionRecord(
                    MISSION_KEY,
                    MissionState.RECONCILING,
                    (leo, anna, mark),
                )
            )
        )

        by_ref = {
            decision.effect_ref: decision
            for decision in plan.decisions
        }
        self.assertEqual(
            by_ref["anna"].action,
            ContinuationAction.SKIP_VERIFIED,
        )
        self.assertEqual(
            by_ref["mark"].action,
            ContinuationAction.EXECUTE_MISSING,
        )
        self.assertEqual(
            by_ref["leo"].action,
            ContinuationAction.RECONCILE_REQUIRED,
        )
        self.assertEqual(plan.total_amount_base_units, 30)
        self.assertEqual(plan.skipped_amount_base_units, 12)
        self.assertEqual(plan.executable_amount_base_units, 7)
        self.assertEqual(plan.unresolved_amount_base_units, 11)
        self.assertEqual(plan.manual_review_amount_base_units, 0)
        self.assertEqual(plan.executable_effect_ids, (mark.effect_id,))
        self.assertTrue(plan.requires_reconciliation)
        self.assertFalse(plan.requires_manual_review)

    def test_new_request_key_for_same_effect_never_creates_second_attempt(
        self,
    ):
        effect = FakeEffect(
            "anna",
            effect_id("5"),
            10,
            EffectState.EXECUTION_UNKNOWN,
        )
        self.store_attempt(
            effect,
            ExecutionAttemptState.EXECUTION_UNKNOWN,
            "original-key",
        )
        plan = ContinuationPlanner(self.store).plan(
            FakeStoredMission(
                FakeMissionRecord(
                    MISSION_KEY,
                    MissionState.RECONCILING,
                    (effect,),
                )
            )
        )
        self.assertEqual(
            plan.decisions[0].action,
            ContinuationAction.RECONCILE_REQUIRED,
        )
        self.assertEqual(plan.executable_effect_ids, ())

    def test_prepared_attempt_is_safe_to_execute_but_in_flight_is_not(
        self,
    ):
        prepared = FakeEffect(
            "prepared",
            effect_id("6"),
            7,
            EffectState.PLANNED,
        )
        active = FakeEffect(
            "active",
            effect_id("7"),
            8,
            EffectState.PLANNED,
        )
        self.store_attempt(
            prepared,
            ExecutionAttemptState.PREPARED,
            "prepared-key",
        )
        self.store_attempt(
            active,
            ExecutionAttemptState.IN_FLIGHT,
            "active-key",
        )
        plan = ContinuationPlanner(self.store).plan(
            FakeStoredMission(
                FakeMissionRecord(
                    MISSION_KEY,
                    MissionState.READY_FOR_EXECUTION,
                    (active, prepared),
                )
            )
        )
        by_ref = {
            decision.effect_ref: decision
            for decision in plan.decisions
        }
        self.assertEqual(
            by_ref["prepared"].action,
            ContinuationAction.EXECUTE_MISSING,
        )
        self.assertEqual(
            by_ref["active"].action,
            ContinuationAction.RECONCILE_REQUIRED,
        )

    def test_confirmed_effect_without_verified_attempt_is_never_executable(
        self,
    ):
        confirmed = FakeEffect(
            "anna",
            effect_id("8"),
            10,
            EffectState.CHAIN_CONFIRMED,
        )
        plan = ContinuationPlanner(self.store).plan(
            FakeStoredMission(
                FakeMissionRecord(
                    MISSION_KEY,
                    MissionState.RECONCILING,
                    (confirmed,),
                )
            )
        )
        self.assertEqual(
            plan.decisions[0].action,
            ContinuationAction.RECONCILE_REQUIRED,
        )
        self.assertEqual(plan.executable_amount_base_units, 0)

    def test_planned_effect_with_verified_attempt_is_manual_contradiction(
        self,
    ):
        planned = FakeEffect(
            "anna",
            effect_id("9"),
            10,
            EffectState.PLANNED,
        )
        self.store_attempt(
            planned,
            ExecutionAttemptState.VERIFIED,
            "verified-key",
        )
        plan = ContinuationPlanner(self.store).plan(
            FakeStoredMission(
                FakeMissionRecord(
                    MISSION_KEY,
                    MissionState.RECONCILING,
                    (planned,),
                )
            )
        )
        self.assertEqual(
            plan.decisions[0].action,
            ContinuationAction.MANUAL_REVIEW,
        )
        self.assertTrue(plan.requires_manual_review)

    def test_persisted_mission_never_authorizes_missing_effect_execution(
        self,
    ):
        missing = FakeEffect(
            "anna",
            effect_id("c"),
            10,
            EffectState.PLANNED,
        )
        plan = ContinuationPlanner(self.store).plan(
            FakeStoredMission(
                FakeMissionRecord(
                    MISSION_KEY,
                    MissionState.PERSISTED,
                    (missing,),
                )
            )
        )
        self.assertEqual(
            plan.decisions[0].action,
            ContinuationAction.RECONCILE_REQUIRED,
        )
        self.assertEqual(plan.executable_amount_base_units, 0)

    def test_completed_mission_with_unconfirmed_effect_is_manual_contradiction(
        self,
    ):
        missing = FakeEffect(
            "anna",
            effect_id("d"),
            10,
            EffectState.PLANNED,
        )
        plan = ContinuationPlanner(self.store).plan(
            FakeStoredMission(
                FakeMissionRecord(
                    MISSION_KEY,
                    MissionState.COMPLETED,
                    (missing,),
                )
            )
        )
        self.assertEqual(
            plan.decisions[0].action,
            ContinuationAction.MANUAL_REVIEW,
        )

    def test_manual_mission_state_overrides_all_effect_actions(self):
        missing = FakeEffect(
            "anna",
            effect_id("a"),
            10,
            EffectState.PLANNED,
        )
        plan = ContinuationPlanner(self.store).plan(
            FakeStoredMission(
                FakeMissionRecord(
                    MISSION_KEY,
                    MissionState.MANUAL_REVIEW_REQUIRED,
                    (missing,),
                )
            )
        )
        self.assertEqual(
            plan.decisions[0].action,
            ContinuationAction.MANUAL_REVIEW,
        )
        self.assertEqual(plan.executable_amount_base_units, 0)

    def test_duplicate_effect_identity_fails_closed(self):
        first = FakeEffect(
            "anna",
            effect_id("b"),
            10,
            EffectState.PLANNED,
        )
        duplicate = FakeEffect(
            "mark",
            first.effect_id,
            20,
            EffectState.PLANNED,
        )
        with self.assertRaises(ContinuationPlanningError) as caught:
            ContinuationPlanner(self.store).plan(
                FakeStoredMission(
                    FakeMissionRecord(
                        MISSION_KEY,
                        MissionState.RECONCILING,
                        (first, duplicate),
                    )
                )
            )
        self.assertEqual(caught.exception.code, "DUPLICATE_EFFECT_ID")

    def test_module_has_no_external_action_capabilities(self):
        source = (
            Path(__file__).parents[1]
            / "src"
            / "nexus_vector"
            / "application"
            / "continuation_planner.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
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
        self.assertTrue(imported.isdisjoint(forbidden))


if __name__ == "__main__":
    unittest.main()
