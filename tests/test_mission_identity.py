from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import unittest
from pathlib import Path

from nexus_vector.domain import mission_identity


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MISSION_KEY_VECTOR = (
    "msn_0732c1c9685b70bcdb2246836fd100d2"
    "dbdce249d5e8a627cad2c082b717e66f"
)
CONTENT_FINGERPRINT_VECTOR = (
    "mfp_f232e8c7674034b72edec8a14fcf4235"
    "6d2bbffc2caf4af37c44e9b25251f030"
)
ANNA_EFFECT_ID_VECTOR = (
    "eff_368a6725b6d8fd85713cda2fa595cbe54"
    "d18fd0ea34dcf9f975f4f3ff2533103"
)
BOB_EFFECT_ID_VECTOR = (
    "eff_94df9ab81823cbfa1e5f6295d1218cf1d"
    "bf17856e54572ba3d4e85d9eee27a49"
)


def mission_document() -> dict[str, object]:
    return {
        "mission_namespace": "nexus-vector:keeperhub-hackathon",
        "mission_ref": "PAYOUT-1042",
        "mission_type": "ERC20_BATCH_PAYOUT",
        "chain_id": 84532,
        "asset": {
            "token_address": "0x0000000000000000000000000000000000000001",
            "decimals": 6,
        },
        "effects": [
            {
                "effect_ref": "anna",
                "recipient": "0x00000000000000000000000000000000000000A1",
                "amount_base_units": 10000000,
            },
            {
                "effect_ref": "bob",
                "recipient": "0x00000000000000000000000000000000000000B2",
                "amount_base_units": 2500000,
            },
        ],
    }


def assert_validation_error(
    test_case: unittest.TestCase,
    document: object,
    expected_code: str,
    *,
    schema_version: str = mission_identity.SCHEMA_VERSION,
) -> None:
    with test_case.assertRaises(
        mission_identity.MissionValidationError
    ) as caught:
        mission_identity.build_mission_identity(
            document,
            schema_version=schema_version,
        )
    test_case.assertEqual(expected_code, caught.exception.code)
    test_case.assertEqual(expected_code, str(caught.exception))


class KnownVectorTests(unittest.TestCase):
    def test_exact_deterministic_known_vectors(self) -> None:
        identity = mission_identity.build_mission_identity(
            mission_document()
        )
        self.assertEqual(
            mission_identity.SCHEMA_VERSION,
            identity.schema_version,
        )
        self.assertEqual(MISSION_KEY_VECTOR, identity.mission_key)
        self.assertEqual(
            CONTENT_FINGERPRINT_VECTOR,
            identity.content_fingerprint,
        )
        self.assertEqual(
            (ANNA_EFFECT_ID_VECTOR, BOB_EFFECT_ID_VECTOR),
            identity.effect_ids,
        )
        self.assertEqual(
            ANNA_EFFECT_ID_VECTOR,
            mission_identity.derive_effect_id(
                identity.mission_key,
                "anna",
            ),
        )
        self.assertEqual(
            BOB_EFFECT_ID_VECTOR,
            mission_identity.derive_effect_id(
                identity.mission_key,
                "bob",
            ),
        )

    def test_object_key_reordering_preserves_identity(self) -> None:
        original = mission_document()
        reordered = {
            "effects": [
                {
                    "amount_base_units": effect["amount_base_units"],
                    "recipient": effect["recipient"],
                    "effect_ref": effect["effect_ref"],
                }
                for effect in original["effects"]  # type: ignore[index]
            ],
            "asset": {
                "decimals": original["asset"]["decimals"],  # type: ignore[index]
                "token_address": original["asset"]["token_address"],  # type: ignore[index]
            },
            "chain_id": original["chain_id"],
            "mission_type": original["mission_type"],
            "mission_ref": original["mission_ref"],
            "mission_namespace": original["mission_namespace"],
        }
        self.assertEqual(
            mission_identity.build_mission_identity(original),
            mission_identity.build_mission_identity(reordered),
        )

    def test_effect_list_reordering_preserves_fingerprint_and_ids(self) -> None:
        original = mission_document()
        reordered = copy.deepcopy(original)
        reordered["effects"].reverse()  # type: ignore[union-attr]
        self.assertEqual(
            mission_identity.build_mission_identity(original),
            mission_identity.build_mission_identity(reordered),
        )

    def test_unicode_nfc_equivalence_preserves_identity(self) -> None:
        composed = mission_document()
        decomposed = mission_document()
        composed["mission_ref"] = "CAFÉ-1042"
        decomposed["mission_ref"] = "CAFE\u0301-1042"
        composed["effects"][0]["effect_ref"] = "café"  # type: ignore[index]
        decomposed["effects"][0]["effect_ref"] = "cafe\u0301"  # type: ignore[index]
        self.assertEqual(
            mission_identity.build_mission_identity(composed),
            mission_identity.build_mission_identity(decomposed),
        )

    def test_repeated_fresh_construction_is_process_independent(self) -> None:
        first_document = mission_document()
        second_document = json.loads(
            json.dumps(mission_document(), separators=(",", ":"))
        )
        self.assertIsNot(first_document, second_document)
        self.assertEqual(
            mission_identity.build_mission_identity(first_document),
            mission_identity.build_mission_identity(second_document),
        )

    def test_address_case_is_normalized_after_shape_validation(self) -> None:
        uppercase = mission_document()
        lowercase = copy.deepcopy(uppercase)
        lowercase["effects"][0]["recipient"] = (  # type: ignore[index]
            "0x00000000000000000000000000000000000000a1"
        )
        self.assertEqual(
            mission_identity.build_mission_identity(uppercase),
            mission_identity.build_mission_identity(lowercase),
        )

    def test_arbitrary_business_strings_are_not_trimmed_or_case_folded(self) -> None:
        original = mission_identity.build_mission_identity(
            mission_document()
        )
        whitespace_changed = mission_document()
        whitespace_changed["mission_ref"] = " PAYOUT-1042 "
        case_changed = mission_document()
        case_changed["mission_ref"] = "payout-1042"
        self.assertNotEqual(
            original.mission_key,
            mission_identity.build_mission_identity(
                whitespace_changed
            ).mission_key,
        )
        self.assertNotEqual(
            original.mission_key,
            mission_identity.build_mission_identity(
                case_changed
            ).mission_key,
        )


class ConflictClassificationTests(unittest.TestCase):
    def test_new_and_same_mission_classification(self) -> None:
        identity = mission_identity.build_mission_identity(
            mission_document()
        )
        self.assertEqual(
            mission_identity.MissionComparison.NEW_MISSION,
            mission_identity.classify_mission(None, identity),
        )
        self.assertEqual(
            mission_identity.MissionComparison.SAME_MISSION,
            mission_identity.classify_mission(identity, identity),
        )

    def test_changed_amount_keeps_key_and_effect_id_but_conflicts(self) -> None:
        original = mission_identity.build_mission_identity(
            mission_document()
        )
        changed_document = mission_document()
        changed_document["effects"][0]["amount_base_units"] = 10000001  # type: ignore[index]
        changed = mission_identity.build_mission_identity(changed_document)
        self.assertEqual(original.mission_key, changed.mission_key)
        self.assertEqual(original.effect_ids, changed.effect_ids)
        self.assertNotEqual(
            original.content_fingerprint,
            changed.content_fingerprint,
        )
        self.assertEqual(
            mission_identity.MissionComparison.MISSION_CONFLICT,
            mission_identity.classify_mission(original, changed),
        )

    def test_changed_recipient_conflicts(self) -> None:
        original = mission_identity.build_mission_identity(
            mission_document()
        )
        changed_document = mission_document()
        changed_document["effects"][0]["recipient"] = (  # type: ignore[index]
            "0x00000000000000000000000000000000000000C3"
        )
        changed = mission_identity.build_mission_identity(changed_document)
        self.assertEqual(original.mission_key, changed.mission_key)
        self.assertEqual(original.effect_ids, changed.effect_ids)
        self.assertEqual(
            mission_identity.MissionComparison.MISSION_CONFLICT,
            mission_identity.classify_mission(original, changed),
        )

    def test_changed_token_or_chain_conflicts(self) -> None:
        original = mission_identity.build_mission_identity(
            mission_document()
        )
        changed_token = mission_document()
        changed_token["asset"]["token_address"] = (  # type: ignore[index]
            "0x0000000000000000000000000000000000000002"
        )
        changed_chain = mission_document()
        changed_chain["chain_id"] = 11155111
        for changed_document in (changed_token, changed_chain):
            with self.subTest(change=changed_document):
                changed = mission_identity.build_mission_identity(
                    changed_document
                )
                self.assertEqual(original.mission_key, changed.mission_key)
                self.assertEqual(
                    mission_identity.MissionComparison.MISSION_CONFLICT,
                    mission_identity.classify_mission(original, changed),
                )

    def test_different_reference_is_different_mission_not_conflict(self) -> None:
        original = mission_identity.build_mission_identity(
            mission_document()
        )
        different_document = mission_document()
        different_document["mission_ref"] = "PAYOUT-1043"
        different = mission_identity.build_mission_identity(
            different_document
        )
        self.assertNotEqual(original.mission_key, different.mission_key)
        self.assertEqual(
            mission_identity.MissionComparison.DIFFERENT_MISSION,
            mission_identity.classify_mission(original, different),
        )


class ValidationTests(unittest.TestCase):
    def test_duplicate_effect_ref_after_nfc_is_rejected(self) -> None:
        document = mission_document()
        document["effects"][0]["effect_ref"] = "café"  # type: ignore[index]
        document["effects"][1]["effect_ref"] = "cafe\u0301"  # type: ignore[index]
        assert_validation_error(
            self,
            document,
            "DUPLICATE_EFFECT_REF",
        )

    def test_invalid_amount_values_are_rejected(self) -> None:
        for invalid in (1.5, True, "100", 0, -1):
            with self.subTest(invalid=repr(invalid)):
                document = mission_document()
                document["effects"][0]["amount_base_units"] = invalid  # type: ignore[index]
                assert_validation_error(
                    self,
                    document,
                    "INVALID_AMOUNT_BASE_UNITS",
                )

    def test_unknown_fields_are_rejected_at_every_level(self) -> None:
        documents = []
        top_level = mission_document()
        top_level["provider_request_id"] = "synthetic-request"
        documents.append(top_level)
        asset = mission_document()
        asset["asset"]["symbol"] = "SYN"  # type: ignore[index]
        documents.append(asset)
        effect = mission_document()
        effect["effects"][0]["memo"] = "synthetic-memo"  # type: ignore[index]
        documents.append(effect)
        for document in documents:
            with self.subTest(document=document):
                assert_validation_error(
                    self,
                    document,
                    "UNKNOWN_FIELD",
                )

    def test_invalid_addresses_are_rejected(self) -> None:
        invalid_addresses = (
            "0000000000000000000000000000000000000001",
            "0x1234",
            "0x00000000000000000000000000000000000000zz",
            7,
        )
        for invalid in invalid_addresses:
            with self.subTest(invalid=repr(invalid)):
                token_document = mission_document()
                token_document["asset"]["token_address"] = invalid  # type: ignore[index]
                assert_validation_error(
                    self,
                    token_document,
                    "INVALID_ADDRESS",
                )
                recipient_document = mission_document()
                recipient_document["effects"][0]["recipient"] = invalid  # type: ignore[index]
                assert_validation_error(
                    self,
                    recipient_document,
                    "INVALID_ADDRESS",
                )

    def test_invalid_chain_ids_and_decimals_are_rejected(self) -> None:
        for invalid in (1.5, True, "84532", 0, -1):
            with self.subTest(field="chain_id", invalid=repr(invalid)):
                document = mission_document()
                document["chain_id"] = invalid
                assert_validation_error(
                    self,
                    document,
                    "INVALID_CHAIN_ID",
                )
        for invalid in (1.5, True, "6", -1):
            with self.subTest(field="decimals", invalid=repr(invalid)):
                document = mission_document()
                document["asset"]["decimals"] = invalid  # type: ignore[index]
                assert_validation_error(
                    self,
                    document,
                    "INVALID_DECIMALS",
                )

    def test_missing_empty_and_invalid_shapes_are_rejected(self) -> None:
        missing = mission_document()
        del missing["mission_ref"]
        assert_validation_error(
            self,
            missing,
            "MISSING_REQUIRED_FIELD",
        )
        empty_string = mission_document()
        empty_string["mission_ref"] = ""
        assert_validation_error(self, empty_string, "EMPTY_STRING")
        empty_effects = mission_document()
        empty_effects["effects"] = []
        assert_validation_error(self, empty_effects, "EMPTY_EFFECTS")
        invalid_asset = mission_document()
        invalid_asset["asset"] = []
        assert_validation_error(
            self,
            invalid_asset,
            "INVALID_ASSET_SHAPE",
        )
        invalid_effects = mission_document()
        invalid_effects["effects"] = {}
        assert_validation_error(
            self,
            invalid_effects,
            "INVALID_EFFECTS_SHAPE",
        )
        assert_validation_error(
            self,
            [],
            "INVALID_DOCUMENT_SHAPE",
        )

    def test_unsupported_schema_version_is_rejected(self) -> None:
        assert_validation_error(
            self,
            mission_document(),
            "UNSUPPORTED_SCHEMA_VERSION",
            schema_version="nexus-vector.mission-identity.v2",
        )

    def test_invalid_mission_key_and_identity_result_are_rejected(self) -> None:
        with self.assertRaises(
            mission_identity.MissionValidationError
        ) as caught:
            mission_identity.derive_effect_id(
                "msn_not_valid",
                "anna",
            )
        self.assertEqual("INVALID_MISSION_KEY", caught.exception.code)
        identity = mission_identity.build_mission_identity(
            mission_document()
        )
        with self.assertRaises(
            mission_identity.MissionValidationError
        ) as classify_error:
            mission_identity.classify_mission(
                "not-an-identity",  # type: ignore[arg-type]
                identity,
            )
        self.assertEqual(
            "INVALID_IDENTITY_RESULT",
            classify_error.exception.code,
        )

    def test_errors_and_results_do_not_echo_sensitive_raw_values(self) -> None:
        sensitive_ref = "SENSITIVE_MISSION_REFERENCE"
        sensitive_recipient = (
            "0x00000000000000000000000000000000000000D4"
        )
        valid_document = mission_document()
        valid_document["mission_ref"] = sensitive_ref
        valid_document["effects"][0]["recipient"] = sensitive_recipient  # type: ignore[index]
        identity = mission_identity.build_mission_identity(valid_document)
        serialized_result = repr(identity)
        self.assertNotIn(sensitive_ref, serialized_result)
        self.assertNotIn(sensitive_recipient, serialized_result)

        invalid_document = mission_document()
        invalid_document["SENSITIVE_UNKNOWN_FIELD"] = "SENSITIVE_VALUE"
        with self.assertRaises(
            mission_identity.MissionValidationError
        ) as caught:
            mission_identity.build_mission_identity(invalid_document)
        error_text = repr(caught.exception)
        self.assertEqual("UNKNOWN_FIELD", caught.exception.code)
        for forbidden in (
            "SENSITIVE_UNKNOWN_FIELD",
            "SENSITIVE_VALUE",
            sensitive_ref,
            sensitive_recipient,
        ):
            self.assertNotIn(forbidden, error_text)


class PurityTests(unittest.TestCase):
    def test_module_has_no_side_effect_capability_imports_or_calls(self) -> None:
        module_path = (
            PROJECT_ROOT
            / "src"
            / "nexus_vector"
            / "domain"
            / "mission_identity.py"
        )
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_import_roots = {
            "datetime",
            "http",
            "logging",
            "os",
            "pathlib",
            "random",
            "requests",
            "socket",
            "sqlite3",
            "subprocess",
            "time",
            "urllib",
            "uuid",
        }
        forbidden_calls = {
            "connect",
            "getenv",
            "open",
            "print",
            "run",
            "sleep",
            "time",
            "urlopen",
            "uuid4",
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
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called_names.add(node.func.attr)

        self.assertTrue(forbidden_import_roots.isdisjoint(imported_roots))
        self.assertTrue(forbidden_calls.isdisjoint(called_names))
        self.assertNotIn("results_private", source)
        self.assertNotIn("KEEPERHUB", source.upper())

    def test_identity_functions_do_not_write_console_output(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            identity = mission_identity.build_mission_identity(
                mission_document()
            )
            mission_identity.classify_mission(None, identity)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
