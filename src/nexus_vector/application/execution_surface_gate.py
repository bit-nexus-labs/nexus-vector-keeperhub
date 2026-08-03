"""Provider-neutral gate that binds one effect to one execution surface."""

from __future__ import annotations

from typing import Any

from nexus_vector.application.execution_dispatch import (
    ExecutionPort,
    ExecutionPortResult,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptRecord,
    ExecutionAttemptState,
)
from nexus_vector.domain.execution_surfaces import (
    ExecutionSurface,
    ExecutionSurfaceBinding,
    ExecutionSurfaceBindingError,
)
from nexus_vector.persistence.sqlite_execution_surface_binding_store import (
    SQLiteExecutionSurfaceBindingStore,
    SQLiteExecutionSurfaceBindingStoreError,
)


class ExecutionSurfaceGateError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ExecutionSurfaceGateError(code)


class SurfaceBoundExecutionPort:
    """Durably select a provider surface before delegating one mutating call."""

    def __init__(
        self,
        delegate: ExecutionPort,
        binding_store: SQLiteExecutionSurfaceBindingStore,
        surface: ExecutionSurface,
        binding_reference: str,
    ) -> None:
        if not callable(getattr(delegate, "execute", None)):
            _fail("INVALID_EXECUTION_PORT")
        if not isinstance(binding_store, SQLiteExecutionSurfaceBindingStore):
            _fail("INVALID_SURFACE_BINDING_STORE")
        if not isinstance(surface, ExecutionSurface):
            _fail("INVALID_EXECUTION_SURFACE")
        try:
            # Validate without retaining any raw provider payload.
            probe = ExecutionSurfaceBinding(
                mission_key="msn_" + "00" * 32,
                effect_id="eff_" + "00" * 32,
                surface=surface,
                binding_reference=binding_reference,
                bound_at_utc=__import__("datetime").datetime(
                    2000,
                    1,
                    1,
                    tzinfo=__import__("datetime").timezone.utc,
                ),
            )
        except ExecutionSurfaceBindingError as error:
            raise ExecutionSurfaceGateError(error.code) from None
        self._delegate = delegate
        self._binding_store = binding_store
        self._surface = probe.surface
        self._binding_reference = probe.binding_reference

    def execute(self, attempt: ExecutionAttemptRecord) -> ExecutionPortResult:
        if not isinstance(attempt, ExecutionAttemptRecord):
            _fail("INVALID_EXECUTION_ATTEMPT")
        if attempt.state is not ExecutionAttemptState.IN_FLIGHT:
            _fail("ATTEMPT_NOT_IN_FLIGHT")
        try:
            self._binding_store.initialize()
            binding = self._binding_store.bind(
                ExecutionSurfaceBinding(
                    mission_key=attempt.plan.mission_key,
                    effect_id=attempt.plan.effect_id,
                    surface=self._surface,
                    binding_reference=self._binding_reference,
                    bound_at_utc=attempt.updated_at_utc,
                )
            )
        except (
            ExecutionSurfaceBindingError,
            SQLiteExecutionSurfaceBindingStoreError,
        ) as error:
            raise ExecutionSurfaceGateError(error.code) from None
        if (
            binding.mission_key != attempt.plan.mission_key
            or binding.effect_id != attempt.plan.effect_id
            or binding.surface is not self._surface
        ):
            _fail("SURFACE_BINDING_MISMATCH")

        result: Any = self._delegate.execute(attempt)
        if not isinstance(result, ExecutionPortResult):
            _fail("INVALID_PORT_RESULT")
        return result
