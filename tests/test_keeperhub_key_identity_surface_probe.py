from __future__ import annotations

import importlib.util
import json
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools" / "keeperhub_key_identity_surface_probe.py"
SPEC = importlib.util.spec_from_file_location("surface_probe", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)

API_KEY = "kh_abcde" + "1" * 27
PREFIX = "kh_abcde"
REQUEST_ID = "nv-key-surface-test-0001"


class Response:
    def __init__(
        self,
        status,
        body,
        *,
        content_type="application/json",
        headers=None,
    ):
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.closed = False

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]

    def close(self):
        self.closed = True


class Opener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class SurfaceProbeTests(unittest.TestCase):
    def test_paginated_success_matches_new_key(self):
        opener = Opener(
            Response(
                200,
                {
                    "items": [{"keyPrefix": PREFIX}],
                    "meta": {"total": 1},
                    "_links": {},
                },
                headers={"X-Request-ID": REQUEST_ID},
            )
        )

        exit_code, result = PROBE.run(
            API_KEY,
            opener=opener,
            request_id=REQUEST_ID,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["organization_key_match"], "MATCH")
        self.assertEqual(result["response_surface"], "APPLICATION_JSON")
        self.assertEqual(result["request_id_reflection"], "MATCH")
        self.assertEqual(result["get_requests"], 1)
        self.assertEqual(result["post_requests"], 0)
        self.assertEqual(len(opener.calls), 1)

        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, PROBE.URL)
        self.assertEqual(request.method, "GET")
        self.assertIsNone(request.data)
        self.assertEqual(timeout, 10.0)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {API_KEY}")

        serialized = json.dumps(result)
        self.assertNotIn(API_KEY, serialized)
        self.assertNotIn(PREFIX, serialized)

    def test_cloudflare_html_is_identified_without_echo(self):
        body = b"<html><title>Just a moment...</title>private challenge</html>"
        error = HTTPError(
            PROBE.URL,
            403,
            "Forbidden",
            {
                "Content-Type": "text/html",
                "Server": "cloudflare",
                "CF-Ray": "private-ray",
            },
            BytesIO(body),
        )
        opener = Opener(error)

        exit_code, result = PROBE.run(
            API_KEY,
            opener=opener,
            request_id=REQUEST_ID,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["response_surface"], "CLOUDFLARE_HTML")
        self.assertEqual(result["reason"], "CLOUDFLARE_EDGE_HTTP_REJECTED")
        self.assertEqual(result["get_requests"], 1)
        serialized = json.dumps(result)
        self.assertNotIn("private", serialized)
        self.assertNotIn(API_KEY, serialized)

    def test_json_403_only_exposes_allowlisted_code(self):
        error = HTTPError(
            PROBE.URL,
            403,
            "Forbidden",
            {"Content-Type": "application/json"},
            BytesIO(
                json.dumps(
                    {
                        "error": "insufficient_scope",
                        "detail": "private backend detail",
                    }
                ).encode()
            ),
        )
        opener = Opener(error)

        exit_code, result = PROBE.run(
            API_KEY,
            opener=opener,
            request_id=REQUEST_ID,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["response_surface"], "APPLICATION_JSON")
        self.assertEqual(result["provider_error_code"], "insufficient_scope")
        self.assertNotIn("private", json.dumps(result))

    def test_empty_html_and_other_are_fixed_labels_only(self):
        cases = [
            (Response(403, b"", content_type="application/json"), "EMPTY_RESPONSE"),
            (
                Response(403, b"<html>private app error</html>", content_type="text/html"),
                "HTML_RESPONSE",
            ),
            (
                Response(
                    403,
                    b"private binary",
                    content_type="application/octet-stream",
                ),
                "OTHER_CONTENT",
            ),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                opener = Opener(response)
                exit_code, result = PROBE.run(
                    API_KEY,
                    opener=opener,
                    request_id=REQUEST_ID,
                )
                self.assertEqual(exit_code, 2)
                self.assertEqual(result["response_surface"], expected)
                self.assertNotIn("private", json.dumps(result))
                self.assertEqual(len(opener.calls), 1)

    def test_decode_failure_after_open_counts_the_get(self):
        response = Response(200, {"items": []})
        response.headers = object()
        opener = Opener(response)

        with self.assertRaises(PROBE.ProbeError) as caught:
            PROBE.run(
                API_KEY,
                opener=opener,
                request_id=REQUEST_ID,
            )

        self.assertEqual(caught.exception.code, "INVALID_HTTP_RESPONSE")
        self.assertTrue(caught.exception.request_performed)
        self.assertEqual(len(opener.calls), 1)

    def test_source_has_only_get_capability(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('method="GET"', source)
        self.assertIn("https://app.keeperhub.com/api/keys", source)
        self.assertNotIn('method="POST"', source)
        self.assertNotIn("/execute/transfer", source)
        self.assertNotIn("Idempotency-Key", source)
        self.assertNotIn("--approve-testnet-write", source)


if __name__ == "__main__":
    unittest.main()
