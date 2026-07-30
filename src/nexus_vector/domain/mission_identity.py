"""Pure, versioned business identity for Nexus Vector Missions."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


SCHEMA_VERSION = "nexus-vector.mission-identity.v1"

NEW_MISSION = "NEW_MISSION"
SAME_MISSION = "SAME_MISSION"
MISSION_CONFLICT = "MISSION_CONFLICT"
DIFFERENT_MISSION = "DIFFERENT_MISSION"

_MISSION_KEY_DOMAIN = b"nexus-vector:mission-key:v1\x00"
_CONTENT_FINGERPRINT_DOMAIN = b"nexus-vector:mission-content:v1\x00"
_EFFECT_ID_DOMAIN = b"nexus-vector:mission-effect:v1\x00"

_TOP_LEVEL_FIELDS = frozenset(
    {
        "mission_namespace",
        "mission_ref",
        "mission_type",
        "chain_id",
        "asset",
        "effects",
    }
)
_ASSET_FIELDS = frozenset({"token_address", "decimals"})
_EFFECT_FIELDS = frozenset(
    {"effect_ref", "recipient", "amount_base_units"}
)
_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
_MISSION_KEY_PATTERN = re.compile(r"msn_[0-9a-f]{64}")


class MissionValidationError(ValueError):
    """Machine-classifiable validation error with no input echo."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class MissionComparison(str, Enum):
    NEW_MISSION = NEW_MISSION
    SAME_MISSION = SAME_MISSION
    MISSION_CONFLICT = MISSION_CONFLICT
    DIFFERENT_MISSION = DIFFERENT_MISSION


@dataclass(frozen=True)
class MissionIdentity:
    """Safe derived identity; contains no Mission payload fields."""

    schema_version: str
    mission_key: str
    content_fingerprint: str
    effect_ids: tuple[str, ...]


def _require_exact_fields(
    value: Mapping[str, Any],
    required_fields: frozenset[str],
) -> None:
    actual_fields = frozenset(value.keys())
    if actual_fields != required_fields:
        if required_fields - actual_fields:
            raise MissionValidationError("MISSING_REQUIRED_FIELD")
        raise MissionValidationError("UNKNOWN_FIELD")
    if any(not isinstance(key, str) for key in value):
        raise MissionValidationError("UNKNOWN_FIELD")


def _normalize_required_string(value: Any) -> str:
    if not isinstance(value, str):
        raise MissionValidationError("INVALID_STRING")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized:
        raise MissionValidationError("EMPTY_STRING")
    return normalized


def _normalize_address(value: Any) -> str:
    if not isinstance(value, str) or _ADDRESS_PATTERN.fullmatch(value) is None:
        raise MissionValidationError("INVALID_ADDRESS")
    return value.lower()


def _require_integer(value: Any, *, code: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise MissionValidationError(code)
    return value


def _normalize_document(
    document: Any,
    *,
    schema_version: str,
) -> dict[str, Any]:
    if schema_version != SCHEMA_VERSION:
        raise MissionValidationError("UNSUPPORTED_SCHEMA_VERSION")
    if not isinstance(document, Mapping):
        raise MissionValidationError("INVALID_DOCUMENT_SHAPE")
    _require_exact_fields(document, _TOP_LEVEL_FIELDS)

    asset = document["asset"]
    if not isinstance(asset, Mapping):
        raise MissionValidationError("INVALID_ASSET_SHAPE")
    _require_exact_fields(asset, _ASSET_FIELDS)

    effects = document["effects"]
    if not isinstance(effects, list):
        raise MissionValidationError("INVALID_EFFECTS_SHAPE")
    if not effects:
        raise MissionValidationError("EMPTY_EFFECTS")

    normalized_effects: list[dict[str, object]] = []
    seen_effect_refs: set[str] = set()
    for effect in effects:
        if not isinstance(effect, Mapping):
            raise MissionValidationError("INVALID_EFFECT_SHAPE")
        _require_exact_fields(effect, _EFFECT_FIELDS)
        effect_ref = _normalize_required_string(effect["effect_ref"])
        if effect_ref in seen_effect_refs:
            raise MissionValidationError("DUPLICATE_EFFECT_REF")
        seen_effect_refs.add(effect_ref)
        normalized_effects.append(
            {
                "effect_ref": effect_ref,
                "recipient": _normalize_address(effect["recipient"]),
                "amount_base_units": _require_integer(
                    effect["amount_base_units"],
                    code="INVALID_AMOUNT_BASE_UNITS",
                    minimum=1,
                ),
            }
        )
    normalized_effects.sort(key=lambda item: str(item["effect_ref"]))

    return {
        "schema_version": schema_version,
        "mission_namespace": _normalize_required_string(
            document["mission_namespace"]
        ),
        "mission_ref": _normalize_required_string(document["mission_ref"]),
        "mission_type": _normalize_required_string(document["mission_type"]),
        "chain_id": _require_integer(
            document["chain_id"],
            code="INVALID_CHAIN_ID",
            minimum=1,
        ),
        "asset": {
            "token_address": _normalize_address(asset["token_address"]),
            "decimals": _require_integer(
                asset["decimals"],
                code="INVALID_DECIMALS",
                minimum=0,
            ),
        },
        "effects": normalized_effects,
    }


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _prefixed_sha256(prefix: str, domain: bytes, material: bytes) -> str:
    return f"{prefix}{hashlib.sha256(domain + material).hexdigest()}"


def derive_effect_id(
    mission_key: str,
    effect_ref: Any,
    *,
    schema_version: str = SCHEMA_VERSION,
) -> str:
    """Derive one stable effect identity without payload-dependent fields."""

    if schema_version != SCHEMA_VERSION:
        raise MissionValidationError("UNSUPPORTED_SCHEMA_VERSION")
    if (
        not isinstance(mission_key, str)
        or _MISSION_KEY_PATTERN.fullmatch(mission_key) is None
    ):
        raise MissionValidationError("INVALID_MISSION_KEY")
    normalized_effect_ref = _normalize_required_string(effect_ref)
    material = _canonical_json(
        {
            "schema_version": schema_version,
            "mission_key": mission_key,
            "effect_ref": normalized_effect_ref,
        }
    )
    return _prefixed_sha256("eff_", _EFFECT_ID_DOMAIN, material)


def build_mission_identity(
    document: Any,
    *,
    schema_version: str = SCHEMA_VERSION,
) -> MissionIdentity:
    """Validate and derive deterministic Mission and effect identities."""

    normalized = _normalize_document(
        document,
        schema_version=schema_version,
    )
    mission_key_material = _canonical_json(
        {
            "schema_version": schema_version,
            "mission_namespace": normalized["mission_namespace"],
            "mission_type": normalized["mission_type"],
            "mission_ref": normalized["mission_ref"],
        }
    )
    mission_key = _prefixed_sha256(
        "msn_",
        _MISSION_KEY_DOMAIN,
        mission_key_material,
    )
    content_fingerprint = _prefixed_sha256(
        "mfp_",
        _CONTENT_FINGERPRINT_DOMAIN,
        _canonical_json(normalized),
    )
    effect_ids = tuple(
        derive_effect_id(
            mission_key,
            effect["effect_ref"],
            schema_version=schema_version,
        )
        for effect in normalized["effects"]
    )
    return MissionIdentity(
        schema_version=schema_version,
        mission_key=mission_key,
        content_fingerprint=content_fingerprint,
        effect_ids=effect_ids,
    )


def classify_mission(
    existing: MissionIdentity | None,
    candidate: MissionIdentity,
) -> MissionComparison:
    """Classify a candidate without performing persistence or side effects."""

    if not isinstance(candidate, MissionIdentity):
        raise MissionValidationError("INVALID_IDENTITY_RESULT")
    if existing is None:
        return MissionComparison.NEW_MISSION
    if not isinstance(existing, MissionIdentity):
        raise MissionValidationError("INVALID_IDENTITY_RESULT")
    if existing.mission_key != candidate.mission_key:
        return MissionComparison.DIFFERENT_MISSION
    if existing.content_fingerprint == candidate.content_fingerprint:
        return MissionComparison.SAME_MISSION
    return MissionComparison.MISSION_CONFLICT
