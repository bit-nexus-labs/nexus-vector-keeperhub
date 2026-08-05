from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.application.continuation_planner import ContinuationAction
from nexus_vector.application.runtime_evidence_plan import (
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_USDC_ADDRESS,
    FLAGSHIP_MISSION_REF,
    SIMULATION_CANARY_MISSION_REF,
    RuntimeEvidencePlanError,
    admit_and_prepare_mission,
    build_flagship_mission_request,
    build_simulation_canary_request,
    sanitized_mission_snapshot,
    sanitized_selection_snapshot,
    select_flagship_effect,
    select_simulation_canary,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    create_initial_execution_attempt,
)
from nexus_vector.domain.mission_models import EffectState, MissionState
from nexus_vector.integrations.keeperhub_request_key import (
    KeeperHubRequestKeyError,
    derive_keeperhub_request_key,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_mission_store import SQLiteMissionStore

T0 = datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc)
RECIPIENT = "0x" + "22" * 20


class KeeperHubRequestKeyTests(unittest.TestCase):
    def test_request_key_is_stable_effect_derived_and_bounded(self):
        first_effect = "eff_" + "11" * 32
        second_effect = "eff_" + "22" * 32

        first = derive_keeperhub_request_key(first_effect)
        repeated = derive_keeperhub_request_key(first_effect)
        second = derive_keeperhub_request_key(second_effect)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("khreq_v1_"))
        self.assertEqual(len(first), 73)
        self.assertNotIn(first_effect, first)

    def test_request_key_rejects_noncanonical_effect_identity(self):
        for value in (
            None,
            "",
            "eff_1234",
            "eff_" + "AA" * 32,
            "att_" + "11" * 32,
        ):
            with self.subTest(value=value):
                with self.assertRaises(KeeperHubRequestKeyError) as caught:
                    derive_keeperhub_request_key(value)
                self.assertEqual(caught.exception.code, "INVALID_EFFECT_ID")


class RuntimeEvidencePlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.mission_path = self.root / "missions.sqlite3"
        self.attempt_path = self.root / "attempts.sqlite3"
        self.missions = SQLiteMissionStore(self.mission_path)
        self.attempts = SQLiteExecutionAttemptStore(self.attempt_path)
        self.attempts.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def prepare_flagship(self):
        return admit_and_prepare_mission(
            build_flagship_mission_request(RECIPIENT),
            self.missions,
            T0,
        )

    def test_flagship_and_canary_are_separate_exact_missions(self):
        flagship = build_flagship_mission_request(RECIPIENT)
        canary = build_simulation_canary_request(RECIPIENT)

        self.assertEqual(flagship.mission_ref, FLAGSHIP_MISSION_REF)
        self.assertEqual(canary.mission_ref, SIMULATION_CANARY_MISSION_REF)
        self.assertNotEqual(
            flagship.build_identity().mission_key,
            canary.build_identity().mission_key,
        )
        self.assertEqual(flagship.chain_id, BASE_SEPOLIA_CHAIN_ID)
        self.assertEqual(flagship.asset.token_address, BASE_SEPOLIA_USDC_ADDRESS.casefold())
        self.assertEqual(flagship.asset.decimals, 6)
        self.assertEqual(
            tuple(
                (effect.effect_ref, effect.amount_base_units)
                for effect in flagship.effects
            ),
            (("anna", 120_000), ("mark", 70_000)),
        )
        self.assertEqual(len(canary.effects), 1)
        self.assertEqual(canary.effects[0].effect_ref, "provider-canary")
        self.assertEqual(canary.effects[0].amount_base_units, 1)

    def test_admission_is_durable_ready_and_idempotent(self):
        first = self.prepare_flagship()
        reopened = SQLiteMissionStore(self.mission_path)
        second = admit_and_prepare_mission(
            build_flagship_mission_request(RECIPIENT),
            reopened,
            T0 + timedelta(seconds=1),
        )

        self.assertEqual(first.record.mission_key, second.record.mission_key)
        self.assertEqual(second.record.state, MissionState.READY_FOR_EXECUTION)
        self.assertEqual(len(second.record.effects), 2)
        self.assertTrue(
            all(
                effect.state is EffectState.PLANNED
                for effect in second.record.effects
            )
        )

    def test_canary_has_zero_broadcast_budget_and_no_network_capability(self):
        mission = admit_and_prepare_mission(
            build_simulation_canary_request(RECIPIENT),
            self.missions,
            T0,
        )
        selection = select_simulation_canary(mission, self.attempts)
        snapshot = sanitized_selection_snapshot(selection)

        self.assertEqual(selection.maximum_simulation_posts, 1)
        self.assertEqual(selection.maximum_broadcast_posts, 0)
        self.assertEqual(selection.network_calls_performed, 0)
        self.assertEqual(snapshot["maximum_broadcast_posts"], 0)
        self.assertFalse(snapshot["broadcast_authorized"])
        self.assertEqual(snapshot["network_calls_performed"], 0)

    def test_flagship_selection_is_one_effect_and_stable_across_restart(self):
        mission = self.prepare_flagship()
        anna = select_flagship_effect(mission, self.attempts, "anna")

        reopened_mission = SQLiteMissionStore(self.mission_path).get(
            mission.record.mission_key
        )
        self.assertIsNotNone(reopened_mission)
        reopened_attempts = SQLiteExecutionAttemptStore(self.attempt_path)
        repeated = select_flagship_effect(
            reopened_mission,
            reopened_attempts,
            "anna",
        )

        self.assertEqual(anna.effect_id, repeated.effect_id)
        self.assertEqual(
            anna.attempt_plan.request_key,
            repeated.attempt_plan.request_key,
        )
        self.assertEqual(
            anna.attempt_plan.request_fingerprint,
            repeated.attempt_plan.request_fingerprint,
        )
        self.assertEqual(anna.maximum_simulation_posts, 1)
        self.assertEqual(anna.maximum_broadcast_posts, 1)
        self.assertEqual(anna.network_calls_performed, 0)

    def test_restart_plan_skips_verified_anna_and_executes_only_mark(self):
        mission = self.prepare_flagship()
        anna = select_flagship_effect(mission, self.attempts, "anna")

        initial_attempt = self.attempts.create(
            create_initial_execution_attempt(anna.attempt_plan, T0)
        )
        in_flight = self.attempts.transition(
            initial_attempt.record.attempt_id,
            initial_attempt.revision,
            ExecutionAttemptState.IN_FLIGHT,
            T0 + timedelta(seconds=1),
        )
        self.attempts.transition(
            in_flight.record.attempt_id,
            in_flight.revision,
            ExecutionAttemptState.VERIFIED,
            T0 + timedelta(seconds=2),
        )

        current = self.missions.get(mission.record.mission_key)
        current = self.missions.transition_mission(
            current.record.mission_key,
            current.revision,
            MissionState.EXECUTING,
            T0 + timedelta(seconds=3),
        )
        for state, seconds in (
            (EffectState.RESERVED, 4),
            (EffectState.SUBMITTED, 5),
            (EffectState.CHAIN_CONFIRMED, 6),
        ):
            current = self.missions.transition_effect(
                current.record.mission_key,
                "anna",
                current.revision,
                state,
                T0 + timedelta(seconds=seconds),
            )
        self.missions.transition_mission(
            current.record.mission_key,
            current.revision,
            MissionState.RECONCILING,
            T0 + timedelta(seconds=7),
        )

        restarted_missions = SQLiteMissionStore(self.mission_path)
        restarted_attempts = SQLiteExecutionAttemptStore(self.attempt_path)
        restarted = restarted_missions.get(mission.record.mission_key)
        snapshot = sanitized_mission_snapshot(restarted, restarted_attempts)
        decisions = {
            item["effect_ref"]: item["continuation_action"]
            for item in snapshot["effects"]
        }

        self.assertEqual(decisions["anna"], ContinuationAction.SKIP_VERIFIED.value)
        self.assertEqual(decisions["mark"], ContinuationAction.EXECUTE_MISSING.value)
        with self.assertRaises(RuntimeEvidencePlanError) as caught:
            select_flagship_effect(restarted, restarted_attempts, "anna")
        self.assertEqual(caught.exception.code, "EFFECT_NOT_EXECUTABLE")
        mark = select_flagship_effect(restarted, restarted_attempts, "mark")
        self.assertEqual(mark.effect_ref, "mark")
        self.assertEqual(mark.amount_base_units, 70_000)
        self.assertEqual(mark.network_calls_performed, 0)

    def test_sanitized_views_do_not_echo_private_or_durable_identifiers(self):
        mission = self.prepare_flagship()
        selection = select_flagship_effect(mission, self.attempts, "anna")
        output = json.dumps(
            {
                "mission": sanitized_mission_snapshot(
                    mission,
                    self.attempts,
                ),
                "selection": sanitized_selection_snapshot(selection),
            },
            sort_keys=True,
        ).casefold()

        self.assertNotIn(RECIPIENT.casefold(), output)
        self.assertNotIn(BASE_SEPOLIA_USDC_ADDRESS.casefold(), output)
        self.assertNotIn(mission.record.mission_key.casefold(), output)
        self.assertNotIn(selection.effect_id.casefold(), output)
        self.assertNotIn(selection.attempt_plan.request_key.casefold(), output)
        self.assertNotIn(
            selection.attempt_plan.request_fingerprint.casefold(),
            output,
        )
        self.assertIn("redacted_by_construction", output)


if __name__ == "__main__":
    unittest.main()
