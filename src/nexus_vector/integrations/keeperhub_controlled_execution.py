"""Action-specific KeeperHub simulation and broadcast gates.

This module is intentionally transport-injected and contains no credential lookup,
network client, wallet access, signing, or automatic retry capability.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from nexus_vector.application.execution_dispatch import ExecutionPortOutcome
from nexus_vector.application.provider_reference_port import ProviderExecutionResult
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptPlan,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    derive_request_fingerprint,
)
from nexus_vector.integrations.keeperhub_direct_execution import (
    KEEPERHUB_PROVIDER_NAMESPACE,
    KeeperHubDirectExecutionPort,
    KeeperHubTransferIntent,
    KeeperHubTransferTransport,
)

_REQUIRED_BROADCAST_FLAG = "--approve-testnet-write"
_BODY_FINGERPRINT_DOMAIN = b"nexus-vector:keeperhub-body:v1\x00"
_MAX_REFERENCE_LENGTH = 256


class KeeperHubControlledExecutionError(RuntimeError):
    """Machine-classifiable gate failure without payload or credential echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise KeeperHubControlledExecutionError(code)


def _required_text(value: Any, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > _MAX_REFERENCE_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(code)
    return value


def _utc(value: Any, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(code)
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        _fail(code)
    if offset != timedelta(0):
        _fail(code)
    return value


def _body_fingerprint(body: Mapping[str, Any]) -> str:
    if not isinstance(body, Mapping):
        _fail("INVALID_TRANSFER_BODY")
    try:
        encoded = json.dumps(
            dict(body),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        _fail("INVALID_TRANSFER_BODY")
    return "khb_" + hashlib.sha256(_BODY_FINGERPRINT_DOMAIN + encoded).hexdigest()


def _validate_plan_intent(
    plan: ExecutionAttemptPlan,
    intent: KeeperHubTransferIntent,
) -> None:
    if not isinstance(plan, ExecutionAttemptPlan):
        _fail("INVALID_ATTEMPT_PLAN")
    if not isinstance(intent, KeeperHubTransferIntent):
        _fail("INVALID_TRANSFER_INTENT")
    if plan.provider_namespace != KEEPERHUB_PROVIDER_NAMESPACE:
        _fail("PROVIDER_NAMESPACE_MISMATCH")
    expected = derive_request_fingerprint(
        plan.provider_namespace,
        plan.request_key,
        intent.request_material,
    )
    if expected != plan.request_fingerprint:
        _fail("REQUEST_FINGERPRINT_MISMATCH")


class _OneShotBudget:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consumed = False

    def consume(self, exhausted_code: str) -> None:
        with self._lock:
            if self._consumed:
                _fail(exhausted_code)
            # Consume before the external call. Ambiguity never restores budget.
            self._consumed = True


class KeeperHubSimulationDecision(str, Enum):
    ELIGIBLE_FOR_BROADCAST_APPROVAL = "ELIGIBLE_FOR_BROADCAST_APPROVAL"
    REJECTED_FINAL = "REJECTED_FINAL"


@dataclass(frozen=True)
class KeeperHubSimulationAuthorization:
    action_sheet_id: str
    approval_reference: str
    attempt_id: str
    request_fingerprint: str
    authorized_at_utc: datetime
    expires_at_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_sheet_id",
            _required_text(self.action_sheet_id, "INVALID_ACTION_SHEET_ID"),
        )
        object.__setattr__(
            self,
            "approval_reference",
            _required_text(self.approval_reference, "INVALID_SIMULATION_APPROVAL"),
        )
        object.__setattr__(
            self,
            "attempt_id",
            _required_text(self.attempt_id, "INVALID_ATTEMPT_ID"),
        )
        object.__setattr__(
            self,
            "request_fingerprint",
            _required_text(
                self.request_fingerprint,
                "INVALID_REQUEST_FINGERPRINT",
            ),
        )
        authorized = _utc(self.authorized_at_utc, "INVALID_AUTHORIZED_AT")
        expires = _utc(self.expires_at_utc, "INVALID_EXPIRES_AT")
        if expires <= authorized:
            _fail("INVALID_AUTHORIZATION_WINDOW")


@dataclass(frozen=True)
class KeeperHubSimulationReceipt:
    action_sheet_id: str
    approval_reference: str
    attempt_id: str
    request_fingerprint: str
    simulation_body_fingerprint: str
    decision: KeeperHubSimulationDecision
    simulated_at_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_sheet_id",
            _required_text(self.action_sheet_id, "INVALID_ACTION_SHEET_ID"),
        )
        object.__setattr__(
            self,
            "approval_reference",
            _required_text(self.approval_reference, "INVALID_SIMULATION_APPROVAL"),
        )
        object.__setattr__(
            self,
            "attempt_id",
            _required_text(self.attempt_id, "INVALID_ATTEMPT_ID"),
        )
        object.__setattr__(
            self,
            "request_fingerprint",
            _required_text(
                self.request_fingerprint,
                "INVALID_REQUEST_FINGERPRINT",
            ),
        )
        fingerprint = _required_text(
            self.simulation_body_fingerprint,
            "INVALID_SIMULATION_BODY_FINGERPRINT",
        )
        if (
            not fingerprint.startswith("khb_")
            or len(fingerprint) != 68
            or any(character not in "0123456789abcdef" for character in fingerprint[4:])
        ):
            _fail("INVALID_SIMULATION_BODY_FINGERPRINT")
        if not isinstance(self.decision, KeeperHubSimulationDecision):
            _fail("INVALID_SIMULATION_DECISION")
        _utc(self.simulated_at_utc, "INVALID_SIMULATED_AT")


@dataclass(frozen=True)
class KeeperHubBroadcastAuthorization:
    action_sheet_id: str
    approval_reference: str
    attempt_id: str
    request_fingerprint: str
    simulation_body_fingerprint: str
    approved_at_utc: datetime
    expires_at_utc: datetime
    runtime_flag: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_sheet_id",
            _required_text(self.action_sheet_id, "INVALID_ACTION_SHEET_ID"),
        )
        object.__setattr__(
            self,
            "approval_reference",
            _required_text(self.approval_reference, "INVALID_BROADCAST_APPROVAL"),
        )
        object.__setattr__(
            self,
            "attempt_id",
            _required_text(self.attempt_id, "INVALID_ATTEMPT_ID"),
        )
        object.__setattr__(
            self,
            "request_fingerprint",
            _required_text(
                self.request_fingerprint,
                "INVALID_REQUEST_FINGERPRINT",
            ),
        )
        fingerprint = _required_text(
            self.simulation_body_fingerprint,
            "INVALID_SIMULATION_BODY_FINGERPRINT",
        )
        if (
            not fingerprint.startswith("khb_")
            or len(fingerprint) != 68
            or any(character not in "0123456789abcdef" for character in fingerprint[4:])
        ):
            _fail("INVALID_SIMULATION_BODY_FINGERPRINT")
        approved = _utc(self.approved_at_utc, "INVALID_APPROVED_AT")
        expires = _utc(self.expires_at_utc, "INVALID_EXPIRES_AT")
        if expires <= approved:
            _fail("INVALID_AUTHORIZATION_WINDOW")
        if self.runtime_flag != _REQUIRED_BROADCAST_FLAG:
            _fail("INVALID_BROADCAST_RUNTIME_FLAG")


class KeeperHubControlledSimulationService:
    """Perform at most one separately authorized simulation POST."""

    def __init__(
        self,
        transport: KeeperHubTransferTransport,
        intent: KeeperHubTransferIntent,
    ) -> None:
        if not callable(getattr(transport, "post_transfer", None)):
            _fail("INVALID_KEEPERHUB_TRANSPORT")
        if not isinstance(intent, KeeperHubTransferIntent):
            _fail("INVALID_TRANSFER_INTENT")
        self._transport = transport
        self._intent = intent
        self._budget = _OneShotBudget()

    def simulate(
        self,
        plan: ExecutionAttemptPlan,
        authorization: KeeperHubSimulationAuthorization,
        simulated_at_utc: datetime,
    ) -> KeeperHubSimulationReceipt:
        _validate_plan_intent(plan, self._intent)
        if not isinstance(authorization, KeeperHubSimulationAuthorization):
            _fail("INVALID_SIMULATION_AUTHORIZATION")
        observed = _utc(simulated_at_utc, "INVALID_SIMULATED_AT")
        if (
            authorization.attempt_id != plan.attempt_id
            or authorization.request_fingerprint != plan.request_fingerprint
        ):
            _fail("SIMULATION_AUTHORIZATION_MISMATCH")
        if not (
            authorization.authorized_at_utc
            <= observed
            <= authorization.expires_at_utc
        ):
            _fail("SIMULATION_AUTHORIZATION_EXPIRED")

        self._budget.consume("SIMULATION_BUDGET_EXHAUSTED")
        try:
            response = self._transport.post_transfer(
                self._intent.simulation_body,
                idempotency_key=None,
            )
            result = KeeperHubDirectExecutionPort._classify_simulation(response)
        except Exception:
            _fail("SIMULATION_OUTCOME_UNKNOWN")

        if result is None:
            decision = (
                KeeperHubSimulationDecision.ELIGIBLE_FOR_BROADCAST_APPROVAL
            )
        elif (
            isinstance(result, ProviderExecutionResult)
            and result.outcome is ExecutionPortOutcome.REJECTED_FINAL
        ):
            decision = KeeperHubSimulationDecision.REJECTED_FINAL
        else:
            _fail("INVALID_SIMULATION_CLASSIFICATION")

        return KeeperHubSimulationReceipt(
            action_sheet_id=authorization.action_sheet_id,
            approval_reference=authorization.approval_reference,
            attempt_id=plan.attempt_id,
            request_fingerprint=plan.request_fingerprint,
            simulation_body_fingerprint=_body_fingerprint(
                self._intent.simulation_body
            ),
            decision=decision,
            simulated_at_utc=observed,
        )


class KeeperHubApprovedBroadcastPort:
    """Broadcast-only provider port bound to one eligible simulation and approval."""

    def __init__(
        self,
        transport: KeeperHubTransferTransport,
        intent: KeeperHubTransferIntent,
        simulation_receipt: KeeperHubSimulationReceipt,
        authorization: KeeperHubBroadcastAuthorization,
    ) -> None:
        if not callable(getattr(transport, "post_transfer", None)):
            _fail("INVALID_KEEPERHUB_TRANSPORT")
        if not isinstance(intent, KeeperHubTransferIntent):
            _fail("INVALID_TRANSFER_INTENT")
        if not isinstance(simulation_receipt, KeeperHubSimulationReceipt):
            _fail("INVALID_SIMULATION_RECEIPT")
        if not isinstance(authorization, KeeperHubBroadcastAuthorization):
            _fail("INVALID_BROADCAST_AUTHORIZATION")
        if (
            simulation_receipt.decision
            is not KeeperHubSimulationDecision.ELIGIBLE_FOR_BROADCAST_APPROVAL
        ):
            _fail("SIMULATION_NOT_ELIGIBLE")
        if (
            authorization.action_sheet_id != simulation_receipt.action_sheet_id
            or authorization.attempt_id != simulation_receipt.attempt_id
            or authorization.request_fingerprint
            != simulation_receipt.request_fingerprint
            or authorization.simulation_body_fingerprint
            != simulation_receipt.simulation_body_fingerprint
        ):
            _fail("BROADCAST_AUTHORIZATION_MISMATCH")
        if authorization.approved_at_utc < simulation_receipt.simulated_at_utc:
            _fail("BROADCAST_APPROVED_BEFORE_SIMULATION")
        if (
            _body_fingerprint(intent.simulation_body)
            != simulation_receipt.simulation_body_fingerprint
        ):
            _fail("SIMULATION_BODY_FINGERPRINT_MISMATCH")

        self._transport = transport
        self._intent = intent
        self._receipt = simulation_receipt
        self._authorization = authorization
        self._budget = _OneShotBudget()

    def execute(self, attempt: ExecutionAttemptRecord) -> ProviderExecutionResult:
        if not isinstance(attempt, ExecutionAttemptRecord):
            _fail("INVALID_EXECUTION_ATTEMPT")
        if attempt.state is not ExecutionAttemptState.IN_FLIGHT:
            _fail("ATTEMPT_NOT_IN_FLIGHT")
        _validate_plan_intent(attempt.plan, self._intent)
        if (
            attempt.attempt_id != self._receipt.attempt_id
            or attempt.plan.request_fingerprint
            != self._receipt.request_fingerprint
        ):
            _fail("BROADCAST_ATTEMPT_MISMATCH")
        if not (
            self._authorization.approved_at_utc
            <= attempt.updated_at_utc
            <= self._authorization.expires_at_utc
        ):
            _fail("BROADCAST_AUTHORIZATION_EXPIRED")

        self._budget.consume("BROADCAST_BUDGET_EXHAUSTED")
        try:
            response = self._transport.post_transfer(
                self._intent.broadcast_body,
                idempotency_key=attempt.plan.request_key,
            )
            result = KeeperHubDirectExecutionPort._classify_broadcast(response)
        except Exception:
            _fail("BROADCAST_OUTCOME_UNKNOWN")
        if not isinstance(result, ProviderExecutionResult):
            _fail("INVALID_BROADCAST_CLASSIFICATION")
        return result
