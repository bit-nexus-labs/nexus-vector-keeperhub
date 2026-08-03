from __future__ import annotations

import json
import unittest
from urllib.error import URLError

from nexus_vector.integrations.keeperhub_direct_execution import (
    KeeperHubTransportResponse,
)
from nexus_vector.integrations.keeperhub_http_transport import (
    KeeperHubHttpTransport,
    KeeperHubHttpTransportError,
)
from nexus_vector.integrations.keeperhub_simulation_runtime import (
    KeeperHubReadOnlyRuntimeClient,
    KeeperHubSimulationOnlyTransport,
    KeeperHubSimulationRuntimeError,
)

API_KEY = "kh_" + "a" * 32
BASE_URL = "https://app.keeperhub.com/api"
WALLET = "0x" + "11" * 20
TOKEN = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


class FakeResponse:
    def __init__(
        self,
        status,
        payload,
        *,
        content_type="application/json; charset=utf-8",
    ):
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Type": content_type}
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


def simulation_body():
    return {
        "amount": "0.01",
        "chainId": 84532,
        "recipientAddress": WALLET,
        "simulate": True,
        "tokenAddress": TOKEN,
    }


def runtime(responses):
    opener = FakeOpener(responses)
    transport = KeeperHubHttpTransport(API_KEY, opener=opener)
    return transport, opener


class KeeperHubSimulationOnlyTransportTests(unittest.TestCase):
    def test_valid_simulation_is_exactly_one_post_without_idempotency(self):
        transport, opener = runtime(
            [
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "status": "simulated",
                        "wouldRevert": False,
                        "gasEstimate": "42000",
                    },
                )
            ]
        )
        boundary = KeeperHubSimulationOnlyTransport(transport)
        response = boundary.post_transfer(
            simulation_body(),
            idempotency_key=None,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, BASE_URL + "/execute/transfer")
        self.assertEqual(request.method, "POST")
        self.assertEqual(timeout, 10.0)
        self.assertIsNone(request.get_header("Idempotency-key"))
        self.assertEqual(json.loads(request.data), simulation_body())
        self.assertNotIn(API_KEY, repr(boundary))

    def test_broadcast_shaped_request_is_blocked_before_http(self):
        cases = (
            ({**simulation_body(), "simulate": False}, None, "SIMULATION_FLAG_REQUIRED"),
            (
                {key: value for key, value in simulation_body().items() if key != "simulate"},
                None,
                "INCOMPLETE_SIMULATION_BODY",
            ),
            (
                {**simulation_body(), "broadcast": True},
                None,
                "UNAPPROVED_SIMULATION_BODY_FIELD",
            ),
            (
                simulation_body(),
                "request-key-forbidden",
                "SIMULATION_IDEMPOTENCY_KEY_FORBIDDEN",
            ),
        )
        for body, idempotency_key, expected in cases:
            with self.subTest(expected=expected):
                transport, opener = runtime([])
                boundary = KeeperHubSimulationOnlyTransport(transport)
                with self.assertRaises(KeeperHubSimulationRuntimeError) as caught:
                    boundary.post_transfer(
                        body,
                        idempotency_key=idempotency_key,
                    )
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(opener.calls, [])

    def test_execution_evidence_in_simulation_response_fails_closed(self):
        cases = (
            {"executionId": "direct_1"},
            {"transactionHash": "0x" + "12" * 32},
            {"result": {"transactionLink": "https://example.invalid/tx"}},
            {"result": [{"signed_transaction": "0xdead"}]},
            {"status": "completed"},
        )
        for extra in cases:
            with self.subTest(extra=extra):
                payload = {
                    "success": True,
                    "status": "simulated",
                    "wouldRevert": False,
                    **extra,
                }
                transport, opener = runtime([FakeResponse(200, payload)])
                boundary = KeeperHubSimulationOnlyTransport(transport)
                with self.assertRaises(KeeperHubSimulationRuntimeError) as caught:
                    boundary.post_transfer(
                        simulation_body(),
                        idempotency_key=None,
                    )
                self.assertIn(
                    caught.exception.code,
                    {
                        "SIMULATION_EXECUTION_EVIDENCE_PRESENT",
                        "SIMULATION_BROADCAST_STATUS_PRESENT",
                    },
                )
                self.assertEqual(len(opener.calls), 1)

    def test_network_ambiguity_is_never_retried(self):
        transport, opener = runtime(
            [URLError("sensitive-runtime-detail")]
        )
        boundary = KeeperHubSimulationOnlyTransport(transport)
        with self.assertRaises(KeeperHubHttpTransportError) as caught:
            boundary.post_transfer(
                simulation_body(),
                idempotency_key=None,
            )
        self.assertEqual(caught.exception.code, "NETWORK_OUTCOME_UNKNOWN")
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertNotIn(API_KEY, str(caught.exception))


class KeeperHubReadOnlyRuntimeClientTests(unittest.TestCase):
    def test_readiness_client_exposes_only_three_exact_get_surfaces(self):
        transport, opener = runtime(
            [
                FakeResponse(
                    200,
                    {
                        "hasWallet": True,
                        "walletAddress": WALLET,
                        "organizationId": "org_123",
                        "isActive": True,
                    },
                ),
                FakeResponse(
                    200,
                    [
                        {
                            "chainId": 84532,
                            "name": "Base Sepolia",
                            "chainType": "evm",
                            "isTestnet": True,
                            "isEnabled": True,
                            "explorerUrl": "https://sepolia.basescan.org",
                        }
                    ],
                ),
                FakeResponse(
                    200,
                    {
                        "balances": [
                            {
                                "chainId": 84532,
                                "symbol": "ETH",
                                "balance": "0.1",
                            }
                        ]
                    },
                ),
            ]
        )
        client = KeeperHubReadOnlyRuntimeClient(transport)
        readiness = client.get_wallet_readiness()
        chains = client.list_chains()
        balances = client.get_wallet_balances()

        self.assertTrue(readiness.ready)
        self.assertEqual(chains[0].chain_id, 84532)
        self.assertEqual(balances["balances"][0]["symbol"], "ETH")
        self.assertEqual(
            [request.full_url for request, _ in opener.calls],
            [
                BASE_URL + "/user/wallet",
                BASE_URL + "/chains",
                BASE_URL + "/user/wallet/balances",
            ],
        )
        self.assertTrue(all(request.method == "GET" for request, _ in opener.calls))
        self.assertTrue(all(request.data is None for request, _ in opener.calls))
        self.assertFalse(hasattr(client, "post_transfer"))
        self.assertFalse(hasattr(client, "get_execution_status"))
        self.assertNotIn(API_KEY, repr(client))

    def test_unknown_balance_status_or_envelope_fails_closed(self):
        cases = (
            (403, {"error": "insufficient_scope"}, "WALLET_BALANCES_SCOPE_REJECTED"),
            (200, "unexpected", "INVALID_WALLET_BALANCES_RESPONSE"),
        )
        for status, payload, expected in cases:
            with self.subTest(status=status):
                transport, opener = runtime([FakeResponse(status, payload)])
                client = KeeperHubReadOnlyRuntimeClient(transport)
                with self.assertRaises(KeeperHubHttpTransportError) as caught:
                    client.get_wallet_balances()
                self.assertEqual(caught.exception.code, expected)
                if status != 200:
                    self.assertEqual(caught.exception.http_status, status)
                self.assertEqual(len(opener.calls), 1)


if __name__ == "__main__":
    unittest.main()
