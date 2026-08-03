"""Capability-limited KeeperHub runtime boundaries for readiness and simulation.

The objects in this module deliberately expose less authority than the general
KeeperHub HTTP transport:

* ``KeeperHubReadOnlyRuntimeClient`` exposes only approved GET operations.
* ``KeeperHubSimulationOnlyTransport`` exposes only a simulation-shaped
  ``post_transfer`` call and rejects every idempotency key or broadcast-shaped
  response.

Neither object reads credentials, retries requests, signs transactions, or
provides a broadcast method.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nexus_vector.integrations.keeperhub_direct_execution import (
    KeeperHubTransportResponse,
)
from nexus_vector.integrations.keeperhub_http_transport import (
    KeeperHubChain,
    KeeperHubHttpTransport,
    KeeperHubWalletReadiness,
)

_REQUIRED_SIMULATION_KEYS = frozenset(
    {
        "amount",
        "chainId",
        "recipientAddress",
        "simulate",
        "tokenAddress",
    }
)
_ALLOWED_SIMULATION_KEYS = _REQUIRED_SIMULATION_KEYS | frozenset(
    {"gasLimitMultiplier"}
)
_FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "auditid",
        "executionid",
        "rawtransaction",
        "reservationid",
        "signedtransaction",
        "transactionhash",
        "transactionlink",
        "txhash",
    }
)
_BROADCAST_STATUSES = frozenset(
    {"pending", "running", "completed", "failed", "success", "error"}
)
_MAX_RESPONSE_NODES = 10_000


class KeeperHubSimulationRuntimeError(RuntimeError):
    """Machine-classifiable failure without credential or payload echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise KeeperHubSimulationRuntimeError(code)


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _validate_simulation_response(response: KeeperHubTransportResponse) -> None:
    if not isinstance(response, KeeperHubTransportResponse):
        _fail("INVALID_SIMULATION_RESPONSE")

    pending: list[Any] = [response.body]
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        if visited > _MAX_RESPONSE_NODES:
            _fail("SIMULATION_RESPONSE_TOO_COMPLEX")

        if isinstance(current, Mapping):
            for key, value in current.items():
                if not isinstance(key, str):
                    _fail("INVALID_SIMULATION_RESPONSE_KEY")
                normalized = _normalized_key(key)
                if normalized in _FORBIDDEN_RESPONSE_KEYS:
                    _fail("SIMULATION_EXECUTION_EVIDENCE_PRESENT")
                if normalized == "status" and isinstance(value, str):
                    if value.casefold() in _BROADCAST_STATUSES:
                        _fail("SIMULATION_BROADCAST_STATUS_PRESENT")
                pending.append(value)
        elif isinstance(current, (list, tuple)):
            pending.extend(current)


class KeeperHubSimulationOnlyTransport:
    """Expose exactly one simulation-shaped transfer capability.

    The wrapped transport may be a general KeeperHub transport, but callers of
    this boundary cannot provide an idempotency key or omit ``simulate=true``.
    The class performs no retry and passes through exactly one underlying call.
    """

    def __init__(self, transport: KeeperHubHttpTransport) -> None:
        if not isinstance(transport, KeeperHubHttpTransport):
            _fail("INVALID_KEEPERHUB_HTTP_TRANSPORT")
        self._transport = transport

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"

    def post_transfer(
        self,
        body: Mapping[str, Any],
        *,
        idempotency_key: str | None,
    ) -> KeeperHubTransportResponse:
        if idempotency_key is not None:
            _fail("SIMULATION_IDEMPOTENCY_KEY_FORBIDDEN")
        if not isinstance(body, Mapping):
            _fail("INVALID_SIMULATION_BODY")

        body_keys = frozenset(body.keys())
        if any(not isinstance(key, str) for key in body_keys):
            _fail("INVALID_SIMULATION_BODY_KEY")
        if not _REQUIRED_SIMULATION_KEYS.issubset(body_keys):
            _fail("INCOMPLETE_SIMULATION_BODY")
        if not body_keys.issubset(_ALLOWED_SIMULATION_KEYS):
            _fail("UNAPPROVED_SIMULATION_BODY_FIELD")
        if body.get("simulate") is not True:
            _fail("SIMULATION_FLAG_REQUIRED")

        response = self._transport.post_transfer(
            dict(body),
            idempotency_key=None,
        )
        _validate_simulation_response(response)
        return response


class KeeperHubReadOnlyRuntimeClient:
    """Expose only approved read-only KeeperHub readiness surfaces."""

    def __init__(self, transport: KeeperHubHttpTransport) -> None:
        if not isinstance(transport, KeeperHubHttpTransport):
            _fail("INVALID_KEEPERHUB_HTTP_TRANSPORT")
        self._transport = transport

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"

    def get_wallet_readiness(self) -> KeeperHubWalletReadiness:
        return self._transport.get_wallet_readiness()

    def list_chains(self) -> tuple[KeeperHubChain, ...]:
        return self._transport.list_chains()

    def get_wallet_balances(self) -> Mapping[str, Any] | list[Any]:
        status, payload, _ = self._transport._json_request(
            method="GET",
            path="/user/wallet/balances",
        )
        if status != 200:
            _fail("WALLET_BALANCES_UNKNOWN")
        if not isinstance(payload, (Mapping, list)):
            _fail("INVALID_WALLET_BALANCES_RESPONSE")
        return payload
