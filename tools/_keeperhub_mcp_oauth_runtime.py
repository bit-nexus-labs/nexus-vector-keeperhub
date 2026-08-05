"""Bounded read-only KeeperHub MCP OAuth handshake diagnostic.

The probe performs OAuth discovery, creates one public PKCE client scoped to
``mcp:read``, waits for interactive user consent, exchanges the one-time code,
initializes one MCP session, sends ``notifications/initialized``, lists tools,
and closes the MCP session.

It never calls ``tools/call`` and has no workflow execution, simulation,
signing, broadcast, x402, marketplace invocation, or funds-moving capability.
Tokens, authorization codes, session IDs, client IDs, response bodies, and
headers are never serialized to the final result or durable state.
"""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import re
import secrets
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROBE = "KEEPERHUB_MCP_OAUTH_READ_HANDSHAKE_V1"
BASE_URL = "https://app.keeperhub.com"
RESOURCE_METADATA_URL = f"{BASE_URL}/.well-known/oauth-protected-resource"
AUTH_METADATA_URL = f"{BASE_URL}/.well-known/oauth-authorization-server"
REGISTER_URL = f"{BASE_URL}/api/oauth/register"
AUTHORIZE_URL = f"{BASE_URL}/oauth/authorize"
TOKEN_URL = f"{BASE_URL}/api/oauth/token"
MCP_URL = f"{BASE_URL}/mcp"
USER_AGENT = "NexusVector-KeeperHub/1.0"
SCOPE = "mcp:read"
PROTOCOL_VERSION = "2025-06-18"
CALLBACK_PATH = "/callback"
CALLBACK_TIMEOUT_SECONDS = 300.0
MAX_RESPONSE_BYTES = 2_097_152
MAX_SECRET_CHARS = 16_384
MAX_SESSION_ID_CHARS = 16_384
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
SAFE_VALUE_RE = re.compile(r"[\x21-\x7e]+")
EXPECTED_ENDPOINTS = {
    "authorization_endpoint": AUTHORIZE_URL,
    "token_endpoint": TOKEN_URL,
    "registration_endpoint": REGISTER_URL,
}


class ProbeError(RuntimeError):
    """Sanitized, machine-classifiable diagnostic failure."""

    def __init__(
        self,
        code: str,
        *,
        stage: str,
        outcome_unknown: bool = False,
        http_status: int | None = None,
        response_surface: str | None = None,
        support_request_id: str | None = None,
    ) -> None:
        self.code = code
        self.stage = stage
        self.outcome_unknown = outcome_unknown
        self.http_status = http_status
        self.response_surface = response_surface
        self.support_request_id = support_request_id
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: dict[str, str]
    raw: bytes
    surface: str


@dataclass(frozen=True)
class AuthorizationResult:
    code: str | None = None
    error: str | None = None
    state: str | None = None
    timed_out: bool = False


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 15.0,
        stage: str,
        support_request_id: str,
    ) -> HttpResult: ...


class Authorizer(Protocol):
    @property
    def redirect_uri(self) -> str: ...

    def authorize(
        self,
        authorization_url: str,
        *,
        expected_state: str,
        timeout_seconds: float,
    ) -> AuthorizationResult: ...

    def close(self) -> None: ...


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value
    return None


def _headers_dict(value: Any) -> dict[str, str]:
    try:
        return {str(key): str(item) for key, item in value.items()}
    except (AttributeError, TypeError, ValueError):
        raise ProbeError("INVALID_HTTP_HEADERS", stage="HTTP_DECODE") from None


def _classify_surface(raw: bytes, headers: Mapping[str, str]) -> str:
    if not raw.strip():
        return "EMPTY_RESPONSE"
    content_type = (_header(headers, "content-type") or "").casefold()
    if content_type.startswith("application/json"):
        return "APPLICATION_JSON"
    html = "text/html" in content_type or raw.lstrip()[:64].lower().startswith(
        (b"<!doctype html", b"<html")
    )
    if html:
        server = (_header(headers, "server") or "").casefold()
        sample = raw[:16_384].lower()
        cloudflare = (
            "cloudflare" in server
            or _header(headers, "cf-ray") is not None
            or _header(headers, "cf-cache-status") is not None
            or any(
                marker in sample
                for marker in (
                    b"cloudflare",
                    b"just a moment",
                    b"attention required",
                    b"cf-chl-",
                )
            )
        )
        return "CLOUDFLARE_HTML" if cloudflare else "HTML_RESPONSE"
    return "OTHER_CONTENT"


class UrllibHttpClient:
    """No-retry, no-redirect HTTP client with bounded response reads."""

    def __init__(self) -> None:
        self._opener = build_opener(NoRedirect())

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 15.0,
        stage: str,
        support_request_id: str,
    ) -> HttpResult:
        request_headers = dict(headers or {})
        request_headers["User-Agent"] = USER_AGENT
        request_headers["X-Request-ID"] = support_request_id
        request = Request(
            url,
            headers=request_headers,
            data=body,
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        except (TimeoutError, URLError, OSError):
            raise ProbeError(
                "NETWORK_OUTCOME_UNKNOWN",
                stage=stage,
                outcome_unknown=True,
                support_request_id=support_request_id,
            ) from None

        try:
            try:
                status_value = getattr(response, "status", None)
                if status_value is None:
                    status_value = response.getcode()
                status = int(status_value)
                response_headers = _headers_dict(response.headers)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            except ProbeError as error:
                raise ProbeError(
                    error.code,
                    stage=stage,
                    support_request_id=support_request_id,
                ) from None
            except (AttributeError, TypeError, ValueError, OSError):
                raise ProbeError(
                    "INVALID_HTTP_RESPONSE",
                    stage=stage,
                    support_request_id=support_request_id,
                ) from None
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ProbeError(
                    "RESPONSE_TOO_LARGE",
                    stage=stage,
                    http_status=status,
                    support_request_id=support_request_id,
                )
            return HttpResult(
                status=status,
                headers=response_headers,
                raw=raw,
                surface=_classify_surface(raw, response_headers),
            )
        finally:
            response.close()


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "NexusVectorLoopback/1.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        query = parse_qs(parsed.query, keep_blank_values=True)
        result = AuthorizationResult(
            code=_single_query_value(query, "code"),
            error=_single_query_value(query, "error"),
            state=_single_query_value(query, "state"),
        )
        setattr(self.server, "authorization_result", result)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(
            b"<!doctype html><html><body><h2>KeeperHub callback received.</h2>"
            b"<p>Return to the terminal. No credential is displayed here.</p>"
            b"</body></html>"
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def _single_query_value(query: Mapping[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values or len(values) != 1:
        return None
    value = values[0]
    if not isinstance(value, str) or len(value) > MAX_SECRET_CHARS:
        return None
    return value


class LoopbackAuthorizer:
    """Interactive system-browser OAuth flow bound to 127.0.0.1 only."""

    def __init__(self) -> None:
        self._server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        self._server.timeout = 1.0
        setattr(self._server, "authorization_result", None)
        port = int(self._server.server_address[1])
        self._redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def authorize(
        self,
        authorization_url: str,
        *,
        expected_state: str,
        timeout_seconds: float,
    ) -> AuthorizationResult:
        print(
            "KeeperHub OAuth consent is opening in your browser. "
            "Leave ONLY Read checked; do not enable Write or Admin.",
            file=sys.stderr,
        )
        opened = webbrowser.open(authorization_url, new=1, autoraise=True)
        if not opened:
            print("Open this URL locally in your browser:", file=sys.stderr)
            print(authorization_url, file=sys.stderr)

        user_signals: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_user_signal() -> None:
            try:
                value = input(
                    "If KeeperHub shows 'Create an account to continue', "
                    "type A and press Enter. Otherwise approve and wait: "
                )
            except (EOFError, OSError):
                return
            try:
                user_signals.put_nowait(value.strip().casefold())
            except queue.Full:
                return

        threading.Thread(target=read_user_signal, daemon=True).start()

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self._server.handle_request()
            result = getattr(self._server, "authorization_result", None)
            if isinstance(result, AuthorizationResult):
                if result.state != expected_state:
                    return AuthorizationResult(
                        error="state_mismatch",
                        state=result.state,
                    )
                return result
            try:
                signal = user_signals.get_nowait()
            except queue.Empty:
                signal = ""
            if signal in {"a", "anonymous"}:
                return AuthorizationResult(
                    error="anonymous_ui_block",
                    state=expected_state,
                )
        return AuthorizationResult(timed_out=True)

    def close(self) -> None:
        self._server.server_close()


def _request_id(run_id: str, stage: str) -> str:
    value = f"{run_id}-{stage.lower()}"
    if REQUEST_ID_RE.fullmatch(value) is None:
        raise ProbeError("INVALID_LOCAL_REQUEST_ID", stage="LOCAL_PREFLIGHT")
    return value


def _decode_json(result: HttpResult, *, stage: str, support_id: str) -> Any:
    if result.surface != "APPLICATION_JSON":
        raise ProbeError(
            "UNEXPECTED_RESPONSE_SURFACE",
            stage=stage,
            http_status=result.status,
            response_surface=result.surface,
            support_request_id=support_id,
        )
    try:
        return json.loads(result.raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProbeError(
            "INVALID_JSON_RESPONSE",
            stage=stage,
            http_status=result.status,
            response_surface=result.surface,
            support_request_id=support_id,
        ) from None


def _expect_status(
    result: HttpResult,
    expected: set[int],
    *,
    stage: str,
    support_id: str,
) -> None:
    if result.status not in expected:
        raise ProbeError(
            "HTTP_REJECTED",
            stage=stage,
            http_status=result.status,
            response_surface=result.surface,
            support_request_id=support_id,
        )


def _validate_same_origin_endpoint(value: Any, expected: str, *, field: str) -> None:
    if not isinstance(value, str) or value != expected:
        raise ProbeError(f"INVALID_{field.upper()}", stage="OAUTH_DISCOVERY")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "app.keeperhub.com":
        raise ProbeError(
            f"INVALID_{field.upper()}_ORIGIN",
            stage="OAUTH_DISCOVERY",
        )


def _parse_scope(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {part for part in value.split() if part}


def _validate_printable_secret(
    value: Any,
    *,
    code: str,
    stage: str,
    max_chars: int,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > max_chars
        or SAFE_VALUE_RE.fullmatch(value) is None
    ):
        raise ProbeError(code, stage=stage)
    return value


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
) -> str:
    return AUTHORIZE_URL + "?" + urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


def _json_rpc_result(
    payload: Any,
    expected_id: int,
    *,
    stage: str,
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProbeError("INVALID_JSON_RPC_RESPONSE", stage=stage)
    if payload.get("jsonrpc") != "2.0" or payload.get("id") != expected_id:
        raise ProbeError("INVALID_JSON_RPC_RESPONSE", stage=stage)
    if "error" in payload:
        raise ProbeError("JSON_RPC_REJECTED", stage=stage)
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ProbeError("INVALID_JSON_RPC_RESULT", stage=stage)
    return result


def _validate_session_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_SESSION_ID_CHARS
        or SAFE_VALUE_RE.fullmatch(value) is None
    ):
        raise ProbeError("INVALID_MCP_SESSION_ID", stage="MCP_INITIALIZE")
    return value
