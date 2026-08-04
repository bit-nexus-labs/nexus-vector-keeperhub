from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools" / "keeperhub_key_identity_probe.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_key_identity_probe_hardening",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

API_KEY = "kh_abcde" + "1" * 27
REQUEST_ID = "nv-key-identity-hardening-0001"


class FakeResponse:
    def __init__(self, status, payload, *, headers=None):
        self.status = status
        self._raw = json.dumps(payload).encode()
        self.headers = {
            "Content-Type": "application/json",
            **(headers or {}),
        }

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self._raw if limit < 0 else self._raw[:limit]

    def close(self):
        pass


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        return self.response


class KeeperHubKeyIdentityProbeHardeningTests(unittest.TestCase):
    def test_short_or_malformed_returned_prefix_fails_after_one_get(self):
        for prefix in ("kh_", "kh_a", " kh_abc", "kh_bad prefix"):
            with self.subTest(prefix=prefix):
                opener = FakeOpener(
                    FakeResponse(
                        200,
                        [
                            {
                                "keyPrefix": prefix,
                                "createdAt": "2026-07-27T20:16:17Z",
                                "lastUsedAt": None,
                                "expiresAt": None,
                            }
                        ],
                        headers={"X-Request-ID": REQUEST_ID},
                    )
                )

                with self.assertRaises(PROBE.KeyIdentityProbeError) as caught:
                    PROBE.run_probe(
                        API_KEY,
                        opener=opener,
                        request_id=REQUEST_ID,
                    )

                self.assertEqual(caught.exception.code, "INVALID_KEY_PREFIX")
                self.assertTrue(caught.exception.request_performed)
                self.assertEqual(len(opener.calls), 1)

    def test_mismatched_request_id_stops_without_trusting_payload(self):
        opener = FakeOpener(
            FakeResponse(
                200,
                [
                    {
                        "keyPrefix": "kh_abcde",
                        "createdAt": "2026-07-27T20:16:17Z",
                        "lastUsedAt": None,
                        "expiresAt": None,
                    }
                ],
                headers={"X-Request-ID": "different-request-id"},
            )
        )

        exit_code, result = PROBE.run_probe(
            API_KEY,
            opener=opener,
            request_id=REQUEST_ID,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(result["reason"], "REQUEST_ID_REFLECTION_INVALID")
        self.assertEqual(result["request_id_reflection"], "MISMATCH")
        self.assertEqual(result["get_requests"], 1)
        self.assertEqual(len(opener.calls), 1)

    def test_post_get_shape_failure_reports_consumed_get_budget(self):
        opener = FakeOpener(
            FakeResponse(
                200,
                {"data": []},
                headers={"X-Request-ID": REQUEST_ID},
            )
        )

        with self.assertRaises(PROBE.KeyIdentityProbeError) as caught:
            PROBE.run_probe(
                API_KEY,
                opener=opener,
                request_id=REQUEST_ID,
            )

        result = PROBE._error_result(caught.exception, REQUEST_ID)
        self.assertTrue(caught.exception.request_performed)
        self.assertEqual(result["get_requests"], 1)
        self.assertEqual(result["support_request_id"], REQUEST_ID)
        self.assertEqual(result["retry"], "REVIEW_BEFORE_REPEAT")
        self.assertEqual(len(opener.calls), 1)

    def test_arbitrary_key_name_and_identifiers_are_not_serialized(self):
        private_name = "private@example.test production treasury"
        opener = FakeOpener(
            FakeResponse(
                200,
                [
                    {
                        "id": "key_private_identifier",
                        "name": private_name,
                        "keyPrefix": "kh_abcde",
                        "createdAt": "2026-07-27T20:16:17Z",
                        "lastUsedAt": None,
                        "createdByName": "private creator",
                        "expiresAt": None,
                    }
                ],
                headers={"X-Request-ID": REQUEST_ID},
            )
        )

        exit_code, result = PROBE.run_probe(
            API_KEY,
            opener=opener,
            request_id=REQUEST_ID,
        )

        self.assertEqual(exit_code, 0)
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(private_name, serialized)
        self.assertNotIn("key_private_identifier", serialized)
        self.assertNotIn("private creator", serialized)


if __name__ == "__main__":
    unittest.main()
