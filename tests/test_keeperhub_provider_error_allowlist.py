from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from nexus_vector.integrations import keeperhub_http_transport as http_transport


ROOT = Path(__file__).parents[1]
CANONICAL_ERROR_CODES = frozenset(
    {
        "unauthorized",
        "insufficient_scope",
        "not_found",
        "invalid_input",
        "conflict",
        "rate_limited",
        "internal_error",
    }
)


def load_tool_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IDENTITY = load_tool_module(
    "keeperhub_key_identity_probe_allowlist",
    "tools/keeperhub_key_identity_probe.py",
)
SURFACE = load_tool_module(
    "keeperhub_key_identity_surface_probe_allowlist",
    "tools/keeperhub_key_identity_surface_probe.py",
)


class KeeperHubProviderErrorAllowlistTests(unittest.TestCase):
    def test_all_keeperhub_clients_share_the_exact_canonical_allowlist(self) -> None:
        self.assertEqual(
            IDENTITY._ALLOWED_PROVIDER_ERROR_CODES,
            CANONICAL_ERROR_CODES,
        )
        self.assertEqual(
            SURFACE.ALLOWED_ERROR_CODES,
            CANONICAL_ERROR_CODES,
        )
        self.assertEqual(
            http_transport._ALLOWED_PROVIDER_ERROR_CODES,
            CANONICAL_ERROR_CODES,
        )

    def test_every_canonical_code_is_exposed_by_all_sanitizers(self) -> None:
        for code in sorted(CANONICAL_ERROR_CODES):
            with self.subTest(code=code):
                payload = {
                    "error": code,
                    "detail": "private provider detail",
                    "hint": "private provider hint",
                    "request_id": "private-provider-request-id",
                }
                self.assertEqual(
                    IDENTITY._safe_provider_error_code(payload),
                    code,
                )
                self.assertEqual(
                    SURFACE.safe_error_code(payload),
                    code,
                )
                self.assertEqual(
                    http_transport._safe_provider_error_code(payload),
                    code,
                )

    def test_unknown_or_malformed_codes_remain_suppressed(self) -> None:
        candidates = (
            "private_internal_reason",
            "wallet_not_configured",
            "Unauthorized",
            "rate-limited",
            "",
            None,
            403,
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                payload = {
                    "error": candidate,
                    "detail": "private provider detail",
                }
                self.assertIsNone(
                    IDENTITY._safe_provider_error_code(payload)
                )
                self.assertIsNone(
                    SURFACE.safe_error_code(payload)
                )
                self.assertIsNone(
                    http_transport._safe_provider_error_code(payload)
                )

    def test_allowlist_does_not_enable_raw_response_diagnostics(self) -> None:
        for relative_path in (
            "tools/keeperhub_key_identity_probe.py",
            "tools/keeperhub_key_identity_surface_probe.py",
            "src/nexus_vector/integrations/keeperhub_http_transport.py",
        ):
            with self.subTest(relative_path=relative_path):
                source = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertNotIn("raw_error_value", source)
                self.assertNotIn("response_top_level_keys", source)
                self.assertNotIn("private provider detail", source)


if __name__ == "__main__":
    unittest.main()
