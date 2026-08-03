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
        self.responses = list(responses)
        self.calls = []
        self.lock = threading.Lock()

    def post_transfer(self, body, *, idempotency_key):
        with self.lock:
            self.calls.append((dict(body), idempotency_key))
            if not self.responses:
                raise AssertionError("UNEXPECTED_TRANSPORT_CALL")
            response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def intent(amount=12):
    return KeeperHubTransferIntent(
        chain_id=84532,
        recipient_address=RECIPIENT,
        amount_base_units=amount,
        token_address=TOKEN,
        token_decimals=6,
        gas_limit_multiplier="1.15",
    )


def plan(selected=None):
    selected = selected or intent()
    return build_execution_attempt_plan(
        mission_key=MISSION_KEY,
        effect_id=EFFECT_ID,
        provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
        request_key=REQUEST_KEY,
        request_material=selected.request_material,
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


def simulation_approval(selected_plan, reference="approval-simulation-001"):
    return KeeperHubSimulationAuthorization(
        action_sheet_id="action-sheet-20260803-001",
        approval_reference=reference,
        attempt_id=selected_plan.attempt_id,
        request_fingerprint=selected_plan.request_fingerprint,
        authorized_at_utc=T0,
        expires_at_utc=T0 + timedelta(minutes=2),
    )


def broadcast_approval(receipt, reference="approval-broadcast-001", **changes):
    values = {
        "action_sheet_id": receipt.action_sheet_id,
        "approval_reference": reference,
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
    def get(self, mission_key):
        return FakeStoredMission() if mission_key == MISSION_KEY else None


class KeeperHubControlledExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.attempt_path = self.root / "attempts.sqlite3"
        self.ledger_path = self.root / "authorizations.sqlite3"
        self.attempts = SQLiteExecutionAttemptStore(self.attempt_path)
        self.ledger = SQLiteKeeperHubAuthorizationLedger(self.ledger_path)

    def tearDown(self):
        self.temporary.cleanup()

    def service(self, transport, selected_intent=None, *, restart=False):
        attempts = (
            SQLiteExecutionAttemptStore(self.attempt_path)
            if restart
            else self.attempts
        )
        ledger = (
            SQLiteKeeperHubAuthorizationLedger(self.ledger_path)
            if restart
            else self.ledger
        )
        return KeeperHubControlledSimulationService(
            transport,
            selected_intent or intent(),
            attempts,
            ledger,
        )

    def simulate_eligible(self, selected_intent=None):
        selected_intent = selected_intent or intent()
        selected_plan = plan(selected_intent)
        transport = ScriptedTransport([simulation_ok()])
        receipt = self.service(transport, selected_intent).simulate(
            selected_plan,
            simulation_approval(selected_plan),
            T0 + timedelta(minutes=1),
        )
        return selected_intent, selected_plan, receipt, transport

    def broadcast_port(
        self,
        selected_intent,
        receipt,
        transport,
        *,
        restart=False,
        reference="approval-broadcast-001",
        **changes,
    ):
        ledger = (
            SQLiteKeeperHubAuthorizationLedger(self.ledger_path)
            if restart
            else self.ledger
        )
        durable_receipt = (
            load_keeperhub_simulation_receipt(
                ledger,
                receipt.approval_reference,
            )
            if restart
            else receipt
        )
        return KeeperHubApprovedBroadcastPort(
            transport,
            selected_intent,
            durable_receipt,
            broadcast_approval(
                durable_receipt,
                reference=reference,
                **changes,
            ),
            ledger,
        )

    def test_simulation_creates_durable_prepared_attempt_and_receipt(self):
        selected_intent, selected_plan, receipt, transport = (
            self.simulate_eligible()
        )
        self.assertEqual(
            receipt.decision,
            KeeperHubSimulationDecision.ELIGIBLE_FOR_BROADCAST_APPROVAL,
        )
        durable_attempt = self.attempts.get(selected_plan.attempt_id)
        self.assertEqual(
            durable_attempt.record.state,
            ExecutionAttemptState.PREPARED,
        )
        self.assertEqual(durable_attempt.record.plan, selected_plan)
        restored = load_keeperhub_simulation_receipt(
            SQLiteKeeperHubAuthorizationLedger(self.ledger_path),
            receipt.approval_reference,
        )
        self.assertEqual(restored, receipt)
        self.assertEqual(len(transport.calls), 1)
        self.assertIs(transport.calls[0][0]["simulate"], True)
        self.assertIsNone(transport.calls[0][1])
        self.assertEqual(selected_intent.amount_base_units, 12)

    def test_simulation_is_one_post_per_effect_across_restart(self):
        _, selected_plan, _, _ = self.simulate_eligible()
        second = ScriptedTransport([simulation_ok()])
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            self.service(second, restart=True).simulate(
                selected_plan,
                simulation_approval(
                    selected_plan,
                    "approval-simulation-002",
                ),
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(
            caught.exception.code,
            "AUTHORIZATION_ALREADY_CONSUMED",
        )
        self.assertEqual(second.calls, [])

    def test_ambiguous_simulation_is_durable_unknown(self):
        selected_plan = plan()
        transport = ScriptedTransport([TimeoutError("timeout")])
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            self.service(transport).simulate(
                selected_plan,
                simulation_approval(selected_plan),
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(caught.exception.code, "SIMULATION_OUTCOME_UNKNOWN")
        record = self.ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.SIMULATION,
            selected_plan.attempt_id,
        )
        self.assertEqual(
            record.state,
            KeeperHubAuthorizationState.OUTCOME_UNKNOWN,
        )
        with self.assertRaises(KeeperHubControlledExecutionError) as second:
            self.service(
                ScriptedTransport([simulation_ok()]),
                restart=True,
            ).simulate(
                selected_plan,
                simulation_approval(
                    selected_plan,
                    "approval-simulation-002",
                ),
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(
            second.exception.code,
            "AUTHORIZATION_ALREADY_CONSUMED",
        )

    def test_non_prepared_attempt_blocks_before_transport(self):
        selected_plan = plan()
        self.attempts.initialize()
        prepared = self.attempts.create(
            create_initial_execution_attempt(selected_plan, T0)
        )
        self.attempts.transition(
            prepared.record.attempt_id,
            prepared.revision,
            ExecutionAttemptState.IN_FLIGHT,
            T0 + timedelta(seconds=1),
        )
        transport = ScriptedTransport([simulation_ok()])
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            self.service(transport).simulate(
                selected_plan,
                simulation_approval(selected_plan),
                T0 + timedelta(minutes=1),
            )
        self.assertEqual(caught.exception.code, "RECONCILIATION_REQUIRED")
        self.assertEqual(transport.calls, [])

    def test_concurrent_simulation_has_one_claim_winner(self):
        selected_intent = intent()
        selected_plan = plan(selected_intent)
        transport = ScriptedTransport([simulation_ok()])

        def run(index):
            try:
                return self.service(
                    transport,
                    selected_intent,
                    restart=True,
                ).simulate(
                    selected_plan,
                    simulation_approval(
                        selected_plan,
                        f"approval-simulation-{index}",
                    ),
                    T0 + timedelta(minutes=1),
                )
            except KeeperHubControlledExecutionError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (1, 2)))
        self.assertEqual(
            sum(not isinstance(item, str) for item in results),
            1,
        )
        self.assertIn("AUTHORIZATION_ALREADY_CONSUMED", results)
        self.assertEqual(len(transport.calls), 1)

    def test_rejected_simulation_never_builds_broadcast_port(self):
        selected_plan = plan()
        receipt = self.service(
            ScriptedTransport([simulation_rejected()])
        ).simulate(
            selected_plan,
            simulation_approval(selected_plan),
            T0 + timedelta(minutes=1),
        )
        self.assertEqual(
            receipt.decision,
            KeeperHubSimulationDecision.REJECTED_FINAL,
        )
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            self.broadcast_port(
                intent(),
                receipt,
                ScriptedTransport([broadcast_ok()]),
            )
        self.assertEqual(caught.exception.code, "SIMULATION_NOT_ELIGIBLE")

    def test_broadcast_requires_exact_flag_and_unchanged_intent(self):
        selected_intent, _, receipt, _ = self.simulate_eligible()
        with self.assertRaises(KeeperHubControlledExecutionError) as flag:
            broadcast_approval(
                receipt,
                runtime_flag="approve-testnet-write",
            )
        self.assertEqual(
            flag.exception.code,
            "INVALID_BROADCAST_RUNTIME_FLAG",
        )
        with self.assertRaises(KeeperHubControlledExecutionError) as changed:
            self.broadcast_port(
                intent(amount=13),
                receipt,
                ScriptedTransport([broadcast_ok()]),
            )
        self.assertEqual(
            changed.exception.code,
            "SIMULATION_BODY_FINGERPRINT_MISMATCH",
        )
        self.assertEqual(selected_intent.amount_base_units, 12)

    def test_broadcast_is_one_post_per_effect_across_restart(self):
        selected_intent, selected_plan, receipt, _ = (
            self.simulate_eligible()
        )
        transport = ScriptedTransport([broadcast_ok()])
        result = self.broadcast_port(
            selected_intent,
            receipt,
            transport,
        ).execute(
            self.attempts.transition(
                selected_plan.attempt_id,
                self.attempts.get(selected_plan.attempt_id).revision,
                ExecutionAttemptState.IN_FLIGHT,
                T0 + timedelta(minutes=3),
            ).record
        )
        self.assertEqual(result.outcome, ExecutionPortOutcome.ACCEPTED)
        self.assertEqual(result.provider_reference, EXECUTION_ID)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("simulate", transport.calls[0][0])
        self.assertEqual(transport.calls[0][1], REQUEST_KEY)

        second_transport = ScriptedTransport([broadcast_ok()])
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            self.broadcast_port(
                selected_intent,
                receipt,
                second_transport,
                restart=True,
                reference="approval-broadcast-002",
            ).execute(
                SQLiteExecutionAttemptStore(
                    self.attempt_path
                ).get(selected_plan.attempt_id).record
            )
        self.assertEqual(
            caught.exception.code,
            "AUTHORIZATION_ALREADY_CONSUMED",
        )
        self.assertEqual(second_transport.calls, [])

    def test_ambiguous_broadcast_is_durable_unknown(self):
        selected_intent, selected_plan, receipt, _ = (
            self.simulate_eligible()
        )
        prepared = self.attempts.get(selected_plan.attempt_id)
        in_flight = self.attempts.transition(
            selected_plan.attempt_id,
            prepared.revision,
            ExecutionAttemptState.IN_FLIGHT,
            T0 + timedelta(minutes=3),
        ).record
        transport = ScriptedTransport([TimeoutError("timeout")])
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            self.broadcast_port(
                selected_intent,
                receipt,
                transport,
            ).execute(in_flight)
        self.assertEqual(caught.exception.code, "BROADCAST_OUTCOME_UNKNOWN")
        record = self.ledger.get_for_attempt(
            KeeperHubAuthorizationPhase.BROADCAST,
            selected_plan.attempt_id,
        )
        self.assertEqual(
            record.state,
            KeeperHubAuthorizationState.OUTCOME_UNKNOWN,
        )

    def test_full_dispatch_persists_execution_id_after_split_approval(self):
        selected_intent, selected_plan, receipt, _ = (
            self.simulate_eligible()
        )
        transport = ScriptedTransport([broadcast_ok()])
        direct = self.broadcast_port(
            selected_intent,
            receipt,
            transport,
        )
        references = SQLiteProviderExecutionReferenceStore(
            self.root / "provider-references.sqlite3"
        )
        wrapped = ProviderReferencePersistingPort(
            direct,
            references,
            provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
        )
        stored = ExecutionDispatchService(
            FakeMissionLookup(),
            self.attempts,
        ).dispatch(
            selected_plan,
            wrapped,
            T0 + timedelta(minutes=3),
        )
        self.assertEqual(
            stored.record.state,
            ExecutionAttemptState.PROVIDER_ACKNOWLEDGED,
        )
        self.assertEqual(
            references.get(selected_plan.attempt_id).provider_reference,
            EXECUTION_ID,
        )
        self.assertEqual(
            self.ledger.get_for_attempt(
                KeeperHubAuthorizationPhase.BROADCAST,
                selected_plan.attempt_id,
            ).state,
            KeeperHubAuthorizationState.ACCEPTED,
        )

    def test_expired_broadcast_blocks_before_claim(self):
        selected_intent, selected_plan, receipt, _ = (
            self.simulate_eligible()
        )
        prepared = self.attempts.get(selected_plan.attempt_id)
        in_flight = self.attempts.transition(
            selected_plan.attempt_id,
            prepared.revision,
            ExecutionAttemptState.IN_FLIGHT,
            T0 + timedelta(minutes=5),
        ).record
        transport = ScriptedTransport([broadcast_ok()])
        port = self.broadcast_port(
            selected_intent,
            receipt,
            transport,
            expires_at_utc=T0 + timedelta(minutes=4),
        )
        with self.assertRaises(KeeperHubControlledExecutionError) as caught:
            port.execute(in_flight)
        self.assertEqual(
            caught.exception.code,
            "BROADCAST_AUTHORIZATION_EXPIRED",
        )
        self.assertEqual(transport.calls, [])
        self.assertIsNone(
            self.ledger.get_for_attempt(
                KeeperHubAuthorizationPhase.BROADCAST,
                selected_plan.attempt_id,
            )
        )

    def test_receipt_is_sanitized(self):
        _, _, receipt, _ = self.simulate_eligible()
        self.assertEqual(
            {field.name for field in dataclasses.fields(receipt)},
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
