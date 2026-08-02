from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nexus_vector.application.execution_dispatch import ExecutionPortOutcome
from nexus_vector.application.provider_reference_port import (
    ProviderExecutionResult,
    ProviderReferencePersistingPort,
    ProviderReferencePortError,
)
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptState,
    build_execution_attempt_plan,
    create_initial_execution_attempt,
)
from nexus_vector.domain.provider_execution_references import (
    ProviderExecutionReference,
)
from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
)
from nexus_vector.persistence.sqlite_provider_execution_reference_store import (
    SQLiteProviderExecutionReferenceStore,
)

T0 = datetime(2026, 8, 2, 19, 55, tzinfo=timezone.utc)
MISSION_KEY = "msn_" + "61" * 32
EFFECT_ID = "eff_" + "62" * 32


class CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, attempt):
        self.calls += 1
        return ProviderExecutionResult(
            ExecutionPortOutcome.ACCEPTED,
            "kh-existing-guard-should-not-run",
        )


class ExistingProviderReferenceGuardTests(unittest.TestCase):
    def test_existing_reference_blocks_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt_store = SQLiteExecutionAttemptStore(root / "attempts.sqlite3")
            reference_store = SQLiteProviderExecutionReferenceStore(
                root / "provider-references.sqlite3"
            )
            plan = build_execution_attempt_plan(
                mission_key=MISSION_KEY,
                effect_id=EFFECT_ID,
                provider_namespace="keeperhub.direct.v1",
                request_key="existing-reference-guard",
                request_material={"amount_base_units": 10, "chain_id": 84532},
            )

            attempt_store.initialize()
            prepared = attempt_store.create(
                create_initial_execution_attempt(plan, T0)
            )
            in_flight = attempt_store.transition(
                prepared.record.attempt_id,
                prepared.revision,
                ExecutionAttemptState.IN_FLIGHT,
                T0,
            )

            reference_store.initialize()
            expected = reference_store.create(
                ProviderExecutionReference(
                    attempt_id=plan.attempt_id,
                    provider_namespace=plan.provider_namespace,
                    request_fingerprint=plan.request_fingerprint,
                    provider_reference="kh-existing-reference",
                    created_at_utc=T0,
                )
            )

            provider = CountingProvider()
            port = ProviderReferencePersistingPort(
                provider,
                reference_store,
                provider_namespace=plan.provider_namespace,
            )
            with self.assertRaises(ProviderReferencePortError) as caught:
                port.execute(in_flight.record)

            self.assertEqual(
                caught.exception.code,
                "PROVIDER_REFERENCE_ALREADY_EXISTS",
            )
            self.assertEqual(provider.calls, 0)
            self.assertEqual(reference_store.get(plan.attempt_id), expected)


if __name__ == "__main__":
    unittest.main()
