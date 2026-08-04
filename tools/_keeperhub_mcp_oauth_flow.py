"""One-shot KeeperHub OAuth and MCP read-only flow orchestration."""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from _keeperhub_mcp_oauth_plan import (
    DEFAULT_STATE_PATH,
    _NetworkBudget,
    _base_result,
    _create_claim,
    _finalize_claim,
)
from _keeperhub_mcp_oauth_runtime import (
    AUTH_METADATA_URL,
    AUTHORIZE_URL,
    BASE_URL,
    CALLBACK_TIMEOUT_SECONDS,
    MAX_SECRET_CHARS,
    MCP_URL,
    PROBE,
    PROTOCOL_VERSION,
    REGISTER_URL,
    RESOURCE_METADATA_URL,
    SCOPE,
    TOKEN_URL,
    EXPECTED_ENDPOINTS,
    AuthorizationResult,
    Authorizer,
    HttpClient,
    HttpResult,
    LoopbackAuthorizer,
    ProbeError,
    UrllibHttpClient,
    _build_authorization_url,
    _decode_json,
    _expect_status,
    _header,
    _json_rpc_result,
    _parse_scope,
    _pkce_pair,
    _request_id,
    _validate_printable_secret,
    _validate_same_origin_endpoint,
    _validate_session_id,
)


def run_probe(
    *,
    http: HttpClient | None = None,
    authorizer: Authorizer | None = None,
    callback_timeout_seconds: float = CALLBACK_TIMEOUT_SECONDS,
    state_path: Path = DEFAULT_STATE_PATH,
) -> tuple[int, dict[str, Any]]:
    """Run one bounded OAuth + MCP read-only handshake."""

    client = http or UrllibHttpClient()
    local_authorizer = authorizer or LoopbackAuthorizer()
    run_id = f"nv-mcp-read-{uuid.uuid4()}"
    counts = {
        "discovery_gets": 0,
        "registration_posts": 0,
        "token_posts": 0,
        "mcp_posts": 0,
        "mcp_deletes": 0,
    }
    budget = _NetworkBudget(counts)
    stage = "LOCAL_PREFLIGHT"
    client_id: str | None = None
    authorization_code: str | None = None
    code_verifier: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    session_id: str | None = None
    session_cleanup = "NOT_CREATED"
    cleanup_attempted = False
    client_registration = "NOT_CREATED"
    result: dict[str, Any] | None = None
    exit_code = 2
    claim_created = False

    def request(
        method: str,
        url: str,
        *,
        stage_name: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        expected: set[int] = {200},
    ) -> tuple[HttpResult, Any, str]:
        budget.consume(stage_name, method, url, body=body)
        support_id = _request_id(run_id, stage_name)
        response = client.request(
            method,
            url,
            headers=headers,
            body=body,
            stage=stage_name,
            support_request_id=support_id,
        )
        _expect_status(response, expected, stage=stage_name, support_id=support_id)
        payload = _decode_json(response, stage=stage_name, support_id=support_id)
        return response, payload, support_id

    def cleanup_session_once() -> str:
        nonlocal cleanup_attempted, session_id
        if session_id is None or access_token is None:
            return session_cleanup
        if cleanup_attempted:
            return session_cleanup
        cleanup_attempted = True
        budget.consume("MCP_SESSION_CLEANUP", "DELETE", MCP_URL)
        support_id = _request_id(run_id, "MCP_SESSION_CLEANUP")
        try:
            response = client.request(
                "DELETE",
                MCP_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Mcp-Session-Id": session_id,
                    "Accept": "application/json, text/event-stream",
                    "Mcp-Protocol-Version": PROTOCOL_VERSION,
                },
                stage="MCP_SESSION_CLEANUP",
                support_request_id=support_id,
            )
        except ProbeError:
            return "UNKNOWN"
        if response.status == 204:
            session_id = None
            return "PASS"
        return "FAILED"

    try:
        if (
            isinstance(callback_timeout_seconds, bool)
            or not isinstance(callback_timeout_seconds, (int, float))
            or not 1.0 <= float(callback_timeout_seconds) <= 600.0
        ):
            raise ProbeError(
                "INVALID_LOCAL_CALLBACK_TIMEOUT",
                stage="LOCAL_PREFLIGHT",
            )
        _create_claim(state_path, run_id)
        claim_created = True

        stage = "RESOURCE_DISCOVERY"
        _, resource_meta, _ = request(
            "GET",
            RESOURCE_METADATA_URL,
            stage_name=stage,
        )
        if not isinstance(resource_meta, Mapping):
            raise ProbeError("INVALID_RESOURCE_METADATA", stage=stage)
        if resource_meta.get("resource") != BASE_URL:
            raise ProbeError("INVALID_RESOURCE_METADATA", stage=stage)
        servers = resource_meta.get("authorization_servers")
        if not isinstance(servers, list) or BASE_URL not in servers:
            raise ProbeError("INVALID_AUTHORIZATION_SERVER_LIST", stage=stage)
        if SCOPE not in (resource_meta.get("scopes_supported") or []):
            raise ProbeError("READ_SCOPE_NOT_ADVERTISED", stage=stage)

        stage = "OAUTH_DISCOVERY"
        _, auth_meta, _ = request(
            "GET",
            AUTH_METADATA_URL,
            stage_name=stage,
        )
        if not isinstance(auth_meta, Mapping):
            raise ProbeError("INVALID_AUTHORIZATION_METADATA", stage=stage)
        if auth_meta.get("issuer") != BASE_URL:
            raise ProbeError("INVALID_OAUTH_ISSUER", stage=stage)
        for field, expected in EXPECTED_ENDPOINTS.items():
            _validate_same_origin_endpoint(auth_meta.get(field), expected, field=field)
        if "S256" not in (auth_meta.get("code_challenge_methods_supported") or []):
            raise ProbeError("PKCE_S256_NOT_SUPPORTED", stage=stage)
        if "none" not in (
            auth_meta.get("token_endpoint_auth_methods_supported") or []
        ):
            raise ProbeError("PUBLIC_PKCE_CLIENT_NOT_SUPPORTED", stage=stage)
        if SCOPE not in (auth_meta.get("scopes_supported") or []):
            raise ProbeError("READ_SCOPE_NOT_SUPPORTED", stage=stage)

        stage = "CLIENT_REGISTRATION"
        registration_body = json.dumps(
            {
                "client_name": "Nexus Vector Read-Only Diagnostic",
                "redirect_uris": [local_authorizer.redirect_uri],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": SCOPE,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        _, registration, _ = request(
            "POST",
            REGISTER_URL,
            stage_name=stage,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body=registration_body,
            expected={201},
        )
        if not isinstance(registration, Mapping):
            raise ProbeError("INVALID_CLIENT_REGISTRATION", stage=stage)
        if registration.get("token_endpoint_auth_method") != "none":
            raise ProbeError("UNEXPECTED_CLIENT_AUTH_METHOD", stage=stage)
        if _parse_scope(registration.get("scope")) != {SCOPE}:
            raise ProbeError("UNEXPECTED_REGISTERED_SCOPE", stage=stage)
        if registration.get("redirect_uris") != [local_authorizer.redirect_uri]:
            raise ProbeError("REDIRECT_URI_REGISTRATION_MISMATCH", stage=stage)
        if "client_secret" in registration:
            raise ProbeError("UNEXPECTED_PUBLIC_CLIENT_SECRET", stage=stage)
        client_id = _validate_printable_secret(
            registration.get("client_id"),
            code="INVALID_CLIENT_ID",
            stage=stage,
            max_chars=512,
        )
        client_registration = "CREATED"

        stage = "USER_CONSENT"
        state = secrets.token_urlsafe(32)
        code_verifier, challenge = _pkce_pair()
        authorization_url = _build_authorization_url(
            client_id=client_id,
            redirect_uri=local_authorizer.redirect_uri,
            state=state,
            challenge=challenge,
        )
        auth_result = local_authorizer.authorize(
            authorization_url,
            expected_state=state,
            timeout_seconds=callback_timeout_seconds,
        )
        if auth_result.timed_out:
            raise ProbeError("CONSENT_TIMEOUT_OR_UI_BLOCK", stage=stage)
        if auth_result.error is not None:
            if auth_result.error == "state_mismatch":
                raise ProbeError("OAUTH_STATE_MISMATCH", stage=stage)
            if auth_result.error == "anonymous_ui_block":
                raise ProbeError(
                    "ACCOUNT_CLASSIFIED_AS_ANONYMOUS_BY_OAUTH_UI",
                    stage=stage,
                )
            raise ProbeError("CONSENT_DENIED_OR_REJECTED", stage=stage)
        authorization_code = _validate_printable_secret(
            auth_result.code,
            code="INVALID_AUTHORIZATION_CODE",
            stage=stage,
            max_chars=MAX_SECRET_CHARS,
        )

        stage = "TOKEN_EXCHANGE"
        token_body = urlencode(
            {
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": local_authorizer.redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            }
        ).encode("ascii")
        _, token_payload, _ = request(
            "POST",
            TOKEN_URL,
            stage_name=stage,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            body=token_body,
        )
        if not isinstance(token_payload, Mapping):
            raise ProbeError("INVALID_TOKEN_RESPONSE", stage=stage)
        if str(token_payload.get("token_type", "")).casefold() != "bearer":
            raise ProbeError("INVALID_TOKEN_TYPE", stage=stage)
        granted_scope = _parse_scope(token_payload.get("scope"))
        if granted_scope != {SCOPE}:
            raise ProbeError("UNEXPECTED_GRANTED_SCOPE", stage=stage)
        access_token = _validate_printable_secret(
            token_payload.get("access_token"),
            code="INVALID_ACCESS_TOKEN",
            stage=stage,
            max_chars=MAX_SECRET_CHARS,
        )
        refresh_value = token_payload.get("refresh_token")
        if refresh_value is not None:
            refresh_token = _validate_printable_secret(
                refresh_value,
                code="INVALID_REFRESH_TOKEN",
                stage=stage,
                max_chars=MAX_SECRET_CHARS,
            )

        common_mcp_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Protocol-Version": PROTOCOL_VERSION,
        }

        stage = "MCP_INITIALIZE"
        initialize_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "nexus-vector-read-probe",
                        "version": "1.0.0",
                    },
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        init_response, init_payload, _ = request(
            "POST",
            MCP_URL,
            stage_name=stage,
            headers=common_mcp_headers,
            body=initialize_body,
        )
        init_rpc = _json_rpc_result(init_payload, 1, stage=stage)
        if init_rpc.get("protocolVersion") != PROTOCOL_VERSION:
            raise ProbeError("UNEXPECTED_MCP_PROTOCOL_VERSION", stage=stage)
        session_id = _validate_session_id(
            _header(init_response.headers, "mcp-session-id")
        )
        session_cleanup = "PENDING"

        session_headers = dict(common_mcp_headers)
        session_headers["Mcp-Session-Id"] = session_id

        stage = "MCP_INITIALIZED_NOTIFICATION"
        notification_body = json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            separators=(",", ":"),
        ).encode("utf-8")
        budget.consume(stage, "POST", MCP_URL, body=notification_body)
        notification_support_id = _request_id(run_id, stage)
        notification_response = client.request(
            "POST",
            MCP_URL,
            headers=session_headers,
            body=notification_body,
            stage=stage,
            support_request_id=notification_support_id,
        )
        if notification_response.status not in {200, 202, 204}:
            raise ProbeError(
                "INITIALIZED_NOTIFICATION_REJECTED",
                stage=stage,
                http_status=notification_response.status,
                response_surface=notification_response.surface,
                support_request_id=notification_support_id,
            )

        stage = "MCP_TOOLS_LIST"
        tools_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        _, tools_payload, _ = request(
            "POST",
            MCP_URL,
            stage_name=stage,
            headers=session_headers,
            body=tools_body,
        )
        tools_rpc = _json_rpc_result(tools_payload, 2, stage=stage)
        tools = tools_rpc.get("tools")
        if not isinstance(tools, list) or not tools:
            raise ProbeError("INVALID_TOOLS_LIST", stage=stage)
        for item in tools:
            if not isinstance(item, Mapping) or not isinstance(
                item.get("name"), str
            ):
                raise ProbeError("INVALID_TOOLS_LIST", stage=stage)
        tool_count = len(tools)

        stage = "MCP_SESSION_CLEANUP"
        session_cleanup = cleanup_session_once()
        if session_cleanup != "PASS":
            raise ProbeError("MCP_SESSION_CLEANUP_FAILED", stage=stage)

        result = _base_result(run_id, counts)
        result.update(
            {
                "status": "PASS",
                "reason": "MCP_OAUTH_READ_HANDSHAKE_SUCCEEDED",
                "stage": "COMPLETE",
                "oauth_discovery": "PASS",
                "client_registration": client_registration,
                "oauth_client_record_created": client_registration == "CREATED",
                "consent": "APPROVED",
                "scope_granted": SCOPE,
                "mcp_initialize": "PASS",
                "tools_list": "PASS",
                "listed_tool_count": tool_count,
                "session_cleanup": session_cleanup,
                "refresh_token_received_and_discarded": refresh_token is not None,
                "retry": "NOT_REQUIRED",
            }
        )
        exit_code = 0

    except ProbeError as error:
        if session_id is not None and not cleanup_attempted:
            session_cleanup = cleanup_session_once()
        result = _base_result(run_id, counts)
        result.update(
            {
                "status": "OUTCOME_UNKNOWN" if error.outcome_unknown else "STOP",
                "reason": error.code,
                "stage": error.stage,
                "client_registration": client_registration,
                "oauth_client_record_created": client_registration == "CREATED",
                "session_cleanup": session_cleanup,
                "retry": "FORBIDDEN_WITHOUT_MANUAL_RECOVERY",
            }
        )
        if error.http_status is not None:
            result["http_status"] = error.http_status
        if error.response_surface is not None:
            result["response_surface"] = error.response_surface
        if error.support_request_id is not None:
            result["support_request_id"] = error.support_request_id
        exit_code = 2

    finally:
        try:
            local_authorizer.close()
        except OSError:
            pass
        client_id = None
        authorization_code = None
        code_verifier = None
        access_token = None
        refresh_token = None
        session_id = None

    assert result is not None
    if not claim_created:
        return exit_code, result
    try:
        _finalize_claim(state_path, result)
    except ProbeError as state_error:
        failed = _base_result(run_id, counts)
        failed.update(
            {
                "status": "STOP",
                "reason": state_error.code,
                "stage": state_error.stage,
                "client_registration": client_registration,
                "oauth_client_record_created": client_registration == "CREATED",
                "session_cleanup": session_cleanup,
                "retry": "MANUAL_LOCAL_RECOVERY_REQUIRED",
            }
        )
        return 2, failed
    return exit_code, result
