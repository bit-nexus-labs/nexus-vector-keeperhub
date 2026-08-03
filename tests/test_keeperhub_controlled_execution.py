from __future__ import annotations

import dataclasses
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.application.execution_dispatch import (
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
from nexus_vector.integrations.keeperhub_controlled_execution import (
    KeeperHubApprovedBroadcastPort,
    KeeperHubBroadcastAuthorization,
    KeeperHubControlledExecutionError,
    KeeperHubControlledSimulationService,
    KeeperHubSimulationAuthorization,
    KeeperHubSimulationDecision,
    load_keeperhub_simulation_receipt,
)
from nexus_vector.integrations.keeperhub_direct_execution import (
    KEEPERHUB_PROVIDER_NAMESPACE,
    KeeperHubTransferIntent,
    KeeperHubTransportResponse,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_keeperhub_authorization_ledger import (
    KeeperHubAuthorizationPhase,
    KeeperHubAuthorizationState,
    SQLiteKeeperHubAuthorizationLedger,
)
from nexus_vector.persistence.sqlite_provider_execution_reference_store import (
    SQLiteProviderExecutionReferenceStore,
)

T0 = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
MISSION_KEY = "msn_" + "11" * 32
EFFECT_ID = "eff_" + "22" * 32
REQUEST_KEY = "keeperhub-request-key-controlled-1"
RECIPIENT = "0x" + "33" * 20
TOKEN = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
EXECUTION_ID = "keeperhub-execution-controlled-1"


class ScriptedTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self._lock = threading.Lock()
        self.calls = []

    def post_transfer(self, body, *, idempotency_key):
        with self._lock:
            self.calls.append((dict(body), idempotency_key))
            if not self._responses:
                raise AssertionError("UNEXPECTED_TRANSPORT_CALL")
            response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def make_intent(**changes):
    values = {
        "chain_id": 84532,
        "recipient_address": RECIPIENT,
        "amount_base_units": 12,
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


def make_in_flight(plan=None, updated_at=T0 + timedelta(minutes=3)):
    selected = plan or make_plan()
    initial = create_initial_execution_attempt(selected, T0)
    return transition_execution_attempt(
        initial,
        ExecutionAttemptState.IN_FLIGHT,
        updated_at,
    )


def simulation_ok():
    return KeeperHubTransportResponse(
        200,
        {"success": True, "status": "simulated", "wouldRevert": False},
    )


def simulation_rejected():
    return KeeperHubTransportResponse(
        400,
        {"success": False, "status": "simulated", "wouldRevert": True},
    )


def broadcast_ok():
    return KeeperHubTransportResponse(
        202,
        {"executionId": EXECUTION_ID, "status": "pending"},
    )


def simulation_authorization(plan=None, **changes):
    selected = plan or make_plan()
    values = {
        "action_sheet_id": "action-sheet-20260803-001",
        "approval_reference": "approval-simulation-001",
        "attempt_id": selected.attempt_id,
        "request_fingerprint": selected.request_fingerprint,
        "authorized_at_utc": T0,
        "expires_at_utc": T0 + timedelta(minutes=2),
    }
    values.update(changes)
    return KeeperHubSimulationAuthorization(**values)


def eligible_receipt(ledger, transport=None, intent=None, plan=None):
    selected_intent = intent or make_intent()
    selected_plan = plan or make_plan(selected_intent)
    selected_transport = transport or ScriptedTransport([simulation_ok()])
    service = KeeperHubControlledSimulationService(
        selected_transport,
        selected_intent,
        ledger,
    )
    receipt = service.simulate(
        selected_plan,
        simulation_authorization(selected_plan),
        T0 + timedelta(minutes=1),
    )
    return receipt, selected_transport


def broadcast_authorization(receipt, **changes):
    values = {
        "action_sheet_id": receipt.action_sheet_id,
        "approval_reference": "approval-broadcast-001",
        "attempt_id": receipt.attempt_id,
        "request_fingerprint": receipt.request_fingerprint,
        "simulation_body_fingerprint": receipt.simulation_body_fingerprint,
        "approved_at_utc": T0 + timedelta(minutes=2),
        "expires_at_utc": T0 + timedelta(minutes=5),
        "runtime_flag": "--approve-testnet-write",
    }
    values.update(changes)
    return KeeperHubBroadcastAuthorization(**values)


@dataclass
class FakeEffect:
    effect_id: str = EFFECT_ID
    state: EffectState = EffectState.PLANNED


@dataclass
class FakeRecord:
    state: MissionState = MissionState.READY_FOR_EXECUTION
    effects: tuple[FakeEffect, ...] = field(
        default_factory=lambda: (FakeEffect(),)
    )


@dataclass
class FakeStoredMission:
    record: FakeRecord = field(default_factory=FakeRecord)


class FakeMissionLookup:
    def get(self, mission_key: str):
        return FakeStoredMission() if mission_key == MISSION_KEY else None


class KeeperHubControlledExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.ledger_path = self.root / "authorization-ledger.sqlite3"
        self.ledger = SQLiteKeeperHubAuthorizationLedger(self.ledger_path)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_simulation_requires_exact_authorization_and_durable_one_shot(self):
        intent = make_intent()
        plan = make_plan(intent)
        transport = ScriptedTransport([simulation_ok()])
        service = KeeperHubControlledSimulationService(
            transport,
            intent,
            self.ledger,
        )

        mismatched = dataclasses.replace(
            simulation_authorization(plan),
            request_fingerprint="xrf_" + "00" * 32,
        )
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            service.simulate(plan, mismatched, T0 + timedelta(minutes=1))
        self.assertEqual(
            caught.exception.code,
            "SIMULATION_AUTHORIZATION_MISMATCH",
        )
        self.assertEqual(transport.calls, [])

        receipt = service.simulate(
            plan,
            simulation_authorization(plan),
            T0 + timedelta(minutes=1),
        )
        self.assertEqual(
            receipt.decision,
            KeeperHubSimulationDecision.ELIGIBLE_FOR_BROADCAST_APPROVAL,
        )
        self.assertEqual(len(transport.calls), 1)
        body, key = transport.calls[0]
        self.assertIs(body["simulate"], True)
        self.assertIsNone(key)

        restarted_ledger = SQLiteKeeperHubAuthorizationLedger(
            self.ledger_path
        )
        restored = load_keeperhub_simulation_receipt(
            restarted_ledger,
            receipt.approval_reference,
        )
        self.assertEqual(restored, receipt)

        second_transport = ScriptedTransport([simulation_ok()])
        restarted_service = KeeperHubControlledSimulationService(
            second_transport,
            intent,
            restarted_ledger,
        )
        changed_reference = simulation_authorization(
            plan,
            approval_reference="approval-simulation-002",
        )
        with self.assertRaises(
            KeeperHubControlledExecutionError
        ) as exhausted:
            restarted_service.simulate(
                plan,
                changed_reference,
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(
            exhausted.exception.code,
            "AUTHORIZATION_ALREADY_CONSUMED",
        )
        self.assertEqual(second_transport.calls, [])

    def test_ambiguous_simulation_consumes_budget_across_restart(self):
        plan = make_plan()
        transport = ScriptedTransport([TimeoutError("timeout")])
        service = KeeperHubControlledSimulationService(
            transport,
            make_intent(),
            self.ledger,
        )

        with self.assertRaises(KeeperHubControlledExecutionError) as unknown:
            service.simulate(
                plan,
                simulation_authorization(plan),
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(unknown.exception.code, "SIMULATION_OUTCOME_UNKNOWN")
        durable = self.ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.SIMULATION,
            plan.attempt_id,
        )
        self.assertEqual(
            durable.state,
            KeeperHubAuthorizationState.OUTCOME_UNKNOWN,
        )

        restarted_service = KeeperHubControlledSimulationService(
            ScriptedTransport([simulation_ok()]),
            make_intent(),
            SQLiteKeeperHubAuthorizationLedger(self.ledger_path),
        )
        with self.assertRaises(
            KeeperHubControlledExecutionError
        ) as exhausted:
            restarted_service.simulate(
                plan,
                simulation_authorization(
                    plan,
                    approval_reference="approval-simulation-after-restart",
                ),
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(
            exhausted.exception.code,
            "AUTHORIZATION_ALREADY_CONSUMED",
        )
        self.assertEqual(len(transport.calls), 1)

    def test_concurrent_simulation_services_produce_one_post(self):
        intent = make_intent()
        plan = make_plan(intent)
        transport = ScriptedTransport([simulation_ok()])

        def run(index):
            ledger = SQLiteKeeperHubAuthorizationLedger(self.ledger_path)
            service = KeeperHubControlledSimulationService(
                transport,
                intent,
                ledger,
            )
            try:
                return service.simulate(
                    plan,
                    simulation_authorization(
                        plan,
                        approval_reference=f"approval-simulation-{index}",
                    ),
                    T0 + timedelta(minutes=1),
                )
            except KeeperHubControlledExecutionError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (1, 2)))

        receipts = [
            result
            for result in results
            if not isinstance(result, str)
        ]
        errors = [
            result
            for result in results
            if isinstance(result, str)
        ]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(errors, ["AUTHORIZATION_ALREADY_CONSUMED"])
        self.assertEqual(len(transport.calls), 1)

    def test_rejected_simulation_cannot_create_broadcast_port(self):
        plan = make_plan()
        transport = ScriptedTransport([simulation_rejected()])
        service = KeeperHubControlledSimulationService(
            transport,
            make_intent(),
            self.ledger,
        )
        receipt = service.simulate(
            plan,
            simulation_authorization(plan),
            T0 + timedelta(minutes=1),
        )
        self.assertEqual(
            receipt.decision,
            KeeperHubSimulationDecision.REJECTED_FINAL,
        )

        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            KeeperHubApprovedBroadcastPort(
                ScriptedTransport([broadcast_ok()]),
                make_intent(),
                receipt,
                broadcast_authorization(receipt),
                self.ledger,
            )
        self.assertEqual(caught.exception.code, "SIMULATION_NOT_ELIGIBLE")

    def test_broadcast_requires_exact_runtime_flag(self):
        receipt, _ = eligible_receipt(self.ledger)
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            broadcast_authorization(
                receipt,
                runtime_flag="approve-testnet-write",
            )
        self.assertEqual(
            caught.exception.code,
            "INVALID_BROADCAST_RUNTIME_FLAG",
        )

    def test_broadcast_approval_must_follow_simulation(self):
        receipt, _ = eligible_receipt(self.ledger)
        approval = broadcast_authorization(
            receipt,
            approved_at_utc=receipt.simulated_at_utc - timedelta(seconds=1),
            expires_at_utc=receipt.simulated_at_utc + timedelta(minutes=5),
        )
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            KeeperHubApprovedBroadcastPort(
                ScriptedTransport([broadcast_ok()]),
                make_intent(),
                receipt,
                approval,
                self.ledger,
            )
        self.assertEqual(
            caught.exception.code,
            "BROADCAST_APPROVED_BEFORE_SIMULATION",
        )

    def test_changed_intent_after_simulation_is_blocked(self):
        receipt, _ = eligible_receipt(self.ledger)
        changed = make_intent(amount_base_units=13)
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            KeeperHubApprovedBroadcastPort(
                ScriptedTransport([broadcast_ok()]),
                changed,
                receipt,
                broadcast_authorization(receipt),
                self.ledger,
            )
        self.assertEqual(
            caught.exception.code,
            "SIMULATION_BODY_FINGERPRINT_MISMATCH",
        )

    def test_broadcast_is_one_post_across_restart(self):
        intent = make_intent()
        plan = make_plan(intent)
        receipt, _ = eligible_receipt(
            self.ledger,
            intent=intent,
            plan=plan,
        )
        transport = ScriptedTransport([broadcast_ok()])
        approval = broadcast_authorization(receipt)
        port = KeeperHubApprovedBroadcastPort(
            transport,
            intent,
            receipt,
            approval,
            self.ledger,
        )

        result = port.execute(make_in_flight(plan))
        self.assertEqual(result.outcome, ExecutionPortOutcome.ACCEPTED)
        self.assertEqual(result.provider_reference, EXECUTION_ID)
        self.assertEqual(len(transport.calls), 1)
        body, key = transport.calls[0]
        self.assertNotIn("simulate", body)
        self.assertEqual(body, intent.broadcast_body)
        self.assertEqual(key, REQUEST_KEY)

        restarted_ledger = SQLiteKeeperHubAuthorizationLedger(
            self.ledger_path
        )
        restored = load_keeperhub_simulation_receipt(
            restarted_ledger,
            receipt.approval_reference,
        )
        second_transport = ScriptedTransport([broadcast_ok()])
        second_port = KeeperHubApprovedBroadcastPort(
            second_transport,
            intent,
            restored,
            broadcast_authorization(
                restored,
                approval_reference="approval-broadcast-002",
            ),
            restarted_ledger,
        )
        with self.assertRaises(
            KeeperHubControlledExecutionError
        ) as exhausted:
            second_port.execute(make_in_flight(plan))
        self.assertEqual(
            exhausted.exception.code,
            "AUTHORIZATION_ALREADY_CONSUMED",
        )
        self.assertEqual(second_transport.calls, [])

    def test_ambiguous_broadcast_consumes_budget_across_restart(self):
        intent = make_intent()
        plan = make_plan(intent)
        receipt, _ = eligible_receipt(
            self.ledger,
            intent=intent,
            plan=plan,
        )
        transport = ScriptedTransport([TimeoutError("timeout")])
        port = KeeperHubApprovedBroadcastPort(
            transport,
            intent,
            receipt,
            broadcast_authorization(receipt),
            self.ledger,
        )
        attempt = make_in_flight(plan)

        with self.assertRaises(KeeperHubControlledExecutionError) as unknown:
            port.execute(attempt)
        self.assertEqual(unknown.exception.code, "BROADCAST_OUTCOME_UNKNOWN")
        durable = self.ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.BROADCAST,
            plan.attempt_id,
        )
        self.assertEqual(
            durable.state,
            KeeperHubAuthorizationState.OUTCOME_UNKNOWN,
        )

        restarted_ledger = SQLiteKeeperHubAuthorizationLedger(
            self.ledger_path
        )
        second_transport = ScriptedTransport([broadcast_ok()])
        second_port = KeeperHubApprovedBroadcastPort(
            second_transport,
            intent,
            load_keeperhub_simulation_receipt(
                restarted_ledger,
                receipt.approval_reference,
            ),
            broadcast_authorization(
                receipt,
                approval_reference="approval-broadcast-after-restart",
            ),
            restarted_ledger,
        )
        with self.assertRaises(
            KeeperHubControlledExecutionError
        ) as exhausted:
            second_port.execute(attempt)
        self.assertEqual(
            exhausted.exception.code,
            "AUTHORIZATION_ALREADY_CONSUMED",
        )
        self.assertEqual(second_transport.calls, [])

    def test_expired_broadcast_approval_blocks_before_claim(self):
        intent = make_intent()
        plan = make_plan(intent)
        receipt, _ = eligible_receipt(
            self.ledger,
            intent=intent,
            plan=plan,
        )
        transport = ScriptedTransport([broadcast_ok()])
        port = KeeperHubApprovedBroadcastPort(
            transport,
            intent,
            receipt,
            broadcast_authorization(
                receipt,
                expires_at_utc=T0 + timedelta(minutes=4),
            ),
            self.ledger,
        )
        attempt = make_in_flight(
            plan,
            updated_at=T0 + timedelta(minutes=5),
        )

        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            port.execute(attempt)
        self.assertEqual(
            caught.exception.code,
            "BROADCAST_AUTHORIZATION_EXPIRED",
        )
        self.assertEqual(transport.calls, [])
        self.assertIsNone(
            self.ledger.get_for_attempt(
                KeeperHubAuthorizationPhase.BROADCAST,
                plan.attempt_id,
            )
        )

    def test_full_dispatch_persists_execution_id_after_split_approval(self):
        intent = make_intent()
        plan = make_plan(intent)
        receipt, _ = eligible_receipt(
            self.ledger,
            intent=intent,
            plan=plan,
        )
        broadcast_transport = ScriptedTransport([broadcast_ok()])
        direct = KeeperHubApprovedBroadcastPort(
            broadcast_transport,
            intent,
            receipt,
            broadcast_authorization(receipt),
            self.ledger,
        )
        attempt_store = SQLiteExecutionAttemptStore(
            self.root / "attempts.sqlite3"
        )
        reference_store = SQLiteProviderExecutionReferenceStore(
            self.root / "provider-refs.sqlite3"
        )
        wrapped = ProviderReferencePersistingPort(
            direct,
            reference_store,
            provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
        )

        stored = ExecutionDispatchService(
            FakeMissionLookup(),
            attempt_store,
        ).dispatch(
            plan,
            wrapped,
            T0 + timedelta(minutes=3),
        )
        self.assertEqual(
            stored.record.state,
            ExecutionAttemptState.PROVIDER_ACKNOWLEDGED,
        )
        durable_reference = reference_store.get(plan.attempt_id)
        self.assertEqual(
            durable_reference.provider_reference,
            EXECUTION_ID,
        )
        self.assertEqual(len(broadcast_transport.calls), 1)
        self.assertNotIn("simulate", broadcast_transport.calls[0][0])
        durable_authorization = self.ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.BROADCAST,
            plan.attempt_id,
        )
        self.assertEqual(
            durable_authorization.state,
            KeeperHubAuthorizationState.ACCEPTED,
        )

    def test_receipt_contains_only_bound_sanitized_metadata(self):
        receipt, _ = eligible_receipt(self.ledger)
        fields = {field.name for field in dataclasses.fields(receipt)}
        self.assertEqual(
            fields,
            {
                "action_sheet_id",
                "approval_reference",
                "attempt_id",
                "request_fingerprint",
                "simulation_body_fingerprint",
                "decision",
                "simulated_at_utc",
            },
        )
        rendered = repr(receipt)
        self.assertNotIn(RECIPIENT, rendered)
        self.assertNotIn(TOKEN.casefold(), rendered)
        self.assertNotIn("amount", rendered)


if __name__ == "__main__":
    unittest.main()
