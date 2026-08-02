"""Execution-port wrapper that durably journals provider references before ACK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from nexus_vector.application.execution_dispatch import (
    ExecutionPortOutcome,
    ExecutionPortResult,
)
from nexus_vector.domain.execution_attempts import ExecutionAttemptRecord
from nexus_vector.domain.provider_execution_references import (
    ProviderExecutionReference,
    ProviderExecutionReferenceError,
    normalize_provider_namespace,
)
from nexus_vector.persistence.sqlite_provider_execution_reference_store import (
    SQLiteProviderExecutionReferenceStore,
    SQLiteProviderExecutionReferenceStoreError,
)


class ProviderReferencePortError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ProviderReferencePortError(code)


@dataclass(frozen=True)
class ProviderExecutionResult:
    outcome: ExecutionPortOutcome
    provider_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ExecutionPortOutcome):
            _fail("INVALID_PROVIDER_OUTCOME")
        if self.outcome is ExecutionPortOutcome.ACCEPTED:
            if not isinstance(self.provider_reference, str) or not self.provider_reference:
                _fail("MISSING_PROVIDER_REFERENCE")
        elif self.provider_reference is not None:
            _fail("UNEXPECTED_PROVIDER_REFERENCE")


class ProviderExecutionPort(Protocol):
    def execute(self, attempt: ExecutionAttemptRecord) -> ProviderExecutionResult: ...


class ProviderReferencePersistingPort:
    """Adapt a provider port to the generic dispatch port with durable reference first."""

    def __init__(
        self,
        provider_port: ProviderExecutionPort,
        reference_store: SQLiteProviderExecutionReferenceStore,
        *,
        provider_namespace: str,
    ) -> None:
        if not callable(getattr(provider_port, "execute", None)):
            _fail("INVALID_PROVIDER_PORT")
        if not isinstance(reference_store, SQLiteProviderExecutionReferenceStore):
            _fail("INVALID_REFERENCE_STORE")
        try:
            canonical_namespace = normalize_provider_namespace(provider_namespace)
        except ProviderExecutionReferenceError as error:
            raise ProviderReferencePortError(error.code) from None
        self._provider_port = provider_port
        self._reference_store = reference_store
        self._provider_namespace = canonical_namespace

    def execute(self, attempt: ExecutionAttemptRecord) -> ExecutionPortResult:
        if not isinstance(attempt, ExecutionAttemptRecord):
            _fail("INVALID_EXECUTION_ATTEMPT")
        if attempt.plan.provider_namespace != self._provider_namespace:
            _fail("PROVIDER_NAMESPACE_MISMATCH")

        try:
            self._reference_store.initialize()
            existing = self._reference_store.get(attempt.attempt_id)
        except SQLiteProviderExecutionReferenceStoreError as error:
            raise ProviderReferencePortError(error.code) from None
        if existing is not None:
            _fail("PROVIDER_REFERENCE_ALREADY_EXISTS")

        result = self._provider_port.execute(attempt)
        if not isinstance(result, ProviderExecutionResult):
            _fail("INVALID_PROVIDER_RESULT")

        if result.outcome is ExecutionPortOutcome.REJECTED_FINAL:
            return ExecutionPortResult(ExecutionPortOutcome.REJECTED_FINAL)
        if result.outcome is not ExecutionPortOutcome.ACCEPTED:
            _fail("INVALID_PROVIDER_OUTCOME")

        try:
            reference = ProviderExecutionReference(
                attempt_id=attempt.attempt_id,
                provider_namespace=attempt.plan.provider_namespace,
                request_fingerprint=attempt.plan.request_fingerprint,
                provider_reference=result.provider_reference,
                created_at_utc=attempt.updated_at_utc,
            )
        except ProviderExecutionReferenceError as error:
            raise ProviderReferencePortError(error.code) from None

        self._reference_store.create(reference)
        return ExecutionPortResult(ExecutionPortOutcome.ACCEPTED)
