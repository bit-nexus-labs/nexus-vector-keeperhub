from __future__ import annotations

import ast
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any

from nexus_vector.integrations.keeperhub_http_transport import KeeperHubHttpTransport

ROOT = Path(__file__).parents[1]
TOOLS = ROOT / "tools"
USER_AGENT = "NexusVector-KeeperHub/1.0"
API_KEY = "kh_" + "a" * 32


def load_tool_module(name: str, filename: str):
    path = TOOLS / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


KEY_PROBE = load_tool_module(
    "keeperhub_key_identity_probe_user_agent_test",
    "keeperhub_key_identity_probe.py",
)
SURFACE_PROBE = load_tool_module(
    "keeperhub_key_identity_surface_probe_user_agent_test",
    "keeperhub_key_identity_surface_probe.py",
)
MCP_RUNTIME = load_tool_module(
    "keeperhub_mcp_oauth_runtime_user_agent_test",
    "_keeperhub_mcp_oauth_runtime.py",
)


class Response:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.closed = False

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._raw if limit < 0 else self._raw[:limit]

    def close(self) -> None:
        self.closed = True


class CapturingOpener:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[Any, float]] = []

    def open(self, request, *, timeout: float):
        self.calls.append((request, timeout))
        if not self.responses:
            raise AssertionError("UNEXPECTED_HTTP_CALL")
        return self.responses.pop(0)


def header(request, name: str) -> str | None:
    wanted = name.casefold()
    for key, value in request.header_items():
        if key.casefold() == wanted:
            return value
    return None


class KeeperHubUserAgentPolicyTests(unittest.TestCase):
    def test_rest_transport_sets_explicit_user_agent_on_get_and_post(self) -> None:
        opener = CapturingOpener(
            [
                Response(200, {"hasWallet": False}),
                Response(200, {"success": True}),
            ]
        )
        transport = KeeperHubHttpTransport(API_KEY, opener=opener)

        transport.get_wallet_readiness()
        transport.post_transfer(
            {
                "chainId": 84532,
                "recipientAddress": "0x" + "11" * 20,
                "amount": "0.000001",
                "tokenAddress": "0x" + "22" * 20,
                "simulate": True,
            },
            idempotency_key=None,
        )

        self.assertEqual(len(opener.calls), 2)
        for request, _ in opener.calls:
            self.assertEqual(header(request, "User-Agent"), USER_AGENT)
            self.assertNotIn("Python-urllib", header(request, "User-Agent") or "")

    def test_both_key_identity_probes_set_explicit_user_agent(self) -> None:
        key_opener = CapturingOpener([Response(200, [])])
        KEY_PROBE._one_get(
            API_KEY,
            "nv-key-identity-user-agent-test",
            opener=key_opener,
        )

        surface_key = "kh_abcde" + "1" * 27
        surface_opener = CapturingOpener([Response(200, {"items": []})])
        SURFACE_PROBE.one_get(
            surface_key,
            "nv-key-surface-user-agent-test",
            opener=surface_opener,
        )

        for opener in (key_opener, surface_opener):
            request, _ = opener.calls[0]
            self.assertEqual(header(request, "User-Agent"), USER_AGENT)
            self.assertNotIn("Python-urllib", header(request, "User-Agent") or "")

    def test_mcp_http_client_forces_canonical_user_agent(self) -> None:
        opener = CapturingOpener([Response(200, {})])
        client = MCP_RUNTIME.UrllibHttpClient()
        client._opener = opener

        result = client.request(
            "GET",
            MCP_RUNTIME.AUTH_METADATA_URL,
            headers={"user-agent": "Python-urllib/3.14"},
            stage="OAUTH_DISCOVERY",
            support_request_id="nv-mcp-user-agent-test",
        )

        self.assertEqual(result.status, 200)
        request, _ = opener.calls[0]
        self.assertEqual(header(request, "User-Agent"), USER_AGENT)
        self.assertNotIn("Python-urllib", header(request, "User-Agent") or "")

    def test_all_direct_python_keeperhub_request_sites_use_canonical_user_agent(self) -> None:
        roots = (ROOT / "src", ROOT / "tools")
        direct_sites: set[str] = set()
        missing: list[str] = []

        for root in roots:
            for path in root.rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                if "app.keeperhub.com" not in source:
                    continue
                tree = ast.parse(source, filename=str(path))
                has_direct_request = any(
                    isinstance(node, ast.Call)
                    and (
                        isinstance(node.func, ast.Name)
                        and node.func.id in {"Request", "urlopen"}
                        or isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"request", "urlopen"}
                    )
                    for node in ast.walk(tree)
                )
                if not has_direct_request:
                    continue
                relative = path.relative_to(ROOT).as_posix()
                direct_sites.add(relative)
                if USER_AGENT not in source or "User-Agent" not in source:
                    missing.append(relative)

        expected = {
            "src/nexus_vector/integrations/keeperhub_http_transport.py",
            "tools/_keeperhub_mcp_oauth_runtime.py",
            "tools/keeperhub_key_identity_probe.py",
            "tools/keeperhub_key_identity_surface_probe.py",
        }
        self.assertTrue(expected.issubset(direct_sites), direct_sites)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
