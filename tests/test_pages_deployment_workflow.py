from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"


class PagesDeploymentWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_canonical_validation_paths_exist(self) -> None:
        paths = (
            "tools/verify_repository_hygiene.py",
            "tools/verify_public_evidence.py",
            "tests/test_static_replay_ui.py",
            "tests/test_pages_deployment_workflow.py",
        )
        for relative in paths:
            with self.subTest(relative=relative):
                self.assertIn(relative, self.text)
                self.assertTrue((ROOT / relative).is_file(), relative)
        self.assertNotIn("tools/check_repository_hygiene.py", self.text)

    def test_pull_request_cannot_run_deploy_job(self) -> None:
        self.assertIn(
            "if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'",
            self.text,
        )
        self.assertIn("needs: validate", self.text)
        self.assertIn("pages: write", self.text)
        self.assertIn("id-token: write", self.text)
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read$")

    def test_only_frontend_directory_is_uploaded(self) -> None:
        self.assertIn("path: ./frontend", self.text)
        self.assertIn("include-hidden-files: true", self.text)
        self.assertNotIn("npm ", self.text)
        self.assertNotIn("curl ", self.text)
        self.assertNotIn("wget ", self.text)

    def test_all_actions_are_exact_sha_pinned(self) -> None:
        uses = re.findall(r"(?m)^\s*uses:\s*([^\s]+)$", self.text)
        self.assertEqual(len(uses), 4)
        for value in uses:
            with self.subTest(value=value):
                self.assertRegex(value, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
                self.assertNotIn("@main", value)
                self.assertNotRegex(value, r"@v\d")

    def test_checkout_does_not_persist_credentials(self) -> None:
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("cancel-in-progress: false", self.text)


if __name__ == "__main__":
    unittest.main()
