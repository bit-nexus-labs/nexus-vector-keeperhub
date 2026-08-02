from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nexus_vector.application.continuation_planner import (
    ContinuationAction,
    EffectContinuationDecision,
    MissionContinuationPlan,
)
from nexus_vector.application.execution_doctor import (
    ChainObservationState,
    DoctorAction,
    EffectObservation,
    ExecutionDoctor,
    ExecutionDoctorError,
    ProviderObservationState,
)
from nexus_vector.cli.execution_doctor import build_report_from_snapshot, run
from nexus_vector.domain.mission_models import MissionState

MISSION_KEY = "msn_" + "11" * 32


def decision(ref, byte, amount, action):
    return EffectContinuationDecision(
        effect_ref=ref,
        effect_id="eff_" + byte * 64,
        amount_base_units=amount,
        action=action,
        reason_code="TEST",
    )


def plan(*decisions):
    totals = {
        action: sum(
            item.amount_base_units
            for item in decisions
            if item.action is action
        )
        for action in ContinuationAction
    }
    return MissionContinuationPlan(
        mission_key=MISSION_KEY,
        mission_state=MissionState.RECONCILING,
        decisions=tuple(decisions),
        total_amount_base_units=sum(x.amount_base_units for x in decisions),
        skipped_amount_base_units=totals[ContinuationAction.SKIP_VERIFIED],
        executable_amount_base_units=totals[ContinuationAction.EXECUTE_MISSING],
        unresolved_amount_base_units=totals[ContinuationAction.RECONCILE_REQUIRED],
        manual_review_amount_base_units=totals[ContinuationAction.MANUAL_REVIEW],
    )


def observation(item, provider, chain, confirmations=0):
    return EffectObservation(
        effect_id=item.effect_id,
        provider_state=provider,
        chain_state=chain,
        confirmations=confirmations,
    )


class ExecutionDoctorTests(unittest.TestCase):
    def test_10_10_10_prioritizes_reconciliation(self):
        anna = decision("anna", "2", 10, ContinuationAction.SKIP_VERIFIED)
        mark = decision("mark", "3", 10, ContinuationAction.EXECUTE_MISSING)
        leo = decision("leo", "4", 10, ContinuationAction.RECONCILE_REQUIRED)
        report = ExecutionDoctor().diagnose(
            plan(anna, leo, mark),
            {
                anna.effect_id: observation(
                    anna,
                    ProviderObservationState.ACCEPTED,
                    ChainObservationState.CONFIRMED,
                    3,
                ),
                mark.effect_id: observation(
                    mark,
                    ProviderObservationState.NOT_QUERIED,
                    ChainObservationState.NOT_QUERIED,
                ),
                leo.effect_id: observation(
                    leo,
                    ProviderObservationState.ACCEPTED,
                    ChainObservationState.NOT_FOUND,
                ),
            },
            minimum_confirmations=2,
        )
        self.assertEqual(report.next_action, DoctorAction.RECONCILE)
        by_ref = {x.effect_ref: x.action for x in report.diagnoses}
        self.assertEqual(by_ref["anna"], DoctorAction.SKIP_VERIFIED)
        self.assertEqual(by_ref["mark"], DoctorAction.EXECUTE_MISSING)
        self.assertEqual(by_ref["leo"], DoctorAction.RECONCILE)

    def test_local_verified_chain_not_found_is_manual(self):
        anna = decision("anna", "2", 10, ContinuationAction.SKIP_VERIFIED)
        report = ExecutionDoctor().diagnose(
            plan(anna),
            {
                anna.effect_id: observation(
                    anna,
                    ProviderObservationState.ACCEPTED,
                    ChainObservationState.NOT_FOUND,
                )
            },
            minimum_confirmations=2,
        )
        self.assertEqual(report.next_action, DoctorAction.MANUAL_REVIEW)

    def test_missing_but_provider_accepted_reconciles(self):
        mark = decision("mark", "3", 10, ContinuationAction.EXECUTE_MISSING)
        report = ExecutionDoctor().diagnose(
            plan(mark),
            {
                mark.effect_id: observation(
                    mark,
                    ProviderObservationState.ACCEPTED,
                    ChainObservationState.NOT_FOUND,
                )
            },
            minimum_confirmations=2,
        )
        self.assertEqual(report.next_action, DoctorAction.RECONCILE)

    def test_insufficient_confirmations_waits(self):
        leo = decision("leo", "4", 10, ContinuationAction.RECONCILE_REQUIRED)
        report = ExecutionDoctor().diagnose(
            plan(leo),
            {
                leo.effect_id: observation(
                    leo,
                    ProviderObservationState.ACCEPTED,
                    ChainObservationState.CONFIRMED,
                    1,
                )
            },
            minimum_confirmations=2,
        )
        self.assertEqual(
            report.next_action,
            DoctorAction.WAIT_FOR_CONFIRMATIONS,
        )

    def test_all_verified_is_complete(self):
        anna = decision("anna", "2", 10, ContinuationAction.SKIP_VERIFIED)
        report = ExecutionDoctor().diagnose(
            plan(anna),
            {
                anna.effect_id: observation(
                    anna,
                    ProviderObservationState.NOT_QUERIED,
                    ChainObservationState.NOT_QUERIED,
                )
            },
            minimum_confirmations=2,
        )
        self.assertEqual(report.next_action, DoctorAction.COMPLETE)
        self.assertEqual(
            report.diagnoses[0].action,
            DoctorAction.SKIP_VERIFIED,
        )

    def test_observation_set_must_be_exact(self):
        anna = decision("anna", "2", 10, ContinuationAction.SKIP_VERIFIED)
        with self.assertRaises(ExecutionDoctorError) as caught:
            ExecutionDoctor().diagnose(
                plan(anna),
                {},
                minimum_confirmations=2,
            )
        self.assertEqual(caught.exception.code, "OBSERVATION_SET_MISMATCH")

    def test_non_confirmed_observation_cannot_have_confirmations(self):
        anna = decision("anna", "2", 10, ContinuationAction.SKIP_VERIFIED)
        with self.assertRaises(ExecutionDoctorError) as caught:
            EffectObservation(
                anna.effect_id,
                ProviderObservationState.NOT_FOUND,
                ChainObservationState.NOT_FOUND,
                1,
            )
        self.assertEqual(caught.exception.code, "UNEXPECTED_CONFIRMATIONS")

    def test_strict_cli_snapshot_and_stable_json(self):
        anna_id = "eff_" + "2" * 64
        snapshot = {
            "mission_key": MISSION_KEY,
            "mission_state": "RECONCILING",
            "minimum_confirmations": 2,
            "decisions": [
                {
                    "effect_ref": "anna",
                    "effect_id": anna_id,
                    "amount_base_units": 10,
                    "action": "SKIP_VERIFIED",
                    "reason_code": "EFFECT_ALREADY_VERIFIED",
                }
            ],
            "observations": [
                {
                    "effect_id": anna_id,
                    "provider_state": "NOT_QUERIED",
                    "chain_state": "NOT_QUERIED",
                    "confirmations": 0,
                }
            ],
        }
        report = build_report_from_snapshot(snapshot)
        self.assertEqual(report["next_action"], "COMPLETE")
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            import io
            stdout = io.StringIO()
            stderr = io.StringIO()
            self.assertEqual(
                run([str(path)], stdout=stdout, stderr=stderr),
                0,
            )
            self.assertEqual(stderr.getvalue(), "")
            parsed = json.loads(stdout.getvalue())
            self.assertEqual(parsed, report)

    def test_cli_rejects_unknown_fields(self):
        anna_id = "eff_" + "2" * 64
        snapshot = {
            "mission_key": MISSION_KEY,
            "mission_state": "RECONCILING",
            "minimum_confirmations": 2,
            "decisions": [],
            "observations": [],
            "unexpected": True,
        }
        with self.assertRaises(ExecutionDoctorError) as caught:
            build_report_from_snapshot(snapshot)
        self.assertEqual(caught.exception.code, "INVALID_SNAPSHOT_SHAPE")

    def test_cli_rejects_noncanonical_effect_id(self):
        snapshot = {
            "mission_key": MISSION_KEY,
            "mission_state": "RECONCILING",
            "minimum_confirmations": 2,
            "decisions": [
                {
                    "effect_ref": "anna",
                    "effect_id": "not-an-effect",
                    "amount_base_units": 10,
                    "action": "SKIP_VERIFIED",
                    "reason_code": "TEST",
                }
            ],
            "observations": [],
        }
        with self.assertRaises(ExecutionDoctorError) as caught:
            build_report_from_snapshot(snapshot)
        self.assertEqual(caught.exception.code, "INVALID_EFFECT_ID")

    def test_cli_rejects_duplicate_observations(self):
        anna_id = "eff_" + "2" * 64
        snapshot = {
            "mission_key": MISSION_KEY,
            "mission_state": "RECONCILING",
            "minimum_confirmations": 2,
            "decisions": [
                {
                    "effect_ref": "anna",
                    "effect_id": anna_id,
                    "amount_base_units": 10,
                    "action": "SKIP_VERIFIED",
                    "reason_code": "TEST",
                }
            ],
            "observations": [
                {
                    "effect_id": anna_id,
                    "provider_state": "NOT_QUERIED",
                    "chain_state": "NOT_QUERIED",
                    "confirmations": 0,
                },
                {
                    "effect_id": anna_id,
                    "provider_state": "NOT_FOUND",
                    "chain_state": "NOT_FOUND",
                    "confirmations": 0,
                },
            ],
        }
        with self.assertRaises(ExecutionDoctorError) as caught:
            build_report_from_snapshot(snapshot)
        self.assertEqual(caught.exception.code, "DUPLICATE_OBSERVATION")

    def test_modules_have_no_network_wallet_or_secret_capabilities(self):
        import ast
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
            Path("application/execution_doctor.py"),
            Path("cli/execution_doctor.py"),
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
