from __future__ import annotations

import ast
import json
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

from nexus_vector.integrations.keeperhub_http_transport import (
    KeeperHubHttpTransport,
    KeeperHubHttpTransportError,
)

API_KEY = "kh_" + "a" * 32
BASE_URL = "https://app.keeperhub.com/api"
WALLET = "0x" + "11" * 20


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


def json_response(status, payload, *, headers=None):
    return FakeResponse(status, payload, headers=headers)


class KeeperHubHttpTransportTests(unittest.TestCase):
    def test_constructor_rejects_unapproved_or_leaky_configuration(self) -> None:
        for key in ("", "wrong", " kh_value", "kh_bad\n"):
            with self.assertRaises(KeeperHubHttpTransportError):
                KeeperHubHttpTransport(key, opener=FakeOpener([]))
        with self.assertRaises(KeeperHubHttpTransportError) as base:
            KeeperHubHttpTransport(
                API_KEY,
                base_url="https://example.invalid/api",
                opener=FakeOpener([]),
            )
        self.assertEqual(base.exception.code, "UNAPPROVED_BASE_URL")
        transport = KeeperHubHttpTransport(API_KEY, opener=FakeOpener([]))
        self.assertNotIn(API_KEY, repr(transport))

    def test_simulation_and_broadcast_headers_and_bodies_are_exact(self) -> None:
        opener = FakeOpener(
            [
                json_response(200, {"success": True}),
                json_response(202, {"executionId": "direct_1", "status": "completed"}),
            ]
        )
        transport = KeeperHubHttpTransport(API_KEY, opener=opener)
        simulation = {
            "chainId": 84532,
            "recipientAddress": WALLET,
            "amount": "0.00001",
            "tokenAddress": "0x" + "22" * 20,
            "simulate": True,
        }
        broadcast = dict(simulation)
        broadcast.pop("simulate")
        transport.post_transfer(simulation, idempotency_key=None)
        transport.post_transfer(broadcast, idempotency_key="request-key-1")

        first, second = opener.calls
        simulation_request, simulation_timeout = first
        broadcast_request, broadcast_timeout = second
        self.assertEqual(simulation_request.full_url, BASE_URL + "/execute/transfer")
        self.assertEqual(simulation_request.method, "POST")
        self.assertEqual(simulation_timeout, 10.0)
        self.assertEqual(broadcast_timeout, 10.0)
        self.assertEqual(
            simulation_request.get_header("Authorization"),
            f"Bearer {API_KEY}",
        )
        self.assertIsNone(simulation_request.get_header("Idempotency-key"))
        self.assertEqual(
            broadcast_request.get_header("Idempotency-key"),
            "request-key-1",
        )
        self.assertEqual(json.loads(simulation_request.data), simulation)
        self.assertEqual(json.loads(broadcast_request.data), broadcast)

    def test_execution_status_path_is_percent_encoded(self) -> None:
        opener = FakeOpener(
            [
                json_response(
                    200,
                    {"executionId": "direct/123", "status": "pending"},
                    headers={"X-Poll-Interval-Hint": "2"},
                )
            ]
        )
        transport = KeeperHubHttpTransport(API_KEY, opener=opener)
        response = transport.get_execution_status("direct/123")
        self.assertEqual(response.status_code, 200)
        request, _ = opener.calls[0]
        self.assertEqual(
            request.full_url,
            BASE_URL + "/execute/direct%2F123/status",
        )
        self.assertEqual(request.method, "GET")

    def test_wallet_readiness_true_false_and_inactive(self) -> None:
        payloads = (
            (
                {
                    "hasWallet": True,
                    "walletAddress": WALLET.upper().replace("0X", "0x"),
                    "organizationId": "org_123",
                    "isActive": True,
                },
                True,
            ),
            ({"hasWallet": False, "message": "No wallet"}, False),
            (
                {
                    "hasWallet": True,
                    "walletAddress": WALLET,
                    "organizationId": "org_123",
                    "isActive": False,
                },
                False,
            ),
        )
        for payload, expected_ready in payloads:
            with self.subTest(payload=payload):
                transport = KeeperHubHttpTransport(
                    API_KEY,
                    opener=FakeOpener([json_response(200, payload)]),
                )
                readiness = transport.get_wallet_readiness()
                self.assertEqual(readiness.ready, expected_ready)
                if payload["hasWallet"]:
                    self.assertEqual(readiness.wallet_address, WALLET)
                    self.assertEqual(readiness.organization_id, "org_123")
                else:
                    self.assertIsNone(readiness.wallet_address)

    def test_chain_catalog_is_bare_array_and_testnet_eligibility_is_explicit(self) -> None:
        payload = [
            {
                "chainId": 84532,
                "name": "Base Sepolia",
                "chainType": "evm",
                "isTestnet": True,
                "isEnabled": True,
                "explorerUrl": "https://sepolia.basescan.org",
            },
            {
                "chainId": 1,
                "name": "Ethereum Mainnet",
                "chainType": "evm",
                "isTestnet": False,
                "isEnabled": True,
                "explorerUrl": "https://etherscan.io",
            },
        ]
        transport = KeeperHubHttpTransport(
            API_KEY,
            opener=FakeOpener([json_response(200, payload)]),
        )
        chains = transport.list_chains()
        self.assertEqual(tuple(item.chain_id for item in chains), (84532, 1))
        self.assertTrue(chains[0].eligible_for_testnet_execution)
        self.assertFalse(chains[1].eligible_for_testnet_execution)

    def test_duplicate_or_malformed_chain_catalog_fails_closed(self) -> None:
        cases = (
            [
                {"chainId": 84532, "name": "A", "chainType": "evm", "isTestnet": True, "isEnabled": True, "explorerUrl": None},
                {"chainId": 84532, "name": "B", "chainType": "evm", "isTestnet": True, "isEnabled": True, "explorerUrl": None},
            ],
            {"data": []},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                transport = KeeperHubHttpTransport(
                    API_KEY,
                    opener=FakeOpener([json_response(200, payload)]),
                )
                with self.assertRaises(KeeperHubHttpTransportError):
                    transport.list_chains()

    def test_http_error_is_returned_once_for_fail_closed_mapping(self) -> None:
        body = json.dumps({"error": "rate_limited"}).encode()
        error = HTTPError(
            BASE_URL + "/execute/transfer",
            429,
            "Too Many Requests",
            {"Content-Type": "application/json", "Retry-After": "5"},
            BytesIO(body),
        )
        opener = FakeOpener([error])
        response = KeeperHubHttpTransport(API_KEY, opener=opener).post_transfer(
            {"chainId": 84532},
            idempotency_key="request-key-1",
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.body["error"], "rate_limited")
        self.assertEqual(len(opener.calls), 1)

    def test_network_failure_is_sanitized_and_never_retried(self) -> None:
        opener = FakeOpener([URLError("contains-sensitive-network-detail")])
        transport = KeeperHubHttpTransport(API_KEY, opener=opener)
        with self.assertRaises(KeeperHubHttpTransportError) as caught:
            transport.post_transfer(
                {"chainId": 84532},
                idempotency_key="request-key-1",
            )
        self.assertEqual(caught.exception.code, "NETWORK_OUTCOME_UNKNOWN")
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertNotIn(API_KEY, str(caught.exception))
        self.assertEqual(len(opener.calls), 1)

    def test_invalid_content_type_json_or_size_fails_closed(self) -> None:
        cases = (
            FakeResponse(200, {}, content_type="text/html"),
            FakeResponse(200, b"not-json"),
            FakeResponse(200, b"{" + b" " * 1_048_576 + b"}"),
        )
        for item in cases:
            with self.subTest(item=item):
                transport = KeeperHubHttpTransport(
                    API_KEY,
                    opener=FakeOpener([item]),
                )
                with self.assertRaises(KeeperHubHttpTransportError):
                    transport.get_wallet_readiness()

    def test_module_never_reads_environment_or_spawns_processes(self) -> None:
        module = (
            Path(__file__).parents[1]
            / "src"
            / "nexus_vector"
            / "integrations"
            / "keeperhub_http_transport.py"
        )
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertTrue(
            imported.isdisjoint({"os", "subprocess", "dotenv", "keyring"}),
            imported,
        )


if __name__ == "__main__":
    unittest.main()
