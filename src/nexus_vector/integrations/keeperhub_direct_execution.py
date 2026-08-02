"""Strict KeeperHub Direct Execution mapper over an injected transport."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from nexus_vector.application.execution_dispatch import ExecutionPortOutcome
from nexus_vector.application.provider_reference_port import ProviderExecutionResult
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    derive_request_fingerprint,
)

KEEPERHUB_PROVIDER_NAMESPACE = "keeperhub.direct.v1"
_BASE_SEPOLIA_CHAIN_ID = 84532
_ETHEREUM_SEPOLIA_CHAIN_ID = 11155111
_ALLOWED_TESTNET_CHAIN_IDS = frozenset(
    {_BASE_SEPOLIA_CHAIN_ID, _ETHEREUM_SEPOLIA_CHAIN_ID}
)
_OFFICIAL_EXECUTION_STATUSES = frozenset(
    {"pending", "running", "completed", "failed"}
)
_EVM_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
_MAX_PROVIDER_REFERENCE_LENGTH = 256


class KeeperHubDirectExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise KeeperHubDirectExecutionError(code)


def _canonical_address(value: Any, code: str) -> str:
    if not isinstance(value, str) or _EVM_ADDRESS_PATTERN.fullmatch(value) is None:
        _fail(code)
    return value.casefold()


def _canonical_decimal_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        _fail(code)
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        _fail(code)
    if not decimal.is_finite() or decimal <= 0:
        _fail(code)
    canonical = format(decimal, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if canonical != value:
        _fail(code)
    return value


def amount_base_units_to_decimal_string(
    amount_base_units: int,
    token_decimals: int,
) -> str:
    if type(amount_base_units) is not int or amount_base_units < 1:
        _fail("INVALID_AMOUNT_BASE_UNITS")
    if type(token_decimals) is not int or not 0 <= token_decimals <= 36:
        _fail("INVALID_TOKEN_DECIMALS")
    if token_decimals == 0:
        return str(amount_base_units)
    divisor = 10**token_decimals
    whole, remainder = divmod(amount_base_units, divisor)
    if remainder == 0:
        return str(whole)
    fraction = f"{remainder:0{token_decimals}d}".rstrip("0")
    return f"{whole}.{fraction}"


@dataclass(frozen=True)
class KeeperHubTransferIntent:
    chain_id: int
    recipient_address: str
    amount_base_units: int
    token_address: str
    token_decimals: int
    gas_limit_multiplier: str | None = None

    def __post_init__(self) -> None:
        if type(self.chain_id) is not int or self.chain_id not in _ALLOWED_TESTNET_CHAIN_IDS:
            _fail("UNSUPPORTED_TESTNET_CHAIN")
        if type(self.amount_base_units) is not int or self.amount_base_units < 1:
            _fail("INVALID_AMOUNT_BASE_UNITS")
        if type(self.token_decimals) is not int or not 0 <= self.token_decimals <= 36:
            _fail("INVALID_TOKEN_DECIMALS")
        object.__setattr__(
            self,
            "recipient_address",
            _canonical_address(self.recipient_address, "INVALID_RECIPIENT_ADDRESS"),
        )
        object.__setattr__(
            self,
            "token_address",
            _canonical_address(self.token_address, "INVALID_TOKEN_ADDRESS"),
        )
        if self.gas_limit_multiplier is not None:
            object.__setattr__(
                self,
                "gas_limit_multiplier",
                _canonical_decimal_string(
                    self.gas_limit_multiplier,
                    "INVALID_GAS_LIMIT_MULTIPLIER",
                ),
            )

    @property
    def amount_decimal_string(self) -> str:
        return amount_base_units_to_decimal_string(
            self.amount_base_units,
            self.token_decimals,
        )

    @property
    def request_material(self) -> dict[str, Any]:
        return {
            "amount_base_units": self.amount_base_units,
            "chain_id": self.chain_id,
            "gas_limit_multiplier": self.gas_limit_multiplier,
            "recipient_address": self.recipient_address,
            "token_address": self.token_address,
            "token_decimals": self.token_decimals,
        }

    @property
    def broadcast_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "amount": self.amount_decimal_string,
            "chainId": self.chain_id,
            "recipientAddress": self.recipient_address,
            "tokenAddress": self.token_address,
        }
        if self.gas_limit_multiplier is not None:
            body["gasLimitMultiplier"] = self.gas_limit_multiplier
        return body

    @property
    def simulation_body(self) -> dict[str, Any]:
        return {**self.broadcast_body, "simulate": True}


@dataclass(frozen=True)
class KeeperHubTransportResponse:
    status_code: int
    body: Mapping[str, Any]
    headers: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            _fail("INVALID_HTTP_STATUS")
        if not isinstance(self.body, Mapping):
            _fail("INVALID_RESPONSE_BODY")
        if self.headers is not None and (
            not isinstance(self.headers, Mapping)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in self.headers.items()
            )
        ):
            _fail("INVALID_RESPONSE_HEADERS")


class KeeperHubTransferTransport(Protocol):
    """Injected HTTP boundary. Authentication is configured outside the adapter."""

    def post_transfer(
        self,
        body: Mapping[str, Any],
        *,
        idempotency_key: str | None,
    ) -> KeeperHubTransportResponse: ...


class KeeperHubDirectExecutionPort:
    """Simulation-first provider port that never owns credentials or performs I/O itself."""

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

    def execute(self, attempt: ExecutionAttemptRecord) -> ProviderExecutionResult:
        if not isinstance(attempt, ExecutionAttemptRecord):
            _fail("INVALID_EXECUTION_ATTEMPT")
        if attempt.state is not ExecutionAttemptState.IN_FLIGHT:
            _fail("ATTEMPT_NOT_IN_FLIGHT")
        if attempt.plan.provider_namespace != KEEPERHUB_PROVIDER_NAMESPACE:
            _fail("PROVIDER_NAMESPACE_MISMATCH")
        expected_fingerprint = derive_request_fingerprint(
            attempt.plan.provider_namespace,
            attempt.plan.request_key,
            self._intent.request_material,
        )
        if expected_fingerprint != attempt.plan.request_fingerprint:
            _fail("REQUEST_FINGERPRINT_MISMATCH")

        simulation = self._transport.post_transfer(
            self._intent.simulation_body,
            idempotency_key=None,
        )
        simulation_decision = self._classify_simulation(simulation)
        if simulation_decision is not None:
            return simulation_decision

        broadcast = self._transport.post_transfer(
            self._intent.broadcast_body,
            idempotency_key=attempt.plan.request_key,
        )
        return self._classify_broadcast(broadcast)

    @staticmethod
    def _classify_simulation(
        response: KeeperHubTransportResponse,
    ) -> ProviderExecutionResult | None:
        if not isinstance(response, KeeperHubTransportResponse):
            _fail("INVALID_SIMULATION_RESPONSE")
        if response.status_code in {401, 403, 422}:
            return ProviderExecutionResult(ExecutionPortOutcome.REJECTED_FINAL)

        success = response.body.get("success")
        status = response.body.get("status")
        would_revert = response.body.get("wouldRevert")
        if response.status_code == 400:
            if success is False and status == "simulated" and would_revert is True:
                return ProviderExecutionResult(ExecutionPortOutcome.REJECTED_FINAL)
            _fail("INVALID_SIMULATION_REJECTION")
        if response.status_code != 200:
            _fail("SIMULATION_OUTCOME_UNKNOWN")
        if type(success) is not bool or type(would_revert) is not bool:
            _fail("INVALID_SIMULATION_RESPONSE")
        if not success or would_revert:
            return ProviderExecutionResult(ExecutionPortOutcome.REJECTED_FINAL)
        if status != "simulated":
            _fail("INVALID_SIMULATION_STATUS")
        return None

    @staticmethod
    def _classify_broadcast(
        response: KeeperHubTransportResponse,
    ) -> ProviderExecutionResult:
        if not isinstance(response, KeeperHubTransportResponse):
            _fail("INVALID_BROADCAST_RESPONSE")
        if response.status_code in {401, 403, 422}:
            return ProviderExecutionResult(ExecutionPortOutcome.REJECTED_FINAL)
        if response.status_code != 202:
            _fail("BROADCAST_OUTCOME_UNKNOWN")
        execution_id = response.body.get("executionId")
        status = response.body.get("status")
        if (
            not isinstance(execution_id, str)
            or not execution_id
            or execution_id.strip() != execution_id
            or len(execution_id) > _MAX_PROVIDER_REFERENCE_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in execution_id)
        ):
            _fail("INVALID_EXECUTION_ID")
        if status not in _OFFICIAL_EXECUTION_STATUSES:
            _fail("INVALID_BROADCAST_STATUS")
        return ProviderExecutionResult(
            ExecutionPortOutcome.ACCEPTED,
            execution_id,
        )
