"""One-shot, GET-only KeeperHub organisation-key surface probe.

This diagnostic performs exactly one ``GET /api/keys`` request. It has no POST,
simulation, signing, broadcast, workflow, MCP, marketplace, or funds-moving
capability. Secrets and response bodies are never printed.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROBE = "KEEPERHUB_KEY_IDENTITY_SURFACE_V1"
API_KEY_ENV = "KEEPERHUB_API_KEY"
URL = "https://app.keeperhub.com/api/keys"
USER_AGENT = "NexusVector-KeeperHub/1.0"
MAX_BYTES = 262_144
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
PREFIX_RE = re.compile(r"kh_[A-Za-z0-9_-]{5}")
ALLOWED_ERROR_CODES = frozenset({"insufficient_scope"})


class ProbeError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        request_performed: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        self.code = code
        self.request_performed = request_performed
        self.outcome_unknown = outcome_unknown
        super().__init__(code)


def performed(error: ProbeError) -> ProbeError:
    if error.request_performed:
        return error
    return ProbeError(
        error.code,
        request_performed=True,
        outcome_unknown=error.outcome_unknown,
    )


class Opener(Protocol):
    def open(self, request: Request, *, timeout: float): ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError("HTTP_REDIRECT_BLOCKED", request_performed=True)


def validate_key(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("kh_")
        or len(value) <= 8
        or len(value) > 512
        or not value.isascii()
        or value.strip() != value
        or any(
            not (character.isalnum() or character in {"_", "-"})
            for character in value
        )
    ):
        raise ProbeError("INVALID_LOCAL_API_KEY")
    return value


def validate_request_id(value: Any) -> str:
    if not isinstance(value, str) or REQUEST_ID_RE.fullmatch(value) is None:
        raise ProbeError("INVALID_LOCAL_REQUEST_ID")
    return value


def headers_dict(value: Any) -> dict[str, str]:
    try:
        return {str(k): str(v) for k, v in value.items()}
    except (AttributeError, TypeError, ValueError):
        raise ProbeError("INVALID_HTTP_RESPONSE") from None


def header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value
    return None


def classify_surface(raw: bytes, headers: Mapping[str, str]) -> str:
    if not raw.strip():
        return "EMPTY_RESPONSE"

    content_type = (header(headers, "content-type") or "").casefold()
    if content_type.startswith("application/json"):
        return "APPLICATION_JSON"

    html = "text/html" in content_type or raw.lstrip()[:64].lower().startswith(
        (b"<!doctype html", b"<html")
    )
    if html:
        server = (header(headers, "server") or "").casefold()
        sample = raw[:16_384].lower()
        cloudflare = (
            "cloudflare" in server
            or header(headers, "cf-ray") is not None
            or header(headers, "cf-cache-status") is not None
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


def decode_response(response: Any) -> tuple[int, Any, dict[str, str], str]:
    try:
        status = int(getattr(response, "status", response.getcode()))
    except (AttributeError, TypeError, ValueError):
        raise ProbeError("INVALID_HTTP_RESPONSE") from None

    headers = headers_dict(response.headers)
    raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ProbeError("RESPONSE_TOO_LARGE", request_performed=True)

    surface = classify_surface(raw, headers)
    if surface != "APPLICATION_JSON":
        return status, None, headers, surface

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProbeError("INVALID_JSON_RESPONSE", request_performed=True) from None
    return status, payload, headers, surface


def one_get(
    api_key: str,
    request_id: str,
    *,
    opener: Opener | None = None,
) -> tuple[int, Any, dict[str, str], str]:
    key = validate_key(api_key)
    correlation_id = validate_request_id(request_id)
    client = opener or build_opener(NoRedirect())
    request = Request(
        URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": USER_AGENT,
            "X-Request-ID": correlation_id,
        },
        method="GET",
    )
    try:
        response = client.open(request, timeout=10.0)
        try:
            try:
                return decode_response(response)
            except ProbeError as error:
                raise performed(error) from None
        finally:
            response.close()
    except HTTPError as error:
        try:
            try:
                return decode_response(error)
            except ProbeError as decode_error:
                raise performed(decode_error) from None
        finally:
            error.close()
    except ProbeError:
        raise
    except (TimeoutError, URLError, OSError):
        raise ProbeError(
            "NETWORK_OUTCOME_UNKNOWN",
            request_performed=True,
            outcome_unknown=True,
        ) from None


def request_id_reflection(
    request_id: str,
    payload: Any,
    headers: Mapping[str, str],
) -> str:
    values: list[str] = []
    reflected_header = header(headers, "x-request-id")
    if reflected_header is not None:
        values.append(reflected_header)
    if isinstance(payload, Mapping) and isinstance(payload.get("request_id"), str):
        values.append(payload["request_id"])

    if not values:
        return "NOT_PRESENT"
    for value in values:
        if REQUEST_ID_RE.fullmatch(value) is None:
            return "INVALID"
        if value != request_id:
            return "MISMATCH"
    return "MATCH"


def safe_error_code(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("error")
    return value if isinstance(value, str) and value in ALLOWED_ERROR_CODES else None


def key_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
        return payload["items"]
    raise ProbeError("INVALID_KEYS_RESPONSE", request_performed=True)


def result_base(
    request_id: str,
    status: int,
    surface: str,
    reflection: str,
) -> dict[str, Any]:
    return {
        "probe": PROBE,
        "endpoint": "GET /api/keys",
        "get_requests": 1,
        "post_requests": 0,
        "simulation_posts": 0,
        "broadcast_posts": 0,
        "funds_moved": False,
        "http_status": status,
        "response_surface": surface,
        "request_id_reflection": reflection,
        "support_request_id": request_id,
    }


def run(
    api_key: str,
    *,
    opener: Opener | None = None,
    request_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    key = validate_key(api_key)
    correlation_id = validate_request_id(
        request_id or f"nv-key-surface-{uuid.uuid4()}"
    )
    status, payload, headers, surface = one_get(
        key,
        correlation_id,
        opener=opener,
    )
    reflection = request_id_reflection(correlation_id, payload, headers)
    result = result_base(correlation_id, status, surface, reflection)

    if reflection in {"INVALID", "MISMATCH"}:
        result.update(
            status="STOP",
            reason="REQUEST_ID_REFLECTION_INVALID",
            retry="MANUAL_PROVIDER_REVIEW_REQUIRED",
        )
        return 2, result

    if status != 200:
        result.update(
            status="STOP",
            reason=(
                "CLOUDFLARE_EDGE_HTTP_REJECTED"
                if surface == "CLOUDFLARE_HTML"
                else "ORGANIZATION_KEY_IDENTITY_HTTP_REJECTED"
            ),
            retry="REVIEW_BEFORE_REPEAT",
        )
        code = safe_error_code(payload)
        if code is not None:
            result["provider_error_code"] = code
        return 2, result

    if surface != "APPLICATION_JSON":
        result.update(
            status="STOP",
            reason="UNEXPECTED_SUCCESS_RESPONSE_SURFACE",
            retry="MANUAL_PROVIDER_REVIEW_REQUIRED",
        )
        return 2, result

    matches = 0
    for item in key_items(payload):
        if not isinstance(item, Mapping):
            raise ProbeError("INVALID_KEYS_RESPONSE", request_performed=True)
        prefix = item.get("keyPrefix")
        if (
            not isinstance(prefix, str)
            or len(prefix) != 8
            or PREFIX_RE.fullmatch(prefix) is None
        ):
            raise ProbeError("INVALID_KEY_PREFIX", request_performed=True)
        if key.startswith(prefix):
            matches += 1

    if matches == 0:
        result.update(
            status="STOP",
            reason="KEY_NOT_VISIBLE_IN_ACTIVE_ORGANIZATION",
            organization_key_match="MISMATCH",
            retry="MANUAL_ORGANIZATION_REVIEW_REQUIRED",
        )
        return 2, result
    if matches > 1:
        result.update(
            status="STOP",
            reason="AMBIGUOUS_KEY_PREFIX_MATCH",
            organization_key_match="AMBIGUOUS",
            retry="MANUAL_ORGANIZATION_REVIEW_REQUIRED",
        )
        return 2, result

    result.update(
        status="PASS",
        reason="ORGANIZATION_KEY_VISIBLE_TO_BACKEND",
        organization_key_match="MATCH",
        retry="NOT_REQUIRED",
    )
    return 0, result


def error_result(error: ProbeError, request_id: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "probe": PROBE,
        "status": "OUTCOME_UNKNOWN" if error.outcome_unknown else "STOP",
        "reason": error.code,
        "get_requests": 1 if error.request_performed else 0,
        "post_requests": 0,
        "simulation_posts": 0,
        "broadcast_posts": 0,
        "funds_moved": False,
        "retry": (
            "REVIEW_BEFORE_REPEAT"
            if error.request_performed
            else "LOCAL_INPUT_CORRECTION_ALLOWED"
        ),
    }
    if error.request_performed and request_id is not None:
        result["support_request_id"] = request_id
    return result


def main() -> int:
    api_key = os.environ.pop(API_KEY_ENV, None)
    request_id: str | None = None
    try:
        key = validate_key(api_key)
        request_id = validate_request_id(f"nv-key-surface-{uuid.uuid4()}")
        exit_code, result = run(key, request_id=request_id)
    except ProbeError as error:
        exit_code = 2
        result = error_result(error, request_id)
    except Exception:
        exit_code = 2
        result = {
            "probe": PROBE,
            "status": "OUTCOME_UNKNOWN",
            "reason": "UNEXPECTED_PROBE_FAILURE",
            "get_requests": None,
            "maximum_get_requests": 1,
            "post_requests": 0,
            "simulation_posts": 0,
            "broadcast_posts": 0,
            "funds_moved": False,
            "retry": "MANUAL_REVIEW_REQUIRED",
        }
    finally:
        api_key = None

    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
