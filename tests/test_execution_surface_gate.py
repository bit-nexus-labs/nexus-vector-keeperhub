from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from nexus_vector.application.execution_dispatch import (
    ExecutionPortOutcome,
    ExecutionPortResult,
)
from nexus_vector.application.execution_surface_gate import (
    ExecutionSurfaceGateError,
    SurfaceBoundExecutionPort,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    build_execution_attempt_plan,
    create_initial_execution_attempt,
    transition_execution_attempt,
)
from nexus_vector.domain.execution_surfaces import ExecutionSurface
from nexus_vector.persistence.sqlite_execution_surface_binding_store import (
    SQLiteExecutionSurfaceBindingStore,
)

T0 = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
MISSION_KEY = "msn_" + "11" * 32
EFFECT_ID = "eff_" + "22" * 32


class CountingPort:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def execute(self, attempt):
        with self.lock:
            self.calls.append(attempt)
        return ExecutionPortResult(ExecutionPortOutcome.ACCEPTED)


class InvalidPort:
    def execute(self, attempt):
        return object()


def in_flight_attempt():
    plan = build_execution_attempt_plan(
        mission_key=MISSION_KEY,
        effect_id=EFFECT_ID,
        provider_namespace="keeperhub-direct-execution",
        request_key="surface-gate-request-key",
        request_material={"amount": 7},
    )
    prepared = create_initial_execution_attempt(plan, T0)
    return transition_execution_attempt(
        prepared,
        ExecutionAttemptState.IN_FLIGHT,
        T0,
    )


class ExecutionSurfaceGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "surface.sqlite3"
        self.store = SQLiteExecutionSurfaceBindingStore(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def gate(self, delegate, surface, reference, *, restart=False):
        store = (
            SQLiteExecutionSurfaceBindingStore(self.path)
            if restart
            else self.store
        )
        return SurfaceBoundExecutionPort(
            delegate,
            store,
            surface,
            reference,
        )

    def test_binding_is_durable_before_delegate_call(self):
        delegate = CountingPort()
        attempt = in_flight_attempt()
        result = self.gate(
            delegate,
            ExecutionSurface.DIRECT_EXECUTION,
            "direct-binding-001",
        ).execute(attempt)
        self.assertEqual(result.outcome, ExecutionPortOutcome.ACCEPTED)
        self.assertEqual(delegate.calls, [attempt])
        stored = self.store.get(EFFECT_ID)
        self.assertEqual(stored.mission_key, MISSION_KEY)
        self.assertEqual(stored.surface, ExecutionSurface.DIRECT_EXECUTION)

    def test_different_surface_after_restart_blocks_before_delegate(self):
        attempt = in_flight_attempt()
        self.gate(
            CountingPort(),
            ExecutionSurface.DIRECT_EXECUTION,
            "direct-binding-001",
        ).execute(attempt)
        workflow = CountingPort()
        with self.assertRaises(ExecutionSurfaceGateError) as caught:
            self.gate(
                workflow,
                ExecutionSurface.WORKFLOW,
                "workflow-binding-001",
                restart=True,
            ).execute(attempt)
        self.assertEqual(caught.exception.code, "SURFACE_BINDING_CONFLICT")
        self.assertEqual(workflow.calls, [])

    def test_same_surface_reconstruction_preserves_original_binding(self):
        attempt = in_flight_attempt()
        self.gate(
            CountingPort(),
            ExecutionSurface.DIRECT_EXECUTION,
            "direct-binding-001",
        ).execute(attempt)
        delegate = CountingPort()
        self.gate(
            delegate,
            ExecutionSurface.DIRECT_EXECUTION,
            "direct-binding-after-restart",
            restart=True,
        ).execute(attempt)
        self.assertEqual(len(delegate.calls), 1)
        self.assertEqual(
            self.store.get(EFFECT_ID).binding_reference,
            "direct-binding-001",
        )

    def test_concurrent_different_surfaces_call_one_delegate(self):
        attempt = in_flight_attempt()
        delegates = {
            surface: CountingPort()
            for surface in ExecutionSurface
        }

        def run(surface):
            try:
                return self.gate(
                    delegates[surface],
                    surface,
                    f"binding-{surface.value}",
                    restart=True,
                ).execute(attempt)
            except ExecutionSurfaceGateError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(run, tuple(ExecutionSurface)))
        successes = [item for item in results if not isinstance(item, str)]
        errors = [item for item in results if isinstance(item, str)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(errors, ["SURFACE_BINDING_CONFLICT"] * 2)
        self.assertEqual(
            sum(len(delegate.calls) for delegate in delegates.values()),
            1,
        )

    def test_non_in_flight_attempt_never_binds_or_calls(self):
        attempt = in_flight_attempt()
        prepared = create_initial_execution_attempt(attempt.plan, T0)
        delegate = CountingPort()
        with self.assertRaises(ExecutionSurfaceGateError) as caught:
            self.gate(
                delegate,
                ExecutionSurface.MCP,
                "mcp-binding-001",
            ).execute(prepared)
        self.assertEqual(caught.exception.code, "ATTEMPT_NOT_IN_FLIGHT")
        self.assertEqual(delegate.calls, [])
        self.store.initialize()
        self.assertIsNone(self.store.get(EFFECT_ID))

    def test_invalid_delegate_result_fails_after_binding_without_fallback(self):
        attempt = in_flight_attempt()
        with self.assertRaises(ExecutionSurfaceGateError) as caught:
            self.gate(
                InvalidPort(),
                ExecutionSurface.DIRECT_EXECUTION,
                "direct-binding-001",
            ).execute(attempt)
        self.assertEqual(caught.exception.code, "INVALID_PORT_RESULT")
        self.assertEqual(
            self.store.get(EFFECT_ID).surface,
            ExecutionSurface.DIRECT_EXECUTION,
        )
        workflow = CountingPort()
        with self.assertRaises(ExecutionSurfaceGateError):
            self.gate(
                workflow,
                ExecutionSurface.WORKFLOW,
                "workflow-binding-001",
            ).execute(attempt)
        self.assertEqual(workflow.calls, [])


if __name__ == "__main__":
    unittest.main()
