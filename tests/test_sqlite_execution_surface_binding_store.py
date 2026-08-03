from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from nexus_vector.domain.execution_surfaces import (
    ExecutionSurface,
    ExecutionSurfaceBinding,
)
from nexus_vector.persistence.sqlite_execution_surface_binding_store import (
    SQLiteExecutionSurfaceBindingStore,
    SQLiteExecutionSurfaceBindingStoreError,
)

T0 = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
MISSION_KEY = "msn_" + "11" * 32
EFFECT_ID = "eff_" + "22" * 32
OTHER_EFFECT_ID = "eff_" + "33" * 32


def binding(
    surface=ExecutionSurface.DIRECT_EXECUTION,
    reference="surface-binding-001",
    effect_id=EFFECT_ID,
):
    return ExecutionSurfaceBinding(
        mission_key=MISSION_KEY,
        effect_id=effect_id,
        surface=surface,
        binding_reference=reference,
        bound_at_utc=T0,
    )


class SQLiteExecutionSurfaceBindingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "surface-bindings.sqlite3"
        self.store = SQLiteExecutionSurfaceBindingStore(self.path)
        self.store.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_binding_survives_restart(self):
        stored = self.store.bind(binding())
        restarted = SQLiteExecutionSurfaceBindingStore(self.path)
        restarted.initialize()
        self.assertEqual(restarted.get(EFFECT_ID), stored)

    def test_same_surface_rebind_is_idempotent_without_authority_churn(self):
        first = self.store.bind(binding())
        repeated = self.store.bind(
            binding(reference="new-reference-that-must-not-replace-authority")
        )
        self.assertEqual(repeated, first)
        self.assertEqual(repeated.binding_reference, "surface-binding-001")

    def test_different_surface_is_terminal_conflict(self):
        self.store.bind(binding())
        with self.assertRaises(
            SQLiteExecutionSurfaceBindingStoreError
        ) as caught:
            self.store.bind(
                binding(
                    surface=ExecutionSurface.WORKFLOW,
                    reference="workflow-binding-001",
                )
            )
        self.assertEqual(caught.exception.code, "SURFACE_BINDING_CONFLICT")
        self.assertEqual(
            self.store.get(EFFECT_ID).surface,
            ExecutionSurface.DIRECT_EXECUTION,
        )

    def test_binding_reference_cannot_be_reused_for_another_effect(self):
        self.store.bind(binding())
        with self.assertRaises(
            SQLiteExecutionSurfaceBindingStoreError
        ) as caught:
            self.store.bind(binding(effect_id=OTHER_EFFECT_ID))
        self.assertEqual(caught.exception.code, "BINDING_REFERENCE_CONFLICT")
        self.assertIsNone(self.store.get(OTHER_EFFECT_ID))

    def test_concurrent_different_surfaces_have_one_winner(self):
        def bind_surface(surface):
            store = SQLiteExecutionSurfaceBindingStore(self.path)
            store.initialize()
            try:
                return store.bind(
                    binding(
                        surface=surface,
                        reference=f"binding-{surface.value}",
                    )
                )
            except SQLiteExecutionSurfaceBindingStoreError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(
                pool.map(
                    bind_surface,
                    (
                        ExecutionSurface.DIRECT_EXECUTION,
                        ExecutionSurface.WORKFLOW,
                        ExecutionSurface.MCP,
                    ),
                )
            )
        winners = [item for item in results if not isinstance(item, str)]
        errors = [item for item in results if isinstance(item, str)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(errors, ["SURFACE_BINDING_CONFLICT"] * 2)
        self.assertEqual(self.store.get(EFFECT_ID), winners[0])

    def test_incompatible_schema_fails_closed(self):
        bad_path = Path(self.temporary.name) / "bad.sqlite3"
        connection = sqlite3.connect(bad_path)
        try:
            connection.execute(
                "CREATE TABLE execution_surface_bindings (effect_id TEXT PRIMARY KEY)"
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(
            SQLiteExecutionSurfaceBindingStoreError
        ) as caught:
            SQLiteExecutionSurfaceBindingStore(bad_path).initialize()
        self.assertEqual(caught.exception.code, "INCOMPATIBLE_SCHEMA")

    def test_record_contains_no_provider_payload_or_credentials(self):
        stored = self.store.bind(binding())
        rendered = repr(stored)
        self.assertNotIn("apiKey", rendered)
        self.assertNotIn("recipientAddress", rendered)
        self.assertNotIn("amount", rendered)
        self.assertNotIn("executionId", rendered)


if __name__ == "__main__":
    unittest.main()
