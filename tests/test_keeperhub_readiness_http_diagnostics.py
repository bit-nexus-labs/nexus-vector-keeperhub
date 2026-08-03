from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from nexus_vector.integrations.keeperhub_http_transport import (
    KeeperHubHttpTransport,
    KeeperHubHttpTransportError,
)

API_KEY = "kh_" + "a" * 32
ROOT = Path(__file__).parents[1]
PROBE_PATH = ROOT / "tools" / "keeperhub_readiness_probe.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_readiness_probe_diagnostics",
    PROBE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Type": "application/json"}
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
        return self.responses.pop(0)


class KeeperHubReadinessHttpDiagnosticsTests(unittest.TestCase):
    def test_wallet_http_failures_are_distinct_and_sanitized(self):
        cases = (
            (401, "unauthorized", "WALLET_READINESS_AUTHENTICATION_REJECTED"),
            (403, "insufficient_scope", "WALLET_READINESS_SCOPE_REJECTED"),
            (404, "not_found", "WALLET_READINESS_ENDPOINT_NOT_FOUND"),
            (429, "rate_limited", "WALLET_READINESS_RATE_LIMITED"),
            (503, "internal_error", "WALLET_READINESS_PROVIDER_UNAVAILABLE"),
            (418, "invalid_input", "WALLET_READINESS_HTTP_REJECTED"),
        )
        for status, provider_code, expected in cases:
            with self.subTest(status=status):
                opener = FakeOpener(
                    [
                        FakeResponse(
                            status,
                            {
                                "error": provider_code,
                                "detail": "private provider detail",
                                "request_id": "private-request-id",
                            },
                        ),
                        FakeResponse(200, {"hasWallet": False}),
                    ]
                )
                transport = KeeperHubHttpTransport(API_KEY, opener=opener)
                with self.assertRaises(KeeperHubHttpTransportError) as caught:
                    transport.get_wallet_readiness()
                error = caught.exception
                self.assertEqual(error.code, expected)
                self.assertEqual(error.http_status, status)
                self.assertEqual(error.provider_error_code, provider_code)
                self.assertEqual(len(opener.calls), 1)
                serialized = json.dumps(PROBE._transport_stop(error), sort_keys=True)
                self.assertNotIn("private provider detail", serialized)
                self.assertNotIn("private-request-id", serialized)
                self.assertNotIn(API_KEY, serialized)

    def test_unsafe_provider_error_code_is_not_exposed(self):
        transport = KeeperHubHttpTransport(
            API_KEY,
            opener=FakeOpener(
                [
                    FakeResponse(
                        401,
                        {"error": "Unauthorized: leaked detail"},
                    )
                ]
            ),
        )
        with self.assertRaises(KeeperHubHttpTransportError) as caught:
            transport.get_wallet_readiness()
        self.assertIsNone(caught.exception.provider_error_code)
        result = PROBE._transport_stop(caught.exception)
        self.assertNotIn("provider_error_code", result)

    def test_chain_and_balance_surfaces_keep_separate_failure_codes(self):
        chain_transport = KeeperHubHttpTransport(
            API_KEY,
            opener=FakeOpener(
                [FakeResponse(403, {"error": "insufficient_scope"})]
            ),
        )
        with self.assertRaises(KeeperHubHttpTransportError) as chain:
            chain_transport.list_chains()
        self.assertEqual(chain.exception.code, "CHAIN_CATALOG_SCOPE_REJECTED")
        self.assertEqual(chain.exception.http_status, 403)

        balance_transport = KeeperHubHttpTransport(
            API_KEY,
            opener=FakeOpener(
                [FakeResponse(404, {"error": "not_found"})]
            ),
        )
        with self.assertRaises(KeeperHubHttpTransportError) as balances:
            balance_transport.get_wallet_balances()
        self.assertEqual(
            balances.exception.code,
            "WALLET_BALANCES_ENDPOINT_NOT_FOUND",
        )
        self.assertEqual(balances.exception.http_status, 404)

    def test_probe_stop_contains_only_bounded_diagnostics(self):
        error = KeeperHubHttpTransportError(
            "WALLET_READINESS_AUTHENTICATION_REJECTED",
            http_status=401,
            provider_error_code="unauthorized",
        )
        self.assertEqual(
            PROBE._transport_stop(error),
            {
                "probe": "KEEPERHUB_READINESS_V1",
                "status": "STOP",
                "reason": "WALLET_READINESS_AUTHENTICATION_REJECTED",
                "retry": "FORBIDDEN",
                "http_status": 401,
                "provider_error_code": "unauthorized",
            },
        )


if __name__ == "__main__":
    unittest.main()
