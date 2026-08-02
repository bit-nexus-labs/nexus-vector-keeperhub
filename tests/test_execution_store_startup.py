from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from nexus_vector.persistence.sqlite_execution_attempt_store import (
    SQLiteExecutionAttemptStore,
    SQLiteExecutionAttemptStoreError,
)


class ExecutionStoreStartupTests(unittest.TestCase):
    def test_concurrent_first_initialize_is_bounded_and_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for index in range(20):
                with self.subTest(index=index):
                    database_path = root / f"init-race-{index}.sqlite3"
                    barrier = threading.Barrier(2)
                    errors: list[str] = []

                    def initialize_worker() -> None:
                        store = SQLiteExecutionAttemptStore(database_path)
                        barrier.wait()
                        try:
                            store.initialize()
                        except SQLiteExecutionAttemptStoreError as error:
                            errors.append(error.code)

                    threads = [
                        threading.Thread(target=initialize_worker)
                        for _ in range(2)
                    ]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()

                    self.assertEqual(errors, [])
                    reopened = SQLiteExecutionAttemptStore(database_path)
                    reopened.initialize()
                    self.assertEqual(reopened.list_recovery_candidates(), ())


if __name__ == "__main__":
    unittest.main()
