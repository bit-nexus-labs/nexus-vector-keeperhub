from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.verify_repository_hygiene import (
    RepositoryHygieneError,
    verify_paths,
)


class RepositoryHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def write(self, relative: str, content: bytes = b"safe\n") -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def assert_code(self, expected: str, paths: list[str]) -> None:
        with self.assertRaises(RepositoryHygieneError) as caught:
            verify_paths(paths, root=self.root)
        self.assertEqual(caught.exception.code, expected)

    def test_safe_public_files_pass(self) -> None:
        self.write("README.md")
        self.write("src/nexus_vector/domain/model.py")
        self.write("evidence/public_manifest.json", b'{"transaction_hash":"0x' + b"1" * 64 + b'"}\n')
        self.assertEqual(
            verify_paths(
                [
                    "README.md",
                    "src/nexus_vector/domain/model.py",
                    "evidence/public_manifest.json",
                ],
                root=self.root,
            ),
            (
                "README.md",
                "src/nexus_vector/domain/model.py",
                "evidence/public_manifest.json",
            ),
        )

    def test_forbidden_runtime_paths_fail(self) -> None:
        cases = {
            ".env": "FORBIDDEN_TRACKED_FILE",
            ".env.local": "FORBIDDEN_TRACKED_FILE",
            "runtime/bot_data.db": "FORBIDDEN_TRACKED_FILE",
            "logs/runtime.txt": "FORBIDDEN_TRACKED_COMPONENT",
            "terminal.txt": "FORBIDDEN_TRACKED_FILE",
            "keys/wallet.pem": "FORBIDDEN_TRACKED_FILE",
        }
        for path, code in cases.items():
            with self.subTest(path=path):
                self.write(path)
                self.assert_code(code, [path])

    def test_high_confidence_secret_shapes_fail_without_echo(self) -> None:
        cases = {
            "private.pem.txt": b"-----BEGIN " + b"PRIVATE KEY-----\nredacted\n",
            "github.txt": b"ghp_" + b"A" * 36,
            "keeperhub.txt": b"kh_" + b"B" * 28,
            "openai.txt": b"sk-" + b"C" * 28,
        }
        for path, content in cases.items():
            with self.subTest(path=path):
                self.write(path, content)
                with self.assertRaises(RepositoryHygieneError) as caught:
                    verify_paths([path], root=self.root)
                self.assertEqual(caught.exception.path, path)
                self.assertNotIn(content.decode("ascii"), str(caught.exception))

    def test_commit_and_transaction_hashes_are_not_secret_false_positives(self) -> None:
        self.write(
            "docs/evidence.md",
            b"commit " + b"a" * 40 + b"\ntx 0x" + b"b" * 64 + b"\n",
        )
        self.assertEqual(
            verify_paths(["docs/evidence.md"], root=self.root),
            ("docs/evidence.md",),
        )

    def test_duplicate_and_unsafe_paths_fail_closed(self) -> None:
        self.write("README.md")
        self.assert_code("DUPLICATE_TRACKED_PATH", ["README.md", "README.md"])
        self.assert_code("UNSAFE_TRACKED_PATH", ["../outside.txt"])
        self.assert_code("INVALID_TRACKED_PATH", ["windows\\path.txt"])

    def test_missing_tracked_file_fails_closed(self) -> None:
        self.assert_code("TRACKED_FILE_MISSING", ["missing.txt"])


if __name__ == "__main__":
    unittest.main()
