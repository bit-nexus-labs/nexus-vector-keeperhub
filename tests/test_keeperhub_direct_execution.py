from __future__ import annotations

import ast
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from nexus_vector.application.execution_dispatch import (
    ExecutionDispatchError,
    ExecutionDispatchService,
    ExecutionPortOutcome,
)
from nexus_vector.application.provider_reference_port import (
    ProviderReferencePersistingPort,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    build_execution_attempt_plan,
    create_initial_execution_attempt,
    transition_execution_attempt,
)
from nexus_vector.domain.mission_models import EffectState, MissionState
from nexus_vector.integrations.keeperhub_direct_execution import (
    KEEPERHUB_PROVIDER_NAMESPACE,
    KeeperHubDirectExecutionError,
    KeeperHubDirectExecutionPort,
    KeeperHubTransferIntent,
    KeeperHubTransportResponse,
    amount_base_units_to_decimal_string,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_provider_execution_reference_store import (
    SQLiteProviderExecutionReferenceStore,
)

T0 = datetime(2026, 8, 2, 18, 30, tzinfo=timezone.utc)
MISSION_KEY = "msn_" + "11" * 32
EFFECT_ID = "eff_" + "22" * 32
REQUEST_KEY = "keeperhub-request-key-1"
RECIPIENT = "0x" + "33" * 20
TOKEN = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
EXECUTION_ID = "keeperhub-execution-123"


def make_intent(**changes):
    values = {
        "chain_id": 84532,
        "recipient_address": RECIPIENT,
        "amount_base_units": 10,
        "token_address": TOKEN,
        "token_decimals": 6,
        "gas_limit_multiplier": "1.15",
    }
    values.update(changes)
    return KeeperHubTransferIntent(**values)


def make_plan(intent=None):
    selected = intent or make_intent()
    return build_execution_attempt_plan(
        mission_key=MISSION_KEY,
        effect_id=EFFECT_ID,
        provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
        request_key=REQUEST_KEY,
        request_material=selected.request_material,
    )


def make_attempt(intent=None):
    return create_initial_execution_attempt(make_plan(intent), T0)


def make_in_flight_attempt(intent=None):
    return transition_execution_attempt(
        make_attempt(intent),
        ExecutionAttemptState.IN_FLIGHT,
        T0,
    )


@dataclass
class FakeEffect:
    effect_id: str = EFFECT_ID
    state: EffectState = EffectState.PLANNED


@dataclass
class FakeRecord:
    state: MissionState = MissionState.READY_FOR_EXECUTION
    effects: tuple[FakeEffect, ...] = field(default_factory=lambda: (FakeEffect(),))


@dataclass
class FakeStoredMission:
    record: FakeRecord = field(default_factory=FakeRecord)


class FakeMissionLookup:
    def get(self, mission_key: str):
        return FakeStoredMission() if mission_key == MISSION_KEY else None


class ScriptedTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post_transfer(self, body, *, idempotency_key):
        self.calls.append((dict(body), idempotency_key))
        if not self._responses:
            raise AssertionError("UNEXPECTED_TRANSPORT_CALL")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def simulation_ok():
    return KeeperHubTransportResponse(
        200,
        {"success": True, "status": "simulated", "wouldRevert": False},
    )


def broadcast_ok(status="completed"):
    return KeeperHubTransportResponse(
        202,
        {"executionId": EXECUTION_ID, "status": status},
    )


class KeeperHubDirectExecutionTests(unittest.TestCase):
    def test_integer_base_units_convert_exactly_without_float(self) -> None:
        vectors = {
            (1, 6): "0.000001",
            (10, 6): "0.00001",
            (1_000_000, 6): "1",
            (1_230_000, 6): "1.23",
            (42, 0): "42",
        }
        for arguments, expected in vectors.items():
            self.assertEqual(amount_base_units_to_decimal_string(*arguments), expected)
        for arguments in ((0, 6), (1.0, 6), (1, -1), (1, 37)):
            with self.assertRaises(KeeperHubDirectExecutionError):
                amount_base_units_to_decimal_string(*arguments)

    def test_intent_is_strict_canonical_and_testnet_only(self) -> None:
        intent = make_intent(
            recipient_address=RECIPIENT.upper().replace("0X", "0x"),
            token_address=TOKEN.upper().replace("0X", "0x"),
        )
        self.assertEqual(intent.recipient_address, RECIPIENT)
        self.assertEqual(intent.token_address, TOKEN.casefold())
        self.assertEqual(intent.amount_decimal_string, "0.00001")
        self.assertIs(intent.simulation_body["simulate"], True)
        self.assertEqual(
            {key: value for key, value in intent.simulation_body.items() if key != "simulate"},
            intent.broadcast_body,
        )
        with self.assertRaises(KeeperHubDirectExecutionError) as mainnet:
            make_intent(chain_id=1)
        self.assertEqual(mainnet.exception.code, "UNSUPPORTED_TESTNET_CHAIN")
        with self.assertRaises(KeeperHubDirectExecutionError):
            make_intent(gas_limit_multiplier="1.150")

    def test_non_in_flight_attempt_never_reaches_transport(self) -> None:
        transport = ScriptedTransport([simulation_ok(), broadcast_ok()])
        with self.assertRaises(KeeperHubDirectExecutionError) as caught:
            KeeperHubDirectExecutionPort(transport, make_intent()).execute(
                make_attempt()
            )
        self.assertEqual(caught.exception.code, "ATTEMPT_NOT_IN_FLIGHT")
        self.assertEqual(transport.calls, [])

    def test_simulation_then_one_broadcast_uses_exact_request_key(self) -> None:
        intent = make_intent()
        transport = ScriptedTransport([simulation_ok(), broadcast_ok()])
        result = KeeperHubDirectExecutionPort(transport, intent).execute(
            make_in_flight_attempt(intent)
        )
        self.assertEqual(result.outcome, ExecutionPortOutcome.ACCEPTED)
        self.assertEqual(result.provider_reference, EXECUTION_ID)
        self.assertEqual(len(transport.calls), 2)
        simulation_body, simulation_key = transport.calls[0]
        broadcast_body, broadcast_key = transport.calls[1]
        self.assertIsNone(simulation_key)
        self.assertEqual(broadcast_key, REQUEST_KEY)
        self.assertIs(simulation_body.pop("simulate"), True)
        self.assertEqual(simulation_body, broadcast_body)

    def test_all_official_execution_statuses_preserve_reference(self) -> None:
        for status in ("pending", "running", "completed", "failed"):
            with self.subTest(status=status):
                transport = ScriptedTransport([simulation_ok(), broadcast_ok(status)])
                result = KeeperHubDirectExecutionPort(
                    transport,
                    make_intent(),
                ).execute(make_in_flight_attempt())
                self.assertEqual(result.outcome, ExecutionPortOutcome.ACCEPTED)
                self.assertEqual(result.provider_reference, EXECUTION_ID)

    def test_changed_economic_intent_is_blocked_before_transport(self) -> None:
        original = make_intent()
        changed = make_intent(amount_base_units=11)
        transport = ScriptedTransport([simulation_ok(), broadcast_ok()])
        with self.assertRaises(KeeperHubDirectExecutionError) as caught:
            KeeperHubDirectExecutionPort(transport, changed).execute(
                make_in_flight_attempt(original)
            )
        self.assertEqual(caught.exception.code, "REQUEST_FINGERPRINT_MISMATCH")
        self.assertEqual(transport.calls, [])

    def test_simulation_rejection_never_broadcasts(self) -> None:
        for response in (
            KeeperHubTransportResponse(
                200,
                {"success": False, "status": "simulated", "wouldRevert": False},
            ),
            KeeperHubTransportResponse(
                400,
                {"success": False, "status": "simulated", "wouldRevert": True},
            ),
            KeeperHubTransportResponse(422, {"error": "wallet_not_configured"}),
        ):
            transport = ScriptedTransport([response])
            result = KeeperHubDirectExecutionPort(transport, make_intent()).execute(
                make_in_flight_attempt()
            )
            self.assertEqual(result.outcome, ExecutionPortOutcome.REJECTED_FINAL)
            self.assertEqual(len(transport.calls), 1)

    def test_ambiguous_provider_responses_fail_unknown_not_final(self) -> None:
        ambiguous_scripts = (
            [KeeperHubTransportResponse(429, {"error": "rate_limited"})],
            [simulation_ok(), KeeperHubTransportResponse(409, {"error": "in_progress"})],
            [simulation_ok(), KeeperHubTransportResponse(202, {"status": "completed"})],
            [
                KeeperHubTransportResponse(
                    400,
                    {"success": False, "status": "invalid", "wouldRevert": True},
                )
            ],
        )
        for script in ambiguous_scripts:
            with self.subTest(script=script):
                transport = ScriptedTransport(script)
                with self.assertRaises(KeeperHubDirectExecutionError):
                    KeeperHubDirectExecutionPort(transport, make_intent()).execute(
                        make_in_flight_attempt()
                    )

    def test_full_dispatch_persists_execution_id_before_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt_store = SQLiteExecutionAttemptStore(root / "attempts.sqlite3")
            reference_store = SQLiteProviderExecutionReferenceStore(
                root / "provider-refs.sqlite3"
            )
            transport = ScriptedTransport([simulation_ok(), broadcast_ok()])
            direct = KeeperHubDirectExecutionPort(transport, make_intent())
            wrapped = ProviderReferencePersistingPort(
                direct,
                reference_store,
                provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
            )
            result = ExecutionDispatchService(
                FakeMissionLookup(),
                attempt_store,
            ).dispatch(make_plan(), wrapped, T0)
            self.assertEqual(
                result.record.state,
                ExecutionAttemptState.PROVIDER_ACKNOWLEDGED,
            )
            durable = reference_store.get(result.record.attempt_id)
            self.assertEqual(durable.provider_reference, EXECUTION_ID)
            self.assertEqual(durable.request_fingerprint, make_plan().request_fingerprint)

    def test_transport_exception_becomes_durable_unknown_through_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            attempt_store = SQLiteExecutionAttemptStore(
                Path(directory) / "attempts.sqlite3"
            )
            transport = ScriptedTransport([simulation_ok(), TimeoutError("timeout")])
            direct = KeeperHubDirectExecutionPort(transport, make_intent())
            reference_store = SQLiteProviderExecutionReferenceStore(
                Path(directory) / "provider-refs.sqlite3"
            )
            wrapped = ProviderReferencePersistingPort(
                direct,
                reference_store,
                provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
            )
            with self.assertRaises(ExecutionDispatchError) as caught:
                ExecutionDispatchService(
                    FakeMissionLookup(),
                    attempt_store,
                ).dispatch(make_plan(), wrapped, T0)
            self.assertEqual(caught.exception.code, "EXECUTION_OUTCOME_UNKNOWN")
            durable = attempt_store.get(make_plan().attempt_id)
            self.assertEqual(
                durable.record.state,
                ExecutionAttemptState.EXECUTION_UNKNOWN,
            )
            self.assertFalse((Path(directory) / "provider-refs.sqlite3").exists())

    def test_invalid_execution_id_is_unknown_and_never_acknowledged(self) -> None:
        transport = ScriptedTransport(
            [
                simulation_ok(),
                KeeperHubTransportResponse(
                    202,
                    {"executionId": " bad-id ", "status": "completed"},
                ),
            ]
        )
        with self.assertRaises(KeeperHubDirectExecutionError) as caught:
            KeeperHubDirectExecutionPort(transport, make_intent()).execute(
                make_in_flight_attempt()
            )
        self.assertEqual(caught.exception.code, "INVALID_EXECUTION_ID")

    def test_adapter_has_no_direct_network_secret_wallet_or_process_capability(self) -> None:
        module = (
            Path(__file__).parents[1]
            / "src"
            / "nexus_vector"
            / "integrations"
            / "keeperhub_direct_execution.py"
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
