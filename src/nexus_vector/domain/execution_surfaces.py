"""Durable one-surface authority for each canonical economic effect."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

_MISSION_KEY_PATTERN = re.compile(r"msn_[0-9a-f]{64}")
_EFFECT_ID_PATTERN = re.compile(r"eff_[0-9a-f]{64}")
_MAX_REFERENCE_LENGTH = 256


class ExecutionSurface(str, Enum):
    DIRECT_EXECUTION = "DIRECT_EXECUTION"
    WORKFLOW = "WORKFLOW"
    MCP = "MCP"


class ExecutionSurfaceBindingError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ExecutionSurfaceBindingError(code)


def _canonical_reference(value: Any, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    return value


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


def _approval_reference(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > _MAX_REFERENCE_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail("INVALID_BINDING_REFERENCE")
    return value


@dataclass(frozen=True)
class ExecutionSurfaceBinding:
    mission_key: str
    effect_id: str
    surface: ExecutionSurface
    binding_reference: str
    bound_at_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "mission_key",
            _canonical_reference(
                self.mission_key,
                _MISSION_KEY_PATTERN,
                "INVALID_MISSION_KEY",
            ),
        )
        object.__setattr__(
            self,
            "effect_id",
            _canonical_reference(
                self.effect_id,
                _EFFECT_ID_PATTERN,
                "INVALID_EFFECT_ID",
            ),
        )
        if not isinstance(self.surface, ExecutionSurface):
            _fail("INVALID_EXECUTION_SURFACE")
        object.__setattr__(
            self,
            "binding_reference",
            _approval_reference(self.binding_reference),
        )
        _utc(self.bound_at_utc)
