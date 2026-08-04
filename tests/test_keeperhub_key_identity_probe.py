from __future__ import annotations

import importlib.util
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools" / "keeperhub_key_identity_probe.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_key_identity_probe",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

API_KEY = "kh_abcde" + "1" * 27
KEY_PREFIX = "kh_abcde"
REQUEST_ID = "nv-key-identity-test-0001"
BASE_URL = "https://app.keeperhub.com/api"


class FakeResponse:
    def __init__(
        self,
        status,
        payload,
        *,
        content_type="application/json; charset=utf-8",
        headers=None,
    ):
        self.status = status
        self._raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.closed = False

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self._raw if limit < 0 else self._raw[:limit]

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        if not self.responses:
            raise AssertionError("UNEXPECTED_HTTP_CALL")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class KeeperHubKeyIdentityProbeTests(unittest.TestCase):
    def test_pass_is_exactly_one_get_and_never_exposes_key_or_prefix(self):
        payload = [
            {
                "id": "key_private_id",
                "name": "Nexus Wallet Readiness Probe",
                "keyPrefix": KEY_PREFIX,
                "createdAt": "2026-07-27T20:16:17Z",
                "lastUsedAt": "2026-07-28T10:00:00Z",
                "createdByName": "private creator",
                "expiresAt": None,
            }
        ]
        opener = FakeOpener(
            [
                FakeResponse(
                    200,
                    payload,
                    headers={"X-Request-ID": REQUEST_ID},
                )
            ]
        )

        exit_code, result = PROBE.run_probe(
            API_KEY,
            opener=opener,
            request_id=REQUEST_ID,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["organization_key_match"], "MATCH")
        self.assertEqual(result["request_id_reflection"], "MATCH")
        self.assertEqual(result["get_requests"], 1)
        self.assertEqual(result["post_requests"], 0)
        self.assertEqual(result["simulation_posts"], 0)
        self.assertEqual(result["broadcast_posts"], 0)
        self.assertFalse(result["funds_moved"])
        self.assertEqual(len(opener.calls), 1)

        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, BASE_URL + "/keys")
        self.assertEqual(request.method, "GET")
        self.assertIsNone(request.data)
        self.assertEqual(timeout, 10.0)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {API_KEY}")
        self.assertEqual(request.get_header("X-request-id"), REQUEST_ID)

        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn(API_KEY, serialized)
        self.assertNotIn(KEY_PREFIX, serialized)
        self.assertNotIn("key_private_id", serialized)
        self.assertNotIn("private creator", serialized)

    def test_key_not_visible_is_terminal_for_this_diagnostic_result(self):
        opener = FakeOpener(
            [
                FakeResponse(
                    200,
                    [
                        {
                            "name": "Other key",
                            "keyPrefix": "kh_other",
                            "createdAt": "2026-07-20T00:00:00Z",
                            "lastUsedAt": None,
                            "expiresAt": None,
                        }
                    ],
                )
            ]
        )

        exit_code, result = PROBE.run_probe(
            API_KEY,
            opener=opener,
            request_id=REQUEST_ID,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(result["organization_key_match"], "MISMATCH")
        self.assertEqual(result["get_requests"], 1)
        self.assertEqual(len(opener.calls), 1)

    def test_http_403_exposes_only_allowlisted_code_and_support_id(self):
        body = json.dumps(
            {
                "error": "insufficient_scope",
                "detail": "private backend detail",
                "request_id": REQUEST_ID,
            }
        ).encode()
        error = HTTPError(
            BASE_URL + "/keys",
            403,
            "Forbidden",
            {
                "Content-Type": "application/json",
                "X-Request-ID": REQUEST_ID,
            },
            BytesIO(body),
        )
        opener = FakeOpener([error])

        exit_code, result = PROBE.run_probe(
            API_KEY,
            opener=opener,
            request_id=REQUEST_ID,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["provider_error_code"], "insufficient_scope")
        self.assertEqual(result["support_request_id"], REQUEST_ID)
        self.assertEqual(result["get_requests"], 1)
        self.assertEqual(len(opener.calls), 1)
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("private backend detail", serialized)
        self.assertNotIn(API_KEY, serialized)

    def test_unknown_provider_error_is_suppressed(self):
        opener = FakeOpener(
            [
                FakeResponse(
                    403,
                    {
                        "error": "private_internal_reason",
                        "detail": "secret detail",
                    },
                )
            ]
        )

        exit_code, result = PROBE.run_probe(
            API_KEY,
            opener=opener,
            request_id=REQUEST_ID,
        )

        self.assertEqual(exit_code, 2)
        self.assertNotIn("provider_error_code", result)
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("private_internal_reason", serialized)
        self.assertNotIn("secret detail", serialized)

    def test_network_ambiguity_is_one_get_and_never_retried(self):
        opener = FakeOpener([URLError("private network detail")])

        with self.assertRaises(PROBE.KeyIdentityProbeError) as caught:
            PROBE.run_probe(
                API_KEY,
                opener=opener,
                request_id=REQUEST_ID,
            )

        self.assertEqual(caught.exception.code, "NETWORK_OUTCOME_UNKNOWN")
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn(API_KEY, str(caught.exception))

    def test_missing_or_invalid_local_key_stops_before_network(self):
        for environment in ({}, {"KEEPERHUB_API_KEY": "not-a-key"}):
            with self.subTest(environment=environment):
                output = io.StringIO()
                with patch.dict(os.environ, environment, clear=True):
                    with redirect_stdout(output):
                        exit_code = PROBE.main()
                    self.assertNotIn("KEEPERHUB_API_KEY", os.environ)
                payload = json.loads(output.getvalue())
                self.assertEqual(exit_code, 2)
                self.assertEqual(payload["status"], "STOP")
                self.assertEqual(payload["get_requests"], 0)
                self.assertNotIn("not-a-key", output.getvalue())

    def test_invalid_response_shape_fails_closed_without_echo(self):
        opener = FakeOpener([FakeResponse(200, {"data": [{"keyPrefix": KEY_PREFIX}]})])

        with self.assertRaises(PROBE.KeyIdentityProbeError) as caught:
            PROBE.run_probe(
                API_KEY,
                opener=opener,
                request_id=REQUEST_ID,
            )

        self.assertEqual(caught.exception.code, "INVALID_KEYS_RESPONSE")
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn(API_KEY, str(caught.exception))

    def test_source_has_no_funds_moving_capability(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("/execute/transfer", source)
        self.assertNotIn("Idempotency-Key", source)
        self.assertNotIn("simulate\": True", source)
        self.assertNotIn("KeeperHubDirectExecutionPort", source)
        self.assertNotIn("KeeperHubSimulationOnlyTransport", source)
        self.assertNotIn("--approve-testnet-write", source)
        self.assertIn('method="GET"', source)
        self.assertIn('_PATH = "/keys"', source)


if __name__ == "__main__":
    unittest.main()
