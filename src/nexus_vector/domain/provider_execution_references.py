"""Immutable provider execution references for restart-safe recovery."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

_ATTEMPT_ID_PATTERN = re.compile(r"att_[0-9a-f]{64}")
_REQUEST_FINGERPRINT_PATTERN = re.compile(r"xrf_[0-9a-f]{64}")
_MAX_NAMESPACE_LENGTH = 256
_MAX_REFERENCE_LENGTH = 256


class ProviderExecutionReferenceError(ValueError):
    """Machine-classifiable validation error without raw-value echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ProviderExecutionReferenceError(code)


def _required_text(value: Any, *, code: str, maximum: int) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or len(normalized) > maximum
        or normalized.strip() != normalized
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        _fail(code)
    return normalized


def validate_attempt_id(value: Any) -> str:
    if not isinstance(value, str) or _ATTEMPT_ID_PATTERN.fullmatch(value) is None:
        _fail("INVALID_ATTEMPT_ID")
    return value


def validate_request_fingerprint(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _REQUEST_FINGERPRINT_PATTERN.fullmatch(value) is None
    ):
        _fail("INVALID_REQUEST_FINGERPRINT")
    return value


def normalize_provider_namespace(value: Any) -> str:
    return _required_text(
        value,
        code="INVALID_PROVIDER_NAMESPACE",
        maximum=_MAX_NAMESPACE_LENGTH,
    )


def normalize_provider_reference(value: Any) -> str:
    return _required_text(
        value,
        code="INVALID_PROVIDER_REFERENCE",
        maximum=_MAX_REFERENCE_LENGTH,
    )


def _utc(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        _fail("INVALID_TIMESTAMP")
    if offset != timedelta(0):
        _fail("NON_UTC_TIMESTAMP")
    return value


@dataclass(frozen=True)
class ProviderExecutionReference:
    """One immutable provider-side execution identifier per canonical attempt."""

    attempt_id: str
    provider_namespace: str
    request_fingerprint: str
    provider_reference: str
    created_at_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", validate_attempt_id(self.attempt_id))
        object.__setattr__(
            self,
            "request_fingerprint",
            validate_request_fingerprint(self.request_fingerprint),
        )
        object.__setattr__(
            self,
            "provider_namespace",
            normalize_provider_namespace(self.provider_namespace),
        )
        object.__setattr__(
            self,
            "provider_reference",
            normalize_provider_reference(self.provider_reference),
        )
        object.__setattr__(self, "created_at_utc", _utc(self.created_at_utc))
