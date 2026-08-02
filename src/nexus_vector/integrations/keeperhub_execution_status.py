"""Read-only KeeperHub execution-status observation over an injected transport."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from nexus_vector.domain.provider_execution_references import (
    ProviderExecutionReference,
)
from nexus_vector.integrations.keeperhub_direct_execution import (
    KEEPERHUB_PROVIDER_NAMESPACE,
    KeeperHubTransportResponse,
)

_TRANSACTION_HASH_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")
_MAX_LINK_LENGTH = 2_048


class KeeperHubExecutionStatusError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise KeeperHubExecutionStatusError(code)


class KeeperHubExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class KeeperHubExecutionStatusObservation:
    provider_reference: str
    status: KeeperHubExecutionStatus
    poll_after_seconds: int
    transaction_hash: str | None = None
    transaction_link: str | None = None
    provider_error_present: bool = False

    @property
    def requires_independent_chain_verification(self) -> bool:
        return self.status is KeeperHubExecutionStatus.COMPLETED

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            KeeperHubExecutionStatus.COMPLETED,
            KeeperHubExecutionStatus.FAILED,
        }


class KeeperHubExecutionStatusTransport(Protocol):
    """Injected read-only boundary. Credentials and HTTP are owned elsewhere."""

    def get_execution_status(
        self,
        provider_reference: str,
    ) -> KeeperHubTransportResponse: ...


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if headers is None:
        return None
    expected = name.casefold()
    values = [
        value
        for key, value in headers.items()
        if key.casefold() == expected
    ]
    if len(values) > 1:
        _fail("DUPLICATE_STATUS_HEADER")
    return values[0] if values else None


def _poll_hint(response: KeeperHubTransportResponse, *, terminal: bool) -> int:
    raw = _header(response.headers, "X-Poll-Interval-Hint")
    if raw is None or not raw.isascii() or not raw.isdigit():
        _fail("INVALID_POLL_INTERVAL_HINT")
    value = int(raw)
    if terminal and value != 0:
        _fail("TERMINAL_POLL_INTERVAL_NOT_ZERO")
    if not terminal and value < 1:
        _fail("ACTIVE_POLL_INTERVAL_NOT_POSITIVE")
    return value


def _transaction_evidence(
    body: Mapping[str, Any],
) -> tuple[str, str]:
    transaction_hash = body.get("transactionHash")
    transaction_link = body.get("transactionLink")
    if (
        not isinstance(transaction_hash, str)
        or _TRANSACTION_HASH_PATTERN.fullmatch(transaction_hash) is None
    ):
        _fail("INVALID_TRANSACTION_HASH")
    if (
        not isinstance(transaction_link, str)
        or not transaction_link.startswith("https://")
        or len(transaction_link) > _MAX_LINK_LENGTH
        or transaction_hash.casefold() not in transaction_link.casefold()
        or any(ord(character) < 32 or ord(character) == 127 for character in transaction_link)
    ):
        _fail("INVALID_TRANSACTION_LINK")
    return transaction_hash.casefold(), transaction_link


class KeeperHubExecutionStatusObserver:
    """Parse provider status without mutating attempts or treating it as chain truth."""

    def __init__(self, transport: KeeperHubExecutionStatusTransport) -> None:
        if not callable(getattr(transport, "get_execution_status", None)):
            _fail("INVALID_STATUS_TRANSPORT")
        self._transport = transport

    def observe(
        self,
        reference: ProviderExecutionReference,
    ) -> KeeperHubExecutionStatusObservation:
        if not isinstance(reference, ProviderExecutionReference):
            _fail("INVALID_PROVIDER_REFERENCE_RECORD")
        if reference.provider_namespace != KEEPERHUB_PROVIDER_NAMESPACE:
            _fail("PROVIDER_NAMESPACE_MISMATCH")

        response = self._transport.get_execution_status(
            reference.provider_reference
        )
        if not isinstance(response, KeeperHubTransportResponse):
            _fail("INVALID_STATUS_RESPONSE")
        if response.status_code != 200:
            _fail("STATUS_OUTCOME_UNKNOWN")

        execution_id = response.body.get("executionId")
        raw_status = response.body.get("status")
        if execution_id != reference.provider_reference:
            _fail("EXECUTION_ID_MISMATCH")
        try:
            status = KeeperHubExecutionStatus(raw_status)
        except (TypeError, ValueError):
            _fail("INVALID_EXECUTION_STATUS")

        terminal = status in {
            KeeperHubExecutionStatus.COMPLETED,
            KeeperHubExecutionStatus.FAILED,
        }
        poll_after = _poll_hint(response, terminal=terminal)

        if status is KeeperHubExecutionStatus.COMPLETED:
            transaction_hash, transaction_link = _transaction_evidence(
                response.body
            )
            return KeeperHubExecutionStatusObservation(
                provider_reference=reference.provider_reference,
                status=status,
                poll_after_seconds=poll_after,
                transaction_hash=transaction_hash,
                transaction_link=transaction_link,
            )

        if status is KeeperHubExecutionStatus.FAILED:
            if response.body.get("transactionHash") is not None:
                _fail("FAILED_STATUS_HAS_TRANSACTION_HASH")
            if response.body.get("transactionLink") is not None:
                _fail("FAILED_STATUS_HAS_TRANSACTION_LINK")
            return KeeperHubExecutionStatusObservation(
                provider_reference=reference.provider_reference,
                status=status,
                poll_after_seconds=poll_after,
                provider_error_present=response.body.get("error") is not None,
            )

        if response.body.get("transactionHash") is not None:
            _fail("ACTIVE_STATUS_HAS_TRANSACTION_HASH")
        if response.body.get("transactionLink") is not None:
            _fail("ACTIVE_STATUS_HAS_TRANSACTION_LINK")
        return KeeperHubExecutionStatusObservation(
            provider_reference=reference.provider_reference,
            status=status,
            poll_after_seconds=poll_after,
        )
