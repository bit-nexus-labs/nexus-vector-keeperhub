from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "tools" / "keeperhub_rehearsal_execution.py"
SPEC = importlib.util.spec_from_file_location(
    "keeperhub_rehearsal_execution_output_guard",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class KeeperHubRehearsalOutputSanitizationTests(unittest.TestCase):
    def test_provider_reference_is_always_one_way_masked_even_when_short(self):
        raw = "short-exec-id"
        masked = RUNNER._mask_provider_reference(raw)

        self.assertNotEqual(masked, raw)
        self.assertIsInstance(masked, str)
        self.assertRegex(masked, r"^khref_sha256_[0-9a-f]{20}$")
        self.assertNotIn(raw, masked)
        self.assertEqual(masked, RUNNER._mask_provider_reference(raw))

    def test_mask_never_echoes_long_provider_reference(self):
        raw = "keeperhub-execution-" + "a" * 80
        masked = RUNNER._mask_provider_reference(raw)

        self.assertIsNotNone(masked)
        self.assertNotIn(raw[:10], masked)
        self.assertNotIn(raw[-10:], masked)
        self.assertTrue(re.fullmatch(r"khref_sha256_[0-9a-f]{20}", masked))


if __name__ == "__main__":
    unittest.main()
