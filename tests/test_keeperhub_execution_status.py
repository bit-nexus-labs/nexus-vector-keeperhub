from __future__ import annotations

import ast
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nexus_vector.domain.provider_execution_references import (
    ProviderExecutionReference,
)
from nexus_vector.integrations.keeperhub_direct_execution import (
    KEEPERHUB_PROVIDER_NAMESPACE,
    KeeperHubTransportResponse,
)
from nexus_vector.integrations.keeperhub_execution_status import (
    KeeperHubExecutionStatus,
    KeeperHubExecutionStatusError,
    KeeperHubExecutionStatusObserver,
)

ATTEMPT_ID = "att_" + "11" * 32
FINGERPRINT = "xrf_" + "22" * 32
EXECUTION_ID = "direct_123"
TRANSACTION_HASH = "0x" + "33" * 32
TRANSACTION_LINK = f"https://sepolia.basescan.org/tx/{TRANSACTION_HASH}"
T0 = datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)


def reference():
    return ProviderExecutionReference(
        attempt_id=ATTEMPT_ID,
        provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
        request_fingerprint=FINGERPRINT,
        provider_reference=EXECUTION_ID,
        created_at_utc=T0,
    )


class FakeStatusTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_execution_status(self, provider_reference):
        self.calls.append(provider_reference)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def response(status, *, hint, **extra):
    body = {"executionId": EXECUTION_ID, "status": status, **extra}
    return KeeperHubTransportResponse(
        200,
        body,
        {"X-Poll-Interval-Hint": str(hint)},
    )


class KeeperHubExecutionStatusTests(unittest.TestCase):
    def test_pending_and_running_require_positive_poll_hint(self) -> None:
        for status in ("pending", "running"):
            with self.subTest(status=status):
                transport = FakeStatusTransport(response(status, hint=3))
                observed = KeeperHubExecutionStatusObserver(transport).observe(
                    reference()
                )
                self.assertEqual(
                    observed.status,
                    KeeperHubExecutionStatus(status),
                )
                self.assertEqual(observed.poll_after_seconds, 3)
                self.assertFalse(observed.is_terminal)
                self.assertFalse(observed.requires_independent_chain_verification)
                self.assertIsNone(observed.transaction_hash)
                self.assertEqual(transport.calls, [EXECUTION_ID])

    def test_completed_requires_exact_transaction_evidence(self) -> None:
        transport = FakeStatusTransport(
            response(
                "completed",
                hint=0,
                transactionHash=TRANSACTION_HASH.upper().replace("0X", "0x"),
                transactionLink=TRANSACTION_LINK,
            )
        )
        observed = KeeperHubExecutionStatusObserver(transport).observe(reference())
        self.assertEqual(observed.status, KeeperHubExecutionStatus.COMPLETED)
        self.assertTrue(observed.is_terminal)
        self.assertTrue(observed.requires_independent_chain_verification)
        self.assertEqual(observed.transaction_hash, TRANSACTION_HASH)
        self.assertEqual(observed.transaction_link, TRANSACTION_LINK)
        self.assertEqual(observed.poll_after_seconds, 0)

    def test_failed_is_terminal_but_is_not_chain_verification(self) -> None:
        observed = KeeperHubExecutionStatusObserver(
            FakeStatusTransport(
                response("failed", hint=0, error="sanitized-provider-error")
            )
        ).observe(reference())
        self.assertEqual(observed.status, KeeperHubExecutionStatus.FAILED)
        self.assertTrue(observed.is_terminal)
        self.assertFalse(observed.requires_independent_chain_verification)
        self.assertTrue(observed.provider_error_present)
        self.assertIsNone(observed.transaction_hash)
        self.assertNotIn("sanitized-provider-error", repr(observed))

    def test_execution_id_mismatch_fails_closed(self) -> None:
        mismatched = KeeperHubTransportResponse(
            200,
            {"executionId": "direct_other", "status": "pending"},
            {"X-Poll-Interval-Hint": "2"},
        )
        with self.assertRaises(KeeperHubExecutionStatusError) as caught:
            KeeperHubExecutionStatusObserver(
                FakeStatusTransport(mismatched)
            ).observe(reference())
        self.assertEqual(caught.exception.code, "EXECUTION_ID_MISMATCH")

    def test_poll_hint_semantics_are_strict(self) -> None:
        cases = (
            response("pending", hint=0),
            response("completed", hint=2, transactionHash=TRANSACTION_HASH, transactionLink=TRANSACTION_LINK),
            KeeperHubTransportResponse(
                200,
                {"executionId": EXECUTION_ID, "status": "pending"},
                {},
            ),
            KeeperHubTransportResponse(
                200,
                {"executionId": EXECUTION_ID, "status": "pending"},
                {"X-Poll-Interval-Hint": "1", "x-poll-interval-hint": "2"},
            ),
        )
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(KeeperHubExecutionStatusError):
                    KeeperHubExecutionStatusObserver(
                        FakeStatusTransport(item)
                    ).observe(reference())

    def test_completed_missing_or_mismatched_evidence_fails_closed(self) -> None:
        cases = (
            response("completed", hint=0),
            response(
                "completed",
                hint=0,
                transactionHash=TRANSACTION_HASH,
                transactionLink="https://example.invalid/tx/0x" + "44" * 32,
            ),
            response(
                "completed",
                hint=0,
                transactionHash="not-a-hash",
                transactionLink=TRANSACTION_LINK,
            ),
        )
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(KeeperHubExecutionStatusError):
                    KeeperHubExecutionStatusObserver(
                        FakeStatusTransport(item)
                    ).observe(reference())

    def test_active_or_failed_status_cannot_smuggle_transaction_evidence(self) -> None:
        for status in ("pending", "running", "failed"):
            item = response(
                status,
                hint=0 if status == "failed" else 2,
                transactionHash=TRANSACTION_HASH,
                transactionLink=TRANSACTION_LINK,
            )
            with self.subTest(status=status):
                with self.assertRaises(KeeperHubExecutionStatusError):
                    KeeperHubExecutionStatusObserver(
                        FakeStatusTransport(item)
                    ).observe(reference())

    def test_non_success_or_unknown_status_is_unknown(self) -> None:
        cases = (
            KeeperHubTransportResponse(429, {"error": "rate_limited"}, {"Retry-After": "5"}),
            response("queued", hint=2),
        )
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(KeeperHubExecutionStatusError):
                    KeeperHubExecutionStatusObserver(
                        FakeStatusTransport(item)
                    ).observe(reference())

    def test_observer_has_no_direct_network_secret_wallet_or_process_capability(self) -> None:
        module = (
            Path(__file__).parents[1]
            / "src"
            / "nexus_vector"
            / "integrations"
            / "keeperhub_execution_status.py"
        )
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        forbidden = {
            "http", "urllib", "socket", "requests", "subprocess", "os",
            "secrets", "web3", "eth_account", "ccxt",
        }
        self.assertTrue(imported.isdisjoint(forbidden), imported & forbidden)


if __name__ == "__main__":
    unittest.main()
