from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

from nexus_vector.application.execution_dispatch import ExecutionPortOutcome
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    build_execution_attempt_plan,
    create_initial_execution_attempt,
    transition_execution_attempt,
)
from nexus_vector.integrations.keeperhub_controlled_execution import (
    KeeperHubApprovedBroadcastPort,
    KeeperHubBroadcastAuthorization,
    KeeperHubControlledExecutionError,
    KeeperHubControlledSimulationService,
    KeeperHubSimulationAuthorization,
    KeeperHubSimulationDecision,
)
from nexus_vector.integrations.keeperhub_direct_execution import (
    KEEPERHUB_PROVIDER_NAMESPACE,
    KeeperHubTransferIntent,
    KeeperHubTransportResponse,
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
        self.calls = []

    def post_transfer(self, body, *, idempotency_key):
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


def simulation_authorization(plan=None):
    selected = plan or make_plan()
    return KeeperHubSimulationAuthorization(
        action_sheet_id="action-sheet-20260803-001",
        approval_reference="approval-simulation-001",
        attempt_id=selected.attempt_id,
        request_fingerprint=selected.request_fingerprint,
        authorized_at_utc=T0,
        expires_at_utc=T0 + timedelta(minutes=2),
    )


def eligible_receipt(transport=None, intent=None, plan=None):
    selected_intent = intent or make_intent()
    selected_plan = plan or make_plan(selected_intent)
    selected_transport = transport or ScriptedTransport([simulation_ok()])
    service = KeeperHubControlledSimulationService(
        selected_transport,
        selected_intent,
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


class KeeperHubControlledExecutionTests(unittest.TestCase):
    def test_simulation_requires_exact_authorization_and_is_one_shot(self):
        intent = make_intent()
        plan = make_plan(intent)
        transport = ScriptedTransport([simulation_ok()])
        service = KeeperHubControlledSimulationService(transport, intent)

        mismatched = dataclasses.replace(
            simulation_authorization(plan),
            request_fingerprint="xrf_" + "00" * 32,
        )
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            service.simulate(plan, mismatched, T0 + timedelta(minutes=1))
        self.assertEqual(caught.exception.code, "SIMULATION_AUTHORIZATION_MISMATCH")
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

        with self.assertRaises(KeeperHubControlledExecutionError) as exhausted:
            service.simulate(
                plan,
                simulation_authorization(plan),
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(exhausted.exception.code, "SIMULATION_BUDGET_EXHAUSTED")
        self.assertEqual(len(transport.calls), 1)

    def test_ambiguous_simulation_consumes_budget(self):
        plan = make_plan()
        transport = ScriptedTransport([TimeoutError("timeout")])
        service = KeeperHubControlledSimulationService(transport, make_intent())

        with self.assertRaises(KeeperHubControlledExecutionError) as unknown:
            service.simulate(
                plan,
                simulation_authorization(plan),
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(unknown.exception.code, "SIMULATION_OUTCOME_UNKNOWN")
        with self.assertRaises(KeeperHubControlledExecutionError) as exhausted:
            service.simulate(
                plan,
                simulation_authorization(plan),
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(exhausted.exception.code, "SIMULATION_BUDGET_EXHAUSTED")
        self.assertEqual(len(transport.calls), 1)

    def test_rejected_simulation_cannot_create_broadcast_port(self):
        plan = make_plan()
        transport = ScriptedTransport([simulation_rejected()])
        service = KeeperHubControlledSimulationService(transport, make_intent())
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
            )
        self.assertEqual(caught.exception.code, "SIMULATION_NOT_ELIGIBLE")

    def test_broadcast_requires_exact_runtime_flag(self):
        receipt, _ = eligible_receipt()
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            broadcast_authorization(receipt, runtime_flag="approve-testnet-write")
        self.assertEqual(caught.exception.code, "INVALID_BROADCAST_RUNTIME_FLAG")

    def test_broadcast_approval_must_follow_simulation(self):
        receipt, _ = eligible_receipt()
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
            )
        self.assertEqual(caught.exception.code, "BROADCAST_APPROVED_BEFORE_SIMULATION")

    def test_changed_intent_after_simulation_is_blocked(self):
        receipt, _ = eligible_receipt()
        changed = make_intent(amount_base_units=13)
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            KeeperHubApprovedBroadcastPort(
                ScriptedTransport([broadcast_ok()]),
                changed,
                receipt,
                broadcast_authorization(receipt),
            )
        self.assertEqual(
            caught.exception.code,
            "SIMULATION_BODY_FINGERPRINT_MISMATCH",
        )

    def test_broadcast_is_one_post_with_exact_key_and_no_simulation(self):
        intent = make_intent()
        plan = make_plan(intent)
        receipt, _ = eligible_receipt(intent=intent, plan=plan)
        transport = ScriptedTransport([broadcast_ok()])
        port = KeeperHubApprovedBroadcastPort(
            transport,
            intent,
            receipt,
            broadcast_authorization(receipt),
        )

        result = port.execute(make_in_flight(plan))
        self.assertEqual(result.outcome, ExecutionPortOutcome.ACCEPTED)
        self.assertEqual(result.provider_reference, EXECUTION_ID)
        self.assertEqual(len(transport.calls), 1)
        body, key = transport.calls[0]
        self.assertNotIn("simulate", body)
        self.assertEqual(body, intent.broadcast_body)
        self.assertEqual(key, REQUEST_KEY)

        with self.assertRaises(KeeperHubControlledExecutionError) as exhausted:
            port.execute(make_in_flight(plan))
        self.assertEqual(exhausted.exception.code, "BROADCAST_BUDGET_EXHAUSTED")
        self.assertEqual(len(transport.calls), 1)

    def test_ambiguous_broadcast_consumes_budget_and_never_reposts(self):
        intent = make_intent()
        plan = make_plan(intent)
        receipt, _ = eligible_receipt(intent=intent, plan=plan)
        transport = ScriptedTransport([TimeoutError("timeout")])
        port = KeeperHubApprovedBroadcastPort(
            transport,
            intent,
            receipt,
            broadcast_authorization(receipt),
        )
        attempt = make_in_flight(plan)

        with self.assertRaises(KeeperHubControlledExecutionError) as unknown:
            port.execute(attempt)
        self.assertEqual(unknown.exception.code, "BROADCAST_OUTCOME_UNKNOWN")
        with self.assertRaises(KeeperHubControlledExecutionError) as exhausted:
            port.execute(attempt)
        self.assertEqual(exhausted.exception.code, "BROADCAST_BUDGET_EXHAUSTED")
        self.assertEqual(len(transport.calls), 1)

    def test_expired_broadcast_approval_blocks_before_transport(self):
        intent = make_intent()
        plan = make_plan(intent)
        receipt, _ = eligible_receipt(intent=intent, plan=plan)
        transport = ScriptedTransport([broadcast_ok()])
        port = KeeperHubApprovedBroadcastPort(
            transport,
            intent,
            receipt,
            broadcast_authorization(
                receipt,
                expires_at_utc=T0 + timedelta(minutes=4),
            ),
        )
        attempt = make_in_flight(
            plan,
            updated_at=T0 + timedelta(minutes=5),
        )

        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            port.execute(attempt)
        self.assertEqual(caught.exception.code, "BROADCAST_AUTHORIZATION_EXPIRED")
        self.assertEqual(transport.calls, [])

    def test_receipt_contains_only_bound_sanitized_metadata(self):
        receipt, _ = eligible_receipt()
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
