"""Run one bounded, sanitized KeeperHub organization-key identity probe.

The command performs exactly one read-only ``GET /api/keys`` request. It has
no transfer, simulation, signing, broadcast, Workflow, MCP, x402, Marketplace,
wallet-write, or mainnet execution capability.

The organization key is read once from ``KEEPERHUB_API_KEY`` and removed from
this process environment before the request. The full key, returned key
prefixes, key names, key IDs, and creator identity are never serialized.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

_PROBE = "KEEPERHUB_KEY_IDENTITY_V1"
_API_KEY_ENV = "KEEPERHUB_API_KEY"
_BASE_URL = "https://app.keeperhub.com/api"
_PATH = "/keys"
_MAX_RESPONSE_BYTES = 262_144
_MAX_API_KEY_LENGTH = 512
_MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_KEY_PREFIX_PATTERN = re.compile(r"kh_[A-Za-z0-9_-]{3,125}")
_PROVIDER_ERROR_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ALLOWED_PROVIDER_ERROR_CODES = frozenset({"insufficient_scope"})


class KeyIdentityProbeError(RuntimeError):
    """Machine-classifiable probe failure without secret or response echo."""

    def __init__(
        self,
        code: str,
        *,
        http_status: int | None = None,
        provider_error_code: str | None = None,
        outcome_unknown: bool = False,
        request_performed: bool = False,
    ) -> None:
        self.code = code
        self.http_status = http_status
        self.provider_error_code = provider_error_code
        self.outcome_unknown = outcome_unknown
        self.request_performed = request_performed
        super().__init__(code)


class _Opener(Protocol):
    def open(self, request: Request, *, timeout: float): ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise KeyIdentityProbeError("HTTP_REDIRECT_BLOCKED")


def _request_performed(error: KeyIdentityProbeError) -> KeyIdentityProbeError:
    if error.request_performed:
        return error
    return KeyIdentityProbeError(
        error.code,
        http_status=error.http_status,
        provider_error_code=error.provider_error_code,
        outcome_unknown=error.outcome_unknown,
        request_performed=True,
    )


def _validate_api_key(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("kh_")
        or len(value) <= 3
        or len(value) > _MAX_API_KEY_LENGTH
        or not value.isascii()
        or value.strip() != value
        or any(ord(character) <= 32 for character in value)
        or any(
            not (character.isalnum() or character in {"_", "-"})
            for character in value
        )
    ):
        raise KeyIdentityProbeError("INVALID_LOCAL_API_KEY")
    return value


def _validate_request_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_REQUEST_ID_LENGTH
        or _REQUEST_ID_PATTERN.fullmatch(value) is None
    ):
        raise KeyIdentityProbeError("INVALID_LOCAL_REQUEST_ID")
    return value


def _generated_request_id() -> str:
    return _validate_request_id(f"nv-key-identity-{uuid.uuid4()}")


def _headers_dict(headers: Any) -> dict[str, str]:
    try:
        return {str(key): str(value) for key, value in headers.items()}
    except (AttributeError, TypeError, ValueError):
        raise KeyIdentityProbeError("INVALID_HTTP_RESPONSE") from None


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if key.casefold() == target:
            return value
    return None


def _safe_provider_error_code(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    candidate = payload.get("error")
    if (
        isinstance(candidate, str)
        and _PROVIDER_ERROR_CODE_PATTERN.fullmatch(candidate) is not None
        and candidate in _ALLOWED_PROVIDER_ERROR_CODES
    ):
        return candidate
    return None


def _decode_response(response: Any) -> tuple[int, Any, dict[str, str]]:
    try:
        status_value = getattr(response, "status", None)
        if status_value is None:
            status_value = response.getcode()
        status = int(status_value)
    except (AttributeError, TypeError, ValueError):
        raise KeyIdentityProbeError("INVALID_HTTP_RESPONSE") from None

    headers = _headers_dict(response.headers)
    content_type = _header_value(headers, "content-type")
    if content_type is None or not content_type.casefold().startswith(
        "application/json"
    ):
        raise KeyIdentityProbeError("INVALID_RESPONSE_CONTENT_TYPE")

    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise KeyIdentityProbeError("RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise KeyIdentityProbeError("INVALID_JSON_RESPONSE") from None
    return status, payload, headers


def _one_get(
    api_key: str,
    request_id: str,
    *,
    opener: _Opener | None = None,
    timeout_seconds: float = 10.0,
) -> tuple[int, Any, dict[str, str]]:
    key = _validate_api_key(api_key)
    correlation_id = _validate_request_id(request_id)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < float(timeout_seconds) <= 60
    ):
        raise KeyIdentityProbeError("INVALID_LOCAL_TIMEOUT")

    client = opener or build_opener(_NoRedirectHandler())
    request = Request(
        _BASE_URL + _PATH,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "X-Request-ID": correlation_id,
        },
        method="GET",
    )
    try:
        response = client.open(request, timeout=float(timeout_seconds))
        try:
            try:
                return _decode_response(response)
            except KeyIdentityProbeError as error:
                raise _request_performed(error) from None
        finally:
            response.close()
    except HTTPError as error:
        try:
            try:
                return _decode_response(error)
            except KeyIdentityProbeError as decode_error:
                raise _request_performed(decode_error) from None
        finally:
            error.close()
    except KeyIdentityProbeError as error:
        raise _request_performed(error) from None
    except (TimeoutError, URLError, OSError):
        raise KeyIdentityProbeError(
            "NETWORK_OUTCOME_UNKNOWN",
            outcome_unknown=True,
            request_performed=True,
        ) from None


def _optional_timestamp(value: Any, code: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > 64
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise KeyIdentityProbeError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise KeyIdentityProbeError(code) from None
    if parsed.tzinfo is None:
        raise KeyIdentityProbeError(code)
    return value


def _validated_key_prefix(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 128
        or _KEY_PREFIX_PATTERN.fullmatch(value) is None
    ):
        raise KeyIdentityProbeError("INVALID_KEY_PREFIX")
    return value


def _request_id_reflection(
    sent_request_id: str,
    payload: Any,
    headers: Mapping[str, str],
) -> str:
    candidates: list[str] = []
    header_request_id = _header_value(headers, "x-request-id")
    if header_request_id is not None:
        candidates.append(header_request_id)
    if isinstance(payload, Mapping):
        body_request_id = payload.get("request_id")
        if isinstance(body_request_id, str):
            candidates.append(body_request_id)
    if not candidates:
        return "NOT_PRESENT"
    for candidate in candidates:
        try:
            validated = _validate_request_id(candidate)
        except KeyIdentityProbeError:
            return "INVALID"
        if validated != sent_request_id:
            return "MISMATCH"
    return "MATCH"


def _base_result(
    correlation_id: str,
    status: int,
    reflection: str,
) -> dict[str, Any]:
    return {
        "probe": _PROBE,
        "endpoint": "GET /api/keys",
        "get_requests": 1,
        "post_requests": 0,
        "simulation_posts": 0,
        "broadcast_posts": 0,
        "funds_moved": False,
        "http_status": status,
        "support_request_id": correlation_id,
        "request_id_reflection": reflection,
    }


def _classify_response(
    api_key: str,
    correlation_id: str,
    status: int,
    payload: Any,
    headers: Mapping[str, str],
) -> tuple[int, dict[str, Any]]:
    reflection = _request_id_reflection(correlation_id, payload, headers)
    base = _base_result(correlation_id, status, reflection)

    if reflection in {"INVALID", "MISMATCH"}:
        base.update(
            {
                "status": "STOP",
                "reason": "REQUEST_ID_REFLECTION_INVALID",
                "retry": "MANUAL_PROVIDER_REVIEW_REQUIRED",
            }
        )
        return 2, base

    if status != 200:
        base.update(
            {
                "status": "STOP",
                "reason": "ORGANIZATION_KEY_IDENTITY_HTTP_REJECTED",
                "retry": "REVIEW_BEFORE_REPEAT",
            }
        )
        provider_error_code = _safe_provider_error_code(payload)
        if provider_error_code is not None:
            base["provider_error_code"] = provider_error_code
        return 2, base

    if not isinstance(payload, list):
        raise KeyIdentityProbeError("INVALID_KEYS_RESPONSE")

    matches: list[dict[str, str | None]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise KeyIdentityProbeError("INVALID_KEYS_RESPONSE")
        prefix = _validated_key_prefix(item.get("keyPrefix"))
        if api_key.startswith(prefix):
            matches.append(
                {
                    "created_at": _optional_timestamp(
                        item.get("createdAt"),
                        "INVALID_CREATED_AT",
                    ),
                    "last_used_at": _optional_timestamp(
                        item.get("lastUsedAt"),
                        "INVALID_LAST_USED_AT",
                    ),
                    "expires_at": _optional_timestamp(
                        item.get("expiresAt"),
                        "INVALID_EXPIRES_AT",
                    ),
                }
            )

    if not matches:
        base.update(
            {
                "status": "STOP",
                "reason": "KEY_NOT_VISIBLE_IN_ACTIVE_ORGANIZATION",
                "organization_key_match": "MISMATCH",
                "retry": "MANUAL_ORGANIZATION_REVIEW_REQUIRED",
            }
        )
        return 2, base
    if len(matches) != 1:
        base.update(
            {
                "status": "STOP",
                "reason": "AMBIGUOUS_KEY_PREFIX_MATCH",
                "organization_key_match": "AMBIGUOUS",
                "retry": "MANUAL_ORGANIZATION_REVIEW_REQUIRED",
            }
        )
        return 2, base

    match = matches[0]
    base.update(
        {
            "status": "PASS",
            "reason": "ORGANIZATION_KEY_VISIBLE_TO_BACKEND",
            "organization_key_match": "MATCH",
            "created_at": match["created_at"],
            "last_used_at": match["last_used_at"],
            "expires_at": match["expires_at"],
            "retry": "NOT_REQUIRED",
        }
    )
    return 0, base


def run_probe(
    api_key: str,
    *,
    opener: _Opener | None = None,
    request_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Perform one GET and return bounded identity evidence."""

    key = _validate_api_key(api_key)
    correlation_id = (
        _generated_request_id()
        if request_id is None
        else _validate_request_id(request_id)
    )
    status, payload, headers = _one_get(
        key,
        correlation_id,
        opener=opener,
    )
    try:
        return _classify_response(
            key,
            correlation_id,
            status,
            payload,
            headers,
        )
    except KeyIdentityProbeError as error:
        raise _request_performed(error) from None


def _error_result(
    error: KeyIdentityProbeError,
    request_id: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "probe": _PROBE,
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
    if error.http_status is not None:
        result["http_status"] = error.http_status
    if error.provider_error_code is not None:
        result["provider_error_code"] = error.provider_error_code
    return result


def main() -> int:
    api_key = os.environ.pop(_API_KEY_ENV, None)
    request_id: str | None = None
    if api_key is None:
        print(
            json.dumps(
                {
                    "probe": _PROBE,
                    "status": "STOP",
                    "reason": "LOCAL_API_KEY_NOT_SET",
                    "get_requests": 0,
                    "post_requests": 0,
                    "simulation_posts": 0,
                    "broadcast_posts": 0,
                    "funds_moved": False,
                    "retry": "LOCAL_INPUT_CORRECTION_ALLOWED",
                },
                sort_keys=True,
            )
        )
        return 2

    try:
        api_key = _validate_api_key(api_key)
        request_id = _generated_request_id()
        exit_code, result = run_probe(api_key, request_id=request_id)
    except KeyIdentityProbeError as error:
        exit_code = 2
        result = _error_result(error, request_id)
    except Exception:
        exit_code = 2
        result = {
            "probe": _PROBE,
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

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
