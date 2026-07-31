from __future__ import annotations

import ast
import contextlib
import io
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.domain.mission_identity import SCHEMA_VERSION
from nexus_vector.domain.mission_models import (
    AssetSpec,
    EffectRequest,
    EffectState,
    MissionRecord,
    MissionRequest,
    MissionState,
    create_initial_mission_record,
)
from nexus_vector.domain.mission_transitions import (
    MissionTransitionError,
    transition_effect,
    transition_mission,
    transition_mission_effect,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CREATED_AT = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
LATER = CREATED_AT + timedelta(seconds=1)

MISSION_TRANSITIONS = {
    MissionState.RECEIVED: {
        MissionState.VALIDATED,
        MissionState.MISSION_CONFLICT,
        MissionState.BLOCKED,
    },
    MissionState.VALIDATED: {
        MissionState.PERSISTED,
        MissionState.MISSION_CONFLICT,
        MissionState.BLOCKED,
    },
    MissionState.PERSISTED: {
        MissionState.RECONCILING,
        MissionState.BLOCKED,
    },
    MissionState.RECONCILING: {
        MissionState.READY_FOR_EXECUTION,
        MissionState.EXECUTION_UNKNOWN,
        MissionState.VERIFICATION_FAILED,
        MissionState.MANUAL_REVIEW_REQUIRED,
        MissionState.BLOCKED,
        MissionState.COMPLETED,
    },
    MissionState.READY_FOR_EXECUTION: {
        MissionState.EXECUTING,
        MissionState.RECONCILING,
        MissionState.MANUAL_REVIEW_REQUIRED,
        MissionState.BLOCKED,
    },
    MissionState.EXECUTING: {
        MissionState.VERIFYING,
        MissionState.EXECUTION_UNKNOWN,
        MissionState.RECONCILING,
        MissionState.MANUAL_REVIEW_REQUIRED,
        MissionState.BLOCKED,
    },
    MissionState.VERIFYING: {
        MissionState.COMPLETED,
        MissionState.EXECUTION_UNKNOWN,
        MissionState.VERIFICATION_FAILED,
        MissionState.RECONCILING,
        MissionState.MANUAL_REVIEW_REQUIRED,
        MissionState.BLOCKED,
    },
    MissionState.EXECUTION_UNKNOWN: {
        MissionState.RECONCILING,
        MissionState.MANUAL_REVIEW_REQUIRED,
        MissionState.BLOCKED,
    },
    MissionState.VERIFICATION_FAILED: {
        MissionState.RECONCILING,
        MissionState.MANUAL_REVIEW_REQUIRED,
        MissionState.BLOCKED,
    },
    MissionState.COMPLETED: set(),
    MissionState.MISSION_CONFLICT: set(),
    MissionState.BLOCKED: set(),
    MissionState.MANUAL_REVIEW_REQUIRED: set(),
}

EFFECT_TRANSITIONS = {
    EffectState.PLANNED: {
        EffectState.RESERVED,
        EffectState.BLOCKED,
    },
    EffectState.RESERVED: {
        EffectState.SUBMITTED,
        EffectState.BLOCKED,
    },
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
    EffectState.CHAIN_CONFIRMED: set(),
    EffectState.FAILED_FINAL: set(),
    EffectState.BLOCKED: set(),
}


def mission_request() -> MissionRequest:
    return MissionRequest(
        schema_version=SCHEMA_VERSION,
        mission_namespace="nexus-vector:transition-test",
        mission_ref="MISSION-TRANSITION-1",
        mission_type="ERC20_BATCH_PAYOUT",
        chain_id=84532,
        asset=AssetSpec(
            token_address=(
                "0x0000000000000000000000000000000000000001"
            ),
            decimals=6,
        ),
        effects=(
            EffectRequest(
                effect_ref="alpha",
                recipient=(
                    "0x00000000000000000000000000000000000000a1"
                ),
                amount_base_units=1_000_000,
            ),
            EffectRequest(
                effect_ref="beta",
                recipient=(
                    "0x00000000000000000000000000000000000000b2"
                ),
                amount_base_units=2_000_000,
            ),
            EffectRequest(
                effect_ref="gamma",
                recipient=(
                    "0x00000000000000000000000000000000000000c3"
                ),
                amount_base_units=3_000_000,
            ),
        ),
    )


def mission_record() -> MissionRecord:
    return create_initial_mission_record(mission_request(), CREATED_AT)


def record_in_state(
    state: MissionState,
    *,
    confirmed_effects: bool = False,
) -> MissionRecord:
    record = mission_record()
    effects = record.effects
    if confirmed_effects:
        effects = tuple(
            replace(effect, state=EffectState.CHAIN_CONFIRMED)
            for effect in effects
        )
    return replace(record, state=state, effects=effects)


def assert_transition_error(
    test_case: unittest.TestCase,
    code: str,
    operation: object,
) -> MissionTransitionError:
    with test_case.assertRaises(MissionTransitionError) as caught:
        if callable(operation):
            operation()
        else:
            raise AssertionError("operation must be callable")
    test_case.assertEqual(code, caught.exception.code)
    test_case.assertEqual(code, str(caught.exception))
    return caught.exception


class MissionTransitionMatrixTests(unittest.TestCase):
    def test_policy_classifies_every_mission_state(self) -> None:
        self.assertEqual(set(MissionState), set(MISSION_TRANSITIONS))

    def test_every_allowed_mission_transition_succeeds(self) -> None:
        for source, targets in MISSION_TRANSITIONS.items():
            for target in targets:
                with self.subTest(source=source, target=target):
                    original = record_in_state(
                        source,
                        confirmed_effects=(
                            target is MissionState.COMPLETED
                        ),
                    )
                    transitioned = transition_mission(
                        original,
                        target,
                        LATER,
                    )
                    self.assertIsNot(original, transitioned)
                    self.assertEqual(target, transitioned.state)
                    self.assertEqual(LATER, transitioned.updated_at_utc)
                    self.assertEqual(source, original.state)
                    self.assertEqual(CREATED_AT, original.updated_at_utc)

    def test_every_disallowed_mission_state_pair_fails_closed(self) -> None:
        for source in MissionState:
            for target in MissionState:
                if target in MISSION_TRANSITIONS[source]:
                    continue
                with self.subTest(source=source, target=target):
                    original = record_in_state(source)
                    assert_transition_error(
                        self,
                        "MISSION_TRANSITION_NOT_ALLOWED",
                        lambda original=original, target=target: (
                            transition_mission(original, target, LATER)
                        ),
                    )

    def test_execution_unknown_never_authorizes_execution(self) -> None:
        original = record_in_state(MissionState.EXECUTION_UNKNOWN)
        assert_transition_error(
            self,
            "MISSION_TRANSITION_NOT_ALLOWED",
            lambda: transition_mission(
                original,
                MissionState.READY_FOR_EXECUTION,
                LATER,
            ),
        )

    def test_terminal_mission_states_reject_all_outgoing_transitions(
        self,
    ) -> None:
        terminal_states = {
            MissionState.COMPLETED,
            MissionState.MISSION_CONFLICT,
            MissionState.BLOCKED,
            MissionState.MANUAL_REVIEW_REQUIRED,
        }
        for source in terminal_states:
            for target in MissionState:
                with self.subTest(source=source, target=target):
                    original = record_in_state(source)
                    assert_transition_error(
                        self,
                        "MISSION_TRANSITION_NOT_ALLOWED",
                        lambda original=original, target=target: (
                            transition_mission(original, target, LATER)
                        ),
                    )

    def test_completion_requires_every_effect_chain_confirmed(self) -> None:
        for source in (MissionState.RECONCILING, MissionState.VERIFYING):
            original = record_in_state(source)
            partly_confirmed = replace(
                original,
                effects=(
                    replace(
                        original.effects[0],
                        state=EffectState.CHAIN_CONFIRMED,
                    ),
                    *original.effects[1:],
                ),
            )
            for record in (original, partly_confirmed):
                with self.subTest(source=source, effects=record.effects):
                    assert_transition_error(
                        self,
                        "MISSION_COMPLETION_REQUIRES_CONFIRMED_EFFECTS",
                        lambda record=record: transition_mission(
                            record,
                            MissionState.COMPLETED,
                            LATER,
                        ),
                    )

    def test_completion_succeeds_when_all_effects_confirmed(self) -> None:
        for source in (MissionState.RECONCILING, MissionState.VERIFYING):
            original = record_in_state(source, confirmed_effects=True)
            transitioned = transition_mission(
                original,
                MissionState.COMPLETED,
                LATER,
            )
            self.assertEqual(MissionState.COMPLETED, transitioned.state)


class EffectTransitionMatrixTests(unittest.TestCase):
    def test_policy_classifies_every_effect_state(self) -> None:
        self.assertEqual(set(EffectState), set(EFFECT_TRANSITIONS))

    def test_every_allowed_effect_transition_succeeds(self) -> None:
        base_effect = mission_record().effects[0]
        for source, targets in EFFECT_TRANSITIONS.items():
            for target in targets:
                with self.subTest(source=source, target=target):
                    original = replace(base_effect, state=source)
                    transitioned = transition_effect(
                        original,
                        target,
                        LATER,
                    )
                    self.assertIsNot(original, transitioned)
                    self.assertEqual(target, transitioned.state)
                    self.assertEqual(LATER, transitioned.updated_at_utc)
                    self.assertEqual(source, original.state)

    def test_every_disallowed_effect_state_pair_fails_closed(self) -> None:
        base_effect = mission_record().effects[0]
        for source in EffectState:
            for target in EffectState:
                if target in EFFECT_TRANSITIONS[source]:
                    continue
                with self.subTest(source=source, target=target):
                    original = replace(base_effect, state=source)
                    assert_transition_error(
                        self,
                        "EFFECT_TRANSITION_NOT_ALLOWED",
                        lambda original=original, target=target: (
                            transition_effect(original, target, LATER)
                        ),
                    )

    def test_unknown_effect_never_returns_to_pre_submission_state(
        self,
    ) -> None:
        original = replace(
            mission_record().effects[0],
            state=EffectState.EXECUTION_UNKNOWN,
        )
        for target in (
            EffectState.PLANNED,
            EffectState.RESERVED,
            EffectState.SUBMITTED,
        ):
            with self.subTest(target=target):
                assert_transition_error(
                    self,
                    "EFFECT_TRANSITION_NOT_ALLOWED",
                    lambda target=target: transition_effect(
                        original,
                        target,
                        LATER,
                    ),
                )

    def test_terminal_effect_states_reject_all_outgoing_transitions(
        self,
    ) -> None:
        base_effect = mission_record().effects[0]
        for source in {
            EffectState.CHAIN_CONFIRMED,
            EffectState.FAILED_FINAL,
            EffectState.BLOCKED,
        }:
            for target in EffectState:
                with self.subTest(source=source, target=target):
                    original = replace(base_effect, state=source)
                    assert_transition_error(
                        self,
                        "EFFECT_TRANSITION_NOT_ALLOWED",
                        lambda original=original, target=target: (
                            transition_effect(original, target, LATER)
                        ),
                    )


class TimestampAndImmutabilityTests(unittest.TestCase):
    def test_naive_and_non_utc_timestamps_fail_for_every_api(self) -> None:
        record = mission_record()
        invalid_timestamps = (
            datetime(2026, 7, 31, 12, 0),
            datetime(
                2026,
                7,
                31,
                13,
                0,
                tzinfo=timezone(timedelta(hours=1)),
            ),
        )
        operations = (
            lambda timestamp: transition_mission(
                record,
                MissionState.VALIDATED,
                timestamp,
            ),
            lambda timestamp: transition_effect(
                record.effects[0],
                EffectState.RESERVED,
                timestamp,
            ),
            lambda timestamp: transition_mission_effect(
                record,
                "alpha",
                EffectState.RESERVED,
                timestamp,
            ),
        )
        for timestamp in invalid_timestamps:
            code = (
                "INVALID_TIMESTAMP"
                if timestamp.tzinfo is None
                else "NON_UTC_TIMESTAMP"
            )
            for operation in operations:
                with self.subTest(timestamp=timestamp, operation=operation):
                    assert_transition_error(
                        self,
                        code,
                        lambda operation=operation, timestamp=timestamp: (
                            operation(timestamp)
                        ),
                    )

    def test_earlier_timestamp_fails_for_every_api(self) -> None:
        record = mission_record()
        earlier = CREATED_AT - timedelta(microseconds=1)
        operations = (
            lambda: transition_mission(
                record,
                MissionState.VALIDATED,
                earlier,
            ),
            lambda: transition_effect(
                record.effects[0],
                EffectState.RESERVED,
                earlier,
            ),
            lambda: transition_mission_effect(
                record,
                "alpha",
                EffectState.RESERVED,
                earlier,
            ),
        )
        for operation in operations:
            assert_transition_error(
                self,
                "TIMESTAMP_BEFORE_CURRENT",
                operation,
            )

    def test_equal_and_later_timestamps_are_valid(self) -> None:
        for timestamp in (CREATED_AT, LATER):
            with self.subTest(timestamp=timestamp):
                record = mission_record()
                mission = transition_mission(
                    record,
                    MissionState.VALIDATED,
                    timestamp,
                )
                effect = transition_effect(
                    record.effects[0],
                    EffectState.RESERVED,
                    timestamp,
                )
                aggregate = transition_mission_effect(
                    record,
                    "alpha",
                    EffectState.RESERVED,
                    timestamp,
                )
                self.assertEqual(timestamp, mission.updated_at_utc)
                self.assertEqual(timestamp, effect.updated_at_utc)
                self.assertEqual(timestamp, aggregate.updated_at_utc)

    def test_aggregate_timestamp_must_cover_selected_effect(self) -> None:
        record = mission_record()
        effect_time = CREATED_AT + timedelta(seconds=2)
        selected = replace(
            record.effects[0],
            updated_at_utc=effect_time,
        )
        aggregate = replace(
            record,
            effects=(selected, *record.effects[1:]),
            updated_at_utc=effect_time,
        )
        assert_transition_error(
            self,
            "TIMESTAMP_BEFORE_CURRENT",
            lambda: transition_mission_effect(
                aggregate,
                "alpha",
                EffectState.RESERVED,
                LATER,
            ),
        )

    def test_created_timestamps_remain_unchanged(self) -> None:
        record = mission_record()
        mission = transition_mission(
            record,
            MissionState.VALIDATED,
            LATER,
        )
        effect = transition_effect(
            record.effects[0],
            EffectState.RESERVED,
            LATER,
        )
        aggregate = transition_mission_effect(
            record,
            "alpha",
            EffectState.RESERVED,
            LATER,
        )
        self.assertEqual(record.created_at_utc, mission.created_at_utc)
        self.assertEqual(
            record.effects[0].created_at_utc,
            effect.created_at_utc,
        )
        self.assertEqual(record.created_at_utc, aggregate.created_at_utc)
        self.assertEqual(
            record.effects[0].created_at_utc,
            aggregate.effects[0].created_at_utc,
        )

    def test_inputs_unchanged_outputs_distinct_and_frozen(self) -> None:
        record = mission_record()
        snapshot = record
        transitioned = transition_mission_effect(
            record,
            "alpha",
            EffectState.RESERVED,
            LATER,
        )
        self.assertEqual(snapshot, record)
        self.assertIsNot(record, transitioned)
        self.assertIsNot(record.effects[0], transitioned.effects[0])
        with self.assertRaises(FrozenInstanceError):
            transitioned.state = MissionState.BLOCKED  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            transitioned.effects[0].state = (  # type: ignore[misc]
                EffectState.BLOCKED
            )


class AggregateEffectSelectionTests(unittest.TestCase):
    def test_effect_selection_uses_canonical_identity_after_reordering(
        self,
    ) -> None:
        original = mission_record()
        reordered = replace(
            original,
            effects=tuple(reversed(original.effects)),
        )
        selected_id = reordered.effect_id_for("beta")
        transitioned = transition_mission_effect(
            reordered,
            "beta",
            EffectState.RESERVED,
            LATER,
        )

        by_id = {effect.effect_id: effect for effect in transitioned.effects}
        self.assertEqual(EffectState.RESERVED, by_id[selected_id].state)
        self.assertTrue(
            all(
                effect.state is EffectState.PLANNED
                for effect_id, effect in by_id.items()
                if effect_id != selected_id
            )
        )
        self.assertEqual(
            tuple(reversed(original.effects)),
            reordered.effects,
        )

    def test_unknown_effect_reference_fails_without_echo(self) -> None:
        secret_like_ref = "private-recipient-reference-123"
        error = assert_transition_error(
            self,
            "UNKNOWN_EFFECT_REF",
            lambda: transition_mission_effect(
                mission_record(),
                secret_like_ref,
                EffectState.RESERVED,
                LATER,
            ),
        )
        self.assertNotIn(secret_like_ref, str(error))

    def test_identity_request_and_economic_values_are_preserved(self) -> None:
        original = mission_record()
        transitioned = transition_mission_effect(
            original,
            "beta",
            EffectState.RESERVED,
            LATER,
        )
        self.assertIs(original.request, transitioned.request)
        self.assertEqual(original.mission_key, transitioned.mission_key)
        self.assertEqual(
            original.content_fingerprint,
            transitioned.content_fingerprint,
        )
        self.assertEqual(original.schema_version, transitioned.schema_version)
        original_effects = {
            effect.effect_id: effect for effect in original.effects
        }
        for effect in transitioned.effects:
            before = original_effects[effect.effect_id]
            self.assertEqual(before.mission_key, effect.mission_key)
            self.assertEqual(before.effect_ref, effect.effect_ref)
            self.assertEqual(before.chain_id, effect.chain_id)
            self.assertEqual(before.token_address, effect.token_address)
            self.assertEqual(before.token_decimals, effect.token_decimals)
            self.assertEqual(before.recipient, effect.recipient)
            self.assertEqual(
                before.amount_base_units,
                effect.amount_base_units,
            )


class ErrorAndPurityTests(unittest.TestCase):
    def test_invalid_public_argument_types_fail_with_safe_codes(self) -> None:
        record = mission_record()
        cases = (
            (
                "INVALID_MISSION_RECORD",
                lambda: transition_mission(  # type: ignore[arg-type]
                    object(),
                    MissionState.VALIDATED,
                    LATER,
                ),
            ),
            (
                "INVALID_MISSION_TARGET_STATE",
                lambda: transition_mission(  # type: ignore[arg-type]
                    record,
                    "VALIDATED",
                    LATER,
                ),
            ),
            (
                "INVALID_EFFECT_RECORD",
                lambda: transition_effect(  # type: ignore[arg-type]
                    object(),
                    EffectState.RESERVED,
                    LATER,
                ),
            ),
            (
                "INVALID_EFFECT_TARGET_STATE",
                lambda: transition_effect(  # type: ignore[arg-type]
                    record.effects[0],
                    "RESERVED",
                    LATER,
                ),
            ),
        )
        for code, operation in cases:
            with self.subTest(code=code):
                assert_transition_error(self, code, operation)

    def test_transition_apis_write_no_console_output(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            record = mission_record()
            transition_mission(
                record,
                MissionState.VALIDATED,
                LATER,
            )
            transition_effect(
                record.effects[0],
                EffectState.RESERVED,
                LATER,
            )
            transition_mission_effect(
                record,
                "alpha",
                EffectState.RESERVED,
                LATER,
            )
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_module_has_no_external_action_capabilities(self) -> None:
        module_path = (
            PROJECT_ROOT
            / "src"
            / "nexus_vector"
            / "domain"
            / "mission_transitions.py"
        )
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_import_roots = {
            "http",
            "logging",
            "os",
            "pathlib",
            "random",
            "requests",
            "socket",
            "sqlite3",
            "subprocess",
            "urllib",
        }
        forbidden_calls = {
            "connect",
            "getenv",
            "now",
            "open",
            "popen",
            "print",
            "run",
            "sleep",
            "system",
            "urlopen",
            "utcnow",
            "write",
            "write_bytes",
            "write_text",
        }
        imported_roots: set[str] = set()
        called_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id.lower())
                elif isinstance(node.func, ast.Attribute):
                    called_names.add(node.func.attr.lower())

        self.assertTrue(forbidden_import_roots.isdisjoint(imported_roots))
        self.assertTrue(forbidden_calls.isdisjoint(called_names))
        self.assertNotIn("results_private", source)
        self.assertNotIn("keeperhub", source.lower())
        self.assertNotIn("retry", source.lower())


if __name__ == "__main__":
    unittest.main()
