from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus_vector.persistence.sqlite_keeperhub_authorization_ledger import (
    KeeperHubAuthorizationPhase,
    KeeperHubAuthorizationRecord,
    KeeperHubAuthorizationState,
    SQLiteKeeperHubAuthorizationLedger,
    SQLiteKeeperHubAuthorizationLedgerError,
)

T0 = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
ATTEMPT_ID = "att_" + "11" * 32
REQUEST_FINGERPRINT = "xrf_" + "22" * 32
BODY_FINGERPRINT = "khb_" + "33" * 32


def make_record(**changes):
    values = {
        "approval_reference": "approval-simulation-001",
        "phase": KeeperHubAuthorizationPhase.SIMULATION,
        "action_sheet_id": "action-sheet-001",
        "attempt_id": ATTEMPT_ID,
        "request_fingerprint": REQUEST_FINGERPRINT,
        "body_fingerprint": BODY_FINGERPRINT,
        "state": KeeperHubAuthorizationState.CLAIMED,
        "claimed_at_utc": T0,
        "updated_at_utc": T0,
    }
    values.update(changes)
    return KeeperHubAuthorizationRecord(**values)


class SQLiteKeeperHubAuthorizationLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.path = Path(self._temporary.name) / "authorization.sqlite3"
        self.store = SQLiteKeeperHubAuthorizationLedger(self.path)
        self.store.initialize()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_claim_and_final_state_survive_restart(self):
        claimed = self.store.claim(make_record())
        self.assertEqual(claimed.state, KeeperHubAuthorizationState.CLAIMED)
        finalized = self.store.transition(
            claimed.approval_reference,
            KeeperHubAuthorizationState.ELIGIBLE_FOR_BROADCAST_APPROVAL,
            T0 + timedelta(seconds=1),
        )

        restarted = SQLiteKeeperHubAuthorizationLedger(self.path)
        restarted.initialize()
        self.assertEqual(restarted.get(claimed.approval_reference), finalized)
        self.assertEqual(
            restarted.get_for_attempt(
                KeeperHubAuthorizationPhase.SIMULATION,
                ATTEMPT_ID,
            ),
            finalized,
        )

    def test_same_phase_attempt_cannot_receive_another_approval(self):
        self.store.claim(make_record())
        with self.assertRaises(
            SQLiteKeeperHubAuthorizationLedgerError
        ) as caught:
            self.store.claim(
                make_record(
                    approval_reference="approval-simulation-002",
                )
            )
        self.assertEqual(
            caught.exception.code,
            "AUTHORIZATION_ALREADY_CONSUMED",
        )

    def test_simulation_and_broadcast_have_independent_slots(self):
        simulation = self.store.claim(make_record())
        broadcast = self.store.claim(
            make_record(
                approval_reference="approval-broadcast-001",
                phase=KeeperHubAuthorizationPhase.BROADCAST,
            )
        )
        self.assertEqual(
            simulation.phase,
            KeeperHubAuthorizationPhase.SIMULATION,
        )
        self.assertEqual(
            broadcast.phase,
            KeeperHubAuthorizationPhase.BROADCAST,
        )

    def test_transition_matrix_is_phase_specific_and_terminal(self):
        simulation = self.store.claim(make_record())
        with self.assertRaises(
            SQLiteKeeperHubAuthorizationLedgerError
        ) as invalid:
            self.store.transition(
                simulation.approval_reference,
                KeeperHubAuthorizationState.ACCEPTED,
                T0 + timedelta(seconds=1),
            )
        self.assertEqual(
            invalid.exception.code,
            "AUTHORIZATION_TRANSITION_NOT_ALLOWED",
        )

        finalized = self.store.transition(
            simulation.approval_reference,
            KeeperHubAuthorizationState.OUTCOME_UNKNOWN,
            T0 + timedelta(seconds=1),
        )
        self.assertEqual(
            finalized.state,
            KeeperHubAuthorizationState.OUTCOME_UNKNOWN,
        )
        with self.assertRaises(
            SQLiteKeeperHubAuthorizationLedgerError
        ) as terminal:
            self.store.transition(
                simulation.approval_reference,
                KeeperHubAuthorizationState.REJECTED_FINAL,
                T0 + timedelta(seconds=2),
            )
        self.assertEqual(
            terminal.exception.code,
            "AUTHORIZATION_ALREADY_FINALIZED",
        )

    def test_concurrent_claims_produce_one_winner(self):
        def claim(index):
            ledger = SQLiteKeeperHubAuthorizationLedger(self.path)
            ledger.initialize()
            try:
                return ledger.claim(
                    make_record(
                        approval_reference=f"approval-simulation-{index}",
                    )
                )
            except SQLiteKeeperHubAuthorizationLedgerError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(4)))

        records = [
            result for result in results if not isinstance(result, str)
        ]
        errors = [
            result for result in results if isinstance(result, str)
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(
            errors,
            ["AUTHORIZATION_ALREADY_CONSUMED"] * 3,
        )

    def test_incompatible_schema_fails_closed(self):
        bad_path = Path(self._temporary.name) / "bad.sqlite3"
        connection = sqlite3.connect(bad_path)
        try:
            connection.execute(
                "CREATE TABLE keeperhub_authorization_ledger ("
                "approval_reference TEXT PRIMARY KEY)"
            )
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(
            SQLiteKeeperHubAuthorizationLedgerError
        ) as caught:
            SQLiteKeeperHubAuthorizationLedger(bad_path).initialize()
        self.assertEqual(caught.exception.code, "INCOMPATIBLE_SCHEMA")

    def test_record_contains_fingerprints_not_raw_economic_payload(self):
        stored = self.store.claim(make_record())
        rendered = repr(stored)
        self.assertNotIn("recipientAddress", rendered)
        self.assertNotIn("tokenAddress", rendered)
        self.assertNotIn("amount", rendered)
        self.assertIn(BODY_FINGERPRINT, rendered)


if __name__ == "__main__":
    unittest.main()
