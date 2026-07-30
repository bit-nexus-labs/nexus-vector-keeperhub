from __future__ import annotations

import ast
import contextlib
import copy
import io
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.domain.mission_identity import (
    SCHEMA_VERSION,
    derive_effect_id,
)
from nexus_vector.domain.mission_models import (
    AssetSpec,
    EffectRecord,
    EffectRequest,
    EffectState,
    MissionModelValidationError,
    MissionRecord,
    MissionRequest,
    MissionState,
    create_initial_mission_record,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CREATED_AT = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)


def request_mapping() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mission_namespace": "nexus-vector:synthetic-test",
        "mission_ref": "PAYOUT-1042",
        "mission_type": "ERC20_BATCH_PAYOUT",
        "chain_id": 84532,
        "asset": {
            "token_address": (
                "0x0000000000000000000000000000000000000001"
            ),
            "decimals": 6,
        },
        "effects": [
            {
                "effect_ref": "alpha",
                "recipient": (
                    "0x00000000000000000000000000000000000000a1"
                ),
                "amount_base_units": 10_000_000,
            },
            {
                "effect_ref": "beta",
                "recipient": (
                    "0x00000000000000000000000000000000000000b2"
                ),
                "amount_base_units": 2_500_000,
            },
            {
                "effect_ref": "gamma",
                "recipient": (
                    "0x00000000000000000000000000000000000000c3"
                ),
                "amount_base_units": 7_250_000,
            },
        ],
    }


def mission_request() -> MissionRequest:
    return MissionRequest.from_mapping(request_mapping())


def mission_record() -> MissionRecord:
    return create_initial_mission_record(mission_request(), CREATED_AT)


def assert_model_error(
    test_case: unittest.TestCase,
    expected_code: str,
    operation: object,
) -> None:
    with test_case.assertRaises(MissionModelValidationError) as caught:
        if callable(operation):
            operation()
        else:
            raise AssertionError("operation must be callable")
    test_case.assertEqual(expected_code, caught.exception.code)
    test_case.assertEqual(expected_code, str(caught.exception))


class PublicContractTests(unittest.TestCase):
    def test_exact_state_vocabularies(self) -> None:
        self.assertEqual(
            {
                "RECEIVED",
                "VALIDATED",
                "PERSISTED",
                "RECONCILING",
                "READY_FOR_EXECUTION",
                "EXECUTING",
                "VERIFYING",
                "COMPLETED",
                "MISSION_CONFLICT",
                "BLOCKED",
                "EXECUTION_UNKNOWN",
                "VERIFICATION_FAILED",
                "MANUAL_REVIEW_REQUIRED",
            },
            {state.value for state in MissionState},
        )
        self.assertEqual(
            {
                "PLANNED",
                "RESERVED",
                "SUBMITTED",
                "EXECUTION_UNKNOWN",
                "CHAIN_CONFIRMED",
                "FAILED_FINAL",
                "BLOCKED",
            },
            {state.value for state in EffectState},
        )

    def test_valid_three_recipient_request_builds_initial_record(self) -> None:
        request = mission_request()
        record = create_initial_mission_record(request, CREATED_AT)

        self.assertEqual(SCHEMA_VERSION, record.schema_version)
        self.assertEqual(request, record.request)
        self.assertEqual(MissionState.RECEIVED, record.state)
        self.assertEqual(3, len(record.effects))
        self.assertEqual(CREATED_AT, record.created_at_utc)
        self.assertEqual(CREATED_AT, record.updated_at_utc)
        self.assertTrue(
            all(effect.state is EffectState.PLANNED for effect in record.effects)
        )
        self.assertTrue(
            all(
                effect.created_at_utc == CREATED_AT
                and effect.updated_at_utc == CREATED_AT
                for effect in record.effects
            )
        )

        with self.assertRaises(FrozenInstanceError):
            record.state = MissionState.COMPLETED  # type: ignore[misc]

    def test_identity_document_shape_matches_public_boundary(self) -> None:
        request = mission_request()
        document = request.to_identity_document()

        self.assertEqual(
            {
                "mission_namespace",
                "mission_ref",
                "mission_type",
                "chain_id",
                "asset",
                "effects",
            },
            set(document),
        )
        self.assertNotIn("schema_version", document)
        self.assertEqual(request.build_identity(), request.build_identity())

    def test_explicit_effect_reference_lookup_is_correct_and_immutable(
        self,
    ) -> None:
        record = mission_record()
        expected = {
            effect.effect_ref: derive_effect_id(
                record.mission_key,
                effect.effect_ref,
            )
            for effect in record.request.effects
        }

        self.assertEqual(expected, dict(record.effect_ids_by_ref))
        for effect_ref, effect_id in expected.items():
            self.assertEqual(effect_id, record.effect_id_for(effect_ref))
        with self.assertRaises(TypeError):
            record.effect_ids_by_ref["alpha"] = "forged"  # type: ignore[index]
        assert_model_error(
            self,
            "UNKNOWN_EFFECT_REF",
            lambda: record.effect_id_for("not-present"),
        )

    def test_reordered_request_effects_preserve_identity_and_mapping(
        self,
    ) -> None:
        original = mission_record()
        reordered_mapping = request_mapping()
        reordered_mapping["effects"].reverse()  # type: ignore[union-attr]
        reordered = create_initial_mission_record(
            MissionRequest.from_mapping(reordered_mapping),
            CREATED_AT,
        )

        self.assertEqual(original.mission_key, reordered.mission_key)
        self.assertEqual(
            original.content_fingerprint,
            reordered.content_fingerprint,
        )
        self.assertEqual(
            dict(original.effect_ids_by_ref),
            dict(reordered.effect_ids_by_ref),
        )

    def test_reordered_effect_record_storage_preserves_lookup_semantics(
        self,
    ) -> None:
        original = mission_record()
        reordered = replace(
            original,
            effects=tuple(reversed(original.effects)),
        )

        self.assertNotEqual(original.effects, reordered.effects)
        self.assertEqual(
            dict(original.effect_ids_by_ref),
            dict(reordered.effect_ids_by_ref),
        )
        for effect_ref in original.effect_ids_by_ref:
            self.assertEqual(
                original.effect_id_for(effect_ref),
                reordered.effect_id_for(effect_ref),
            )

    def test_changed_recipient_or_amount_changes_only_fingerprint(
        self,
    ) -> None:
        original = mission_record()
        changed_mappings = []

        changed_recipient = request_mapping()
        changed_recipient["effects"][0]["recipient"] = (  # type: ignore[index]
            "0x00000000000000000000000000000000000000d4"
        )
        changed_mappings.append(changed_recipient)

        changed_amount = request_mapping()
        changed_amount["effects"][0]["amount_base_units"] = (  # type: ignore[index]
            10_000_001
        )
        changed_mappings.append(changed_amount)

        for changed_mapping in changed_mappings:
            with self.subTest(change=changed_mapping):
                changed = create_initial_mission_record(
                    MissionRequest.from_mapping(changed_mapping),
                    CREATED_AT,
                )
                self.assertEqual(original.mission_key, changed.mission_key)
                self.assertEqual(
                    dict(original.effect_ids_by_ref),
                    dict(changed.effect_ids_by_ref),
                )
                self.assertNotEqual(
                    original.content_fingerprint,
                    changed.content_fingerprint,
                )


class FailClosedRecordValidationTests(unittest.TestCase):
    def test_forged_mission_key_fails_closed(self) -> None:
        record = mission_record()
        assert_model_error(
            self,
            "MISSION_KEY_MISMATCH",
            lambda: replace(
                record,
                mission_key=(
                    "msn_"
                    "000000000000000000000000000000000000000000000000"
                    "0000000000000000"
                ),
            ),
        )

    def test_forged_content_fingerprint_fails_closed(self) -> None:
        record = mission_record()
        assert_model_error(
            self,
            "CONTENT_FINGERPRINT_MISMATCH",
            lambda: replace(
                record,
                content_fingerprint=(
                    "mfp_"
                    "000000000000000000000000000000000000000000000000"
                    "0000000000000000"
                ),
            ),
        )

    def test_forged_effect_id_fails_closed(self) -> None:
        record = mission_record()
        assert_model_error(
            self,
            "EFFECT_ID_MISMATCH",
            lambda: replace(
                record.effects[0],
                effect_id=(
                    "eff_"
                    "000000000000000000000000000000000000000000000000"
                    "0000000000000000"
                ),
            ),
        )

    def test_duplicate_missing_and_unexpected_effect_refs_fail_closed(
        self,
    ) -> None:
        record = mission_record()

        assert_model_error(
            self,
            "DUPLICATE_EFFECT_REF",
            lambda: replace(
                record,
                effects=(
                    record.effects[0],
                    record.effects[0],
                    *record.effects[1:],
                ),
            ),
        )
        assert_model_error(
            self,
            "MISSING_EFFECT_REF",
            lambda: replace(record, effects=record.effects[:-1]),
        )

        extra_ref = "unexpected"
        extra = EffectRecord(
            mission_key=record.mission_key,
            effect_ref=extra_ref,
            effect_id=derive_effect_id(record.mission_key, extra_ref),
            chain_id=record.request.chain_id,
            token_address=record.request.asset.token_address,
            token_decimals=record.request.asset.decimals,
            recipient=(
                "0x00000000000000000000000000000000000000e5"
            ),
            amount_base_units=1,
            state=EffectState.PLANNED,
            created_at_utc=CREATED_AT,
            updated_at_utc=CREATED_AT,
        )
        assert_model_error(
            self,
            "UNEXPECTED_EFFECT_REF",
            lambda: replace(record, effects=(*record.effects, extra)),
        )

    def test_cross_mission_effect_record_fails_closed(self) -> None:
        original = mission_record()
        other_mapping = request_mapping()
        other_mapping["mission_ref"] = "PAYOUT-2042"
        other = create_initial_mission_record(
            MissionRequest.from_mapping(other_mapping),
            CREATED_AT,
        )

        assert_model_error(
            self,
            "EFFECT_MISSION_KEY_MISMATCH",
            lambda: replace(
                original,
                effects=(other.effects[0], *original.effects[1:]),
            ),
        )

    def test_mismatched_effect_economic_data_fails_closed(self) -> None:
        record = mission_record()
        effect = record.effects[0]
        mismatches = (
            {"chain_id": effect.chain_id + 1},
            {
                "token_address": (
                    "0x0000000000000000000000000000000000000002"
                )
            },
            {"token_decimals": effect.token_decimals + 1},
            {
                "recipient": (
                    "0x00000000000000000000000000000000000000f6"
                )
            },
            {"amount_base_units": effect.amount_base_units + 1},
        )

        for mismatch in mismatches:
            with self.subTest(mismatch=mismatch):
                changed_effect = replace(effect, **mismatch)
                changed_effects = (changed_effect, *record.effects[1:])
                assert_model_error(
                    self,
                    "EFFECT_ECONOMIC_MISMATCH",
                    lambda changed_effects=changed_effects: replace(
                        record,
                        effects=changed_effects,
                    ),
                )

    def test_naive_and_non_utc_timestamps_fail_closed(self) -> None:
        request = mission_request()
        naive = datetime(2026, 7, 30, 9, 0)
        non_utc = datetime(
            2026,
            7,
            30,
            10,
            0,
            tzinfo=timezone(timedelta(hours=1)),
        )

        assert_model_error(
            self,
            "INVALID_TIMESTAMP",
            lambda: create_initial_mission_record(request, naive),
        )
        assert_model_error(
            self,
            "NON_UTC_TIMESTAMP",
            lambda: create_initial_mission_record(request, non_utc),
        )

    def test_reversed_record_and_effect_timestamps_fail_closed(self) -> None:
        record = mission_record()
        earlier = CREATED_AT - timedelta(microseconds=1)

        assert_model_error(
            self,
            "REVERSED_TIMESTAMP",
            lambda: replace(record, updated_at_utc=earlier),
        )
        assert_model_error(
            self,
            "REVERSED_TIMESTAMP",
            lambda: replace(record.effects[0], updated_at_utc=earlier),
        )

    def test_invalid_state_types_fail_closed(self) -> None:
        record = mission_record()
        assert_model_error(
            self,
            "INVALID_MISSION_STATE",
            lambda: replace(
                record,
                state="RECEIVED",  # type: ignore[arg-type]
            ),
        )
        assert_model_error(
            self,
            "INVALID_EFFECT_STATE",
            lambda: replace(
                record.effects[0],
                state="PLANNED",  # type: ignore[arg-type]
            ),
        )

    def test_mutable_effect_containers_are_rejected(self) -> None:
        request = mission_request()
        record = mission_record()

        assert_model_error(
            self,
            "INVALID_EFFECT_REQUESTS_CONTAINER",
            lambda: MissionRequest(
                schema_version=request.schema_version,
                mission_namespace=request.mission_namespace,
                mission_ref=request.mission_ref,
                mission_type=request.mission_type,
                chain_id=request.chain_id,
                asset=request.asset,
                effects=list(request.effects),  # type: ignore[arg-type]
            ),
        )
        assert_model_error(
            self,
            "INVALID_EFFECTS_CONTAINER",
            lambda: MissionRecord(
                schema_version=record.schema_version,
                mission_key=record.mission_key,
                content_fingerprint=record.content_fingerprint,
                request=record.request,
                state=record.state,
                effects=list(record.effects),  # type: ignore[arg-type]
                created_at_utc=record.created_at_utc,
                updated_at_utc=record.updated_at_utc,
            ),
        )

    def test_mapping_construction_converts_without_retaining_aliases(
        self,
    ) -> None:
        source = request_mapping()
        source_effects = source["effects"]
        request = MissionRequest.from_mapping(source)
        source_effects.clear()  # type: ignore[union-attr]

        self.assertEqual(3, len(request.effects))
        self.assertIsInstance(request.effects, tuple)


class StrictBusinessInputTests(unittest.TestCase):
    def test_runtime_provider_and_transaction_fields_are_absent(self) -> None:
        request_fields = {field.name for field in fields(MissionRequest)}
        forbidden_fields = {
            "provider_request_id",
            "provider_execution_id",
            "http_method",
            "url",
            "headers",
            "http_status",
            "response_body",
            "retry_count",
            "retry_policy",
            "idempotency_key",
            "transaction_hash",
            "transaction_link",
            "wallet_address",
            "signer",
            "key_id",
            "wallet_metadata",
            "gas",
            "nonce",
            "fee",
            "reconciliation_evidence",
            "verification_evidence",
        }
        self.assertTrue(request_fields.isdisjoint(forbidden_fields))

    def test_strict_mapping_rejects_every_unknown_runtime_field(self) -> None:
        forbidden_fields = (
            "provider_request_id",
            "provider_execution_id",
            "http_method",
            "url",
            "headers",
            "http_status",
            "response_body",
            "retry_count",
            "retry_policy",
            "idempotency_key",
            "transaction_hash",
            "transaction_link",
            "wallet_address",
            "signer",
            "key_id",
            "wallet_metadata",
            "gas",
            "nonce",
            "fee",
            "reconciliation_evidence",
            "verification_evidence",
        )
        for forbidden_field in forbidden_fields:
            with self.subTest(field=forbidden_field):
                value = request_mapping()
                value[forbidden_field] = "synthetic-forbidden-value"
                assert_model_error(
                    self,
                    "UNKNOWN_FIELD",
                    lambda value=value: MissionRequest.from_mapping(value),
                )

    def test_strict_mapping_rejects_unknown_nested_fields(self) -> None:
        asset_unknown = request_mapping()
        asset_unknown["asset"]["symbol"] = "SYN"  # type: ignore[index]
        effect_unknown = request_mapping()
        effect_unknown["effects"][0]["memo"] = "synthetic"  # type: ignore[index]

        for value in (asset_unknown, effect_unknown):
            with self.subTest(value=value):
                assert_model_error(
                    self,
                    "UNKNOWN_FIELD",
                    lambda value=value: MissionRequest.from_mapping(value),
                )

    def test_public_value_objects_are_immutable(self) -> None:
        asset = AssetSpec(
            token_address=(
                "0x0000000000000000000000000000000000000001"
            ),
            decimals=6,
        )
        effect = EffectRequest(
            effect_ref="synthetic-effect",
            recipient=(
                "0x00000000000000000000000000000000000000a1"
            ),
            amount_base_units=1,
        )
        with self.assertRaises(FrozenInstanceError):
            asset.decimals = 18  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            effect.amount_base_units = 2  # type: ignore[misc]


class PurityTests(unittest.TestCase):
    def test_models_and_helper_write_no_console_output(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            request = mission_request()
            record = create_initial_mission_record(request, CREATED_AT)
            record.effect_id_for("alpha")
            request.to_identity_document()
            request.build_identity()

        self.assertEqual("", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_module_has_no_io_or_external_action_capabilities(self) -> None:
        module_path = (
            PROJECT_ROOT
            / "src"
            / "nexus_vector"
            / "domain"
            / "mission_models.py"
        )
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_import_roots = {
            "http",
            "logging",
            "os",
            "pathlib",
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
        self.assertNotIn("KEEPERHUB", source.upper())
        self.assertNotIn("sqlite", source.lower())
        self.assertNotIn("transition", source.lower())
        self.assertNotIn("retry", source.lower())


if __name__ == "__main__":
    unittest.main()
