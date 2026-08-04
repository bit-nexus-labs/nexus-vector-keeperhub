from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parents[1]
TOOLS = ROOT / "tools"
MODULE_PATH = TOOLS / "_keeperhub_mcp_oauth_flow.py"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("probe", MODULE_PATH)
assert SPEC and SPEC.loader
P = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = P
SPEC.loader.exec_module(P)


class FakeAuthorizer:
    def __init__(self, result_factory=None):
        self._redirect_uri = "http://127.0.0.1:54321/callback"
        self.result_factory = result_factory
        self.urls = []
        self.closed = False

    @property
    def redirect_uri(self):
        return self._redirect_uri

    def authorize(self, authorization_url, *, expected_state, timeout_seconds):
        self.urls.append(authorization_url)
        if self.result_factory:
            return self.result_factory(expected_state)
        return P.AuthorizationResult(code="authcode123", state=expected_state)

    def close(self):
        self.closed = True


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(
        self,
        method,
        url,
        *,
        headers=None,
        body=None,
        timeout=15.0,
        stage,
        support_request_id,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "stage": stage,
                "support_request_id": support_request_id,
            }
        )
        if not self.responses:
            raise AssertionError("UNEXPECTED_REQUEST")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def jr(payload, status=200, headers=None):
    raw = json.dumps(payload).encode()
    return P.HttpResult(
        status,
        {"Content-Type": "application/json", **(headers or {})},
        raw,
        "APPLICATION_JSON",
    )


def empty(status=204, headers=None):
    return P.HttpResult(status, dict(headers or {}), b"", "EMPTY_RESPONSE")


def base_responses(token_scope="mcp:read", init_status=200, tools_status=200):
    resource = jr(
        {
            "resource": P.BASE_URL,
            "authorization_servers": [P.BASE_URL],
            "scopes_supported": ["mcp:read", "mcp:write", "mcp:admin"],
        }
    )
    auth = jr(
        {
            "issuer": P.BASE_URL,
            "authorization_endpoint": P.AUTHORIZE_URL,
            "token_endpoint": P.TOKEN_URL,
            "registration_endpoint": P.REGISTER_URL,
            "scopes_supported": ["mcp:read", "mcp:write", "mcp:admin"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
        }
    )
    registration = jr(
        {
            "client_id": "client-123",
            "token_endpoint_auth_method": "none",
            "scope": "mcp:read",
            "redirect_uris": ["http://127.0.0.1:54321/callback"],
        },
        status=201,
    )
    token = jr(
        {
            "access_token": "access-token-123",
            "refresh_token": "refresh-token-123",
            "token_type": "Bearer",
            "scope": token_scope,
            "expires_in": 3600,
        }
    )
    initialize = (
        jr(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": P.PROTOCOL_VERSION,
                    "capabilities": {},
                    "serverInfo": {"name": "keeperhub", "version": "1.2.0"},
                },
            },
            status=init_status,
            headers={"Mcp-Session-Id": "session-123"},
        )
        if init_status == 200
        else jr({"error": "forbidden"}, status=init_status)
    )
    notify = empty(204)
    tools = jr(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "list_workflows",
                        "description": "x",
                        "inputSchema": {},
                    }
                ]
            },
        },
        status=tools_status,
    )
    delete = empty(204)
    return [resource, auth, registration, token, initialize, notify, tools, delete]


class ProbeTests(unittest.TestCase):
    def test_full_success_is_bounded_and_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            http = FakeHttp(base_responses())
            authorizer = FakeAuthorizer()
            code, result = P.run_probe(
                http=http,
                authorizer=authorizer,
                state_path=state,
            )
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["scope_granted"], "mcp:read")
            self.assertEqual(result["listed_tool_count"], 1)
            self.assertEqual(result["session_cleanup"], "PASS")
            self.assertEqual(
                result["scripted_requests"],
                {
                    "discovery_gets": 2,
                    "registration_posts": 1,
                    "token_posts": 1,
                    "mcp_posts": 3,
                    "mcp_deletes": 1,
                },
            )
            self.assertTrue(result["oauth_client_record_created"])
            self.assertEqual(
                [call["method"] for call in http.calls],
                ["GET", "GET", "POST", "POST", "POST", "POST", "POST", "DELETE"],
            )
            self.assertTrue(authorizer.closed)
            auth_query = parse_qs(urlparse(authorizer.urls[0]).query)
            self.assertEqual(auth_query["scope"], ["mcp:read"])
            serialized = json.dumps(result)
            for secret in [
                "client-123",
                "authcode123",
                "access-token-123",
                "refresh-token-123",
                "session-123",
            ]:
                self.assertNotIn(secret, serialized)
            self.assertTrue(state.is_file())
            persisted = json.loads(state.read_text())
            self.assertEqual(persisted["state"], "TERMINAL")
            self.assertFalse(persisted["funds_moved"])

            source_paths = [
                MODULE_PATH,
                TOOLS / "_keeperhub_mcp_oauth_plan.py",
                TOOLS / "_keeperhub_mcp_oauth_runtime.py",
                TOOLS / "keeperhub_mcp_oauth_read_probe.py",
            ]
            source = "".join(path.read_text() for path in source_paths)
            self.assertNotIn('"method": "tools/call"', source)
            self.assertNotIn("/api/execute", source)
            self.assertNotIn("Idempotency-Key", source)
            self.assertNotIn("--approve-testnet-write", source)
            self.assertIn('"method": "tools/list"', source)
            self.assertIn('"method": "notifications/initialized"', source)

    def test_broader_granted_scope_stops_before_mcp(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            responses = base_responses(token_scope="mcp:read mcp:write")[:4]
            http = FakeHttp(responses)
            code, result = P.run_probe(
                http=http,
                authorizer=FakeAuthorizer(),
                state_path=state,
            )
            self.assertEqual(code, 2)
            self.assertEqual(result["reason"], "UNEXPECTED_GRANTED_SCOPE")
            self.assertEqual(result["scripted_requests"]["mcp_posts"], 0)
            self.assertEqual(len(http.calls), 4)

    def test_consent_timeout_stops_after_registration(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            http = FakeHttp(base_responses()[:3])
            auth = FakeAuthorizer(lambda state: P.AuthorizationResult(timed_out=True))
            code, result = P.run_probe(
                http=http,
                authorizer=auth,
                state_path=state,
            )
            self.assertEqual(code, 2)
            self.assertEqual(result["reason"], "CONSENT_TIMEOUT_OR_UI_BLOCK")
            self.assertEqual(result["scripted_requests"]["registration_posts"], 1)
            self.assertEqual(result["scripted_requests"]["token_posts"], 0)

    def test_oauth_ui_anonymous_block_is_terminal_before_token_exchange(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            http = FakeHttp(base_responses()[:3])
            auth = FakeAuthorizer(
                lambda state: P.AuthorizationResult(
                    error="anonymous_ui_block",
                    state=state,
                )
            )
            code, result = P.run_probe(
                http=http,
                authorizer=auth,
                state_path=state,
            )
            self.assertEqual(code, 2)
            self.assertEqual(
                result["reason"],
                "ACCOUNT_CLASSIFIED_AS_ANONYMOUS_BY_OAUTH_UI",
            )
            self.assertEqual(result["scripted_requests"]["token_posts"], 0)
            self.assertEqual(result["scripted_requests"]["mcp_posts"], 0)

    def test_mcp_init_403_is_application_level_and_no_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            responses = base_responses(init_status=403)[:5]
            http = FakeHttp(responses)
            code, result = P.run_probe(
                http=http,
                authorizer=FakeAuthorizer(),
                state_path=state,
            )
            self.assertEqual(code, 2)
            self.assertEqual(result["stage"], "MCP_INITIALIZE")
            self.assertEqual(result["http_status"], 403)
            self.assertEqual(result["response_surface"], "APPLICATION_JSON")
            self.assertEqual(result["scripted_requests"]["mcp_deletes"], 0)

    def test_failure_after_session_creation_cleans_up_once(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            responses = base_responses(tools_status=403)
            http = FakeHttp(responses)
            code, result = P.run_probe(
                http=http,
                authorizer=FakeAuthorizer(),
                state_path=state,
            )
            self.assertEqual(code, 2)
            self.assertEqual(result["stage"], "MCP_TOOLS_LIST")
            self.assertEqual(result["session_cleanup"], "PASS")
            self.assertEqual(result["scripted_requests"]["mcp_deletes"], 1)
            self.assertEqual(
                [call["method"] for call in http.calls].count("DELETE"),
                1,
            )

    def test_network_budget_blocks_duplicate_stage_before_second_request(self):
        counts = {
            "discovery_gets": 0,
            "registration_posts": 0,
            "token_posts": 0,
            "mcp_posts": 0,
            "mcp_deletes": 0,
        }
        budget = P._NetworkBudget(counts)
        budget.consume("RESOURCE_DISCOVERY", "GET", P.RESOURCE_METADATA_URL)

        with self.assertRaises(P.ProbeError) as caught:
            budget.consume("RESOURCE_DISCOVERY", "GET", P.RESOURCE_METADATA_URL)

        self.assertEqual(caught.exception.code, "NETWORK_STAGE_ALREADY_CONSUMED")
        self.assertEqual(counts["discovery_gets"], 1)

    def test_network_budget_rejects_tools_call_body_before_network(self):
        counts = {
            "discovery_gets": 0,
            "registration_posts": 0,
            "token_posts": 0,
            "mcp_posts": 0,
            "mcp_deletes": 0,
        }
        budget = P._NetworkBudget(counts)
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "execute_transfer"},
            }
        ).encode()

        with self.assertRaises(P.ProbeError) as caught:
            budget.consume(
                "MCP_TOOLS_LIST",
                "POST",
                P.MCP_URL,
                body=body,
            )

        self.assertEqual(caught.exception.code, "MCP_METHOD_PLAN_MISMATCH")
        self.assertEqual(counts["mcp_posts"], 0)

    def test_invalid_callback_timeout_stops_before_claim_and_network(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            http = FakeHttp([])
            code, result = P.run_probe(
                http=http,
                authorizer=FakeAuthorizer(),
                callback_timeout_seconds=0,
                state_path=state,
            )
            self.assertEqual(code, 2)
            self.assertEqual(result["reason"], "INVALID_LOCAL_CALLBACK_TIMEOUT")
            self.assertEqual(len(http.calls), 0)
            self.assertFalse(state.exists())

    def test_existing_claim_blocks_all_network(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            state.write_text('{"state":"CLAIMED"}')
            http = FakeHttp([])
            code, result = P.run_probe(
                http=http,
                authorizer=FakeAuthorizer(),
                state_path=state,
            )
            self.assertEqual(code, 2)
            self.assertEqual(result["reason"], "DURABLE_CLAIM_ALREADY_EXISTS")
            self.assertEqual(len(http.calls), 0)
            self.assertEqual(json.loads(state.read_text())["state"], "CLAIMED")


if __name__ == "__main__":
    unittest.main()
