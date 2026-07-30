"""Immutable, provider-neutral Mission business record models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Any

from nexus_vector.domain.mission_identity import (
    SCHEMA_VERSION,
    MissionIdentity,
    MissionValidationError,
    build_mission_identity,
    derive_effect_id,
)


_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "mission_namespace",
        "mission_ref",
        "mission_type",
        "chain_id",
        "asset",
        "effects",
    }
)
_ASSET_FIELDS = frozenset({"token_address", "decimals"})
_EFFECT_REQUEST_FIELDS = frozenset(
    {"effect_ref", "recipient", "amount_base_units"}
)


class MissionModelValidationError(ValueError):
    """Machine-classifiable model validation error with no input echo."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class MissionState(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    PERSISTED = "PERSISTED"
    RECONCILING = "RECONCILING"
    READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    MISSION_CONFLICT = "MISSION_CONFLICT"
    BLOCKED = "BLOCKED"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class EffectState(str, Enum):
    PLANNED = "PLANNED"
    RESERVED = "RESERVED"
    SUBMITTED = "SUBMITTED"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    CHAIN_CONFIRMED = "CHAIN_CONFIRMED"
    FAILED_FINAL = "FAILED_FINAL"
    BLOCKED = "BLOCKED"


def _fail(code: str) -> None:
    raise MissionModelValidationError(code)


def _require_exact_fields(
    value: Mapping[str, Any],
    required_fields: frozenset[str],
) -> None:
    actual_fields = frozenset(value.keys())
    if actual_fields != required_fields:
        if required_fields - actual_fields:
            _fail("MISSING_REQUIRED_FIELD")
        _fail("UNKNOWN_FIELD")
    if any(not isinstance(key, str) for key in value):
        _fail("UNKNOWN_FIELD")


def _require_nonempty_string(value: Any, code: str) -> None:
    if not isinstance(value, str) or not value:
        _fail(code)


def _require_address(value: Any, code: str) -> None:
    if not isinstance(value, str) or _ADDRESS_PATTERN.fullmatch(value) is None:
        _fail(code)


def _require_integer(value: Any, code: str, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        _fail(code)


def _require_utc_timestamp(value: Any) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        _fail("INVALID_TIMESTAMP")
    if offset != timedelta(0):
        _fail("NON_UTC_TIMESTAMP")


def _require_timestamp_order(
    created_at_utc: datetime,
    updated_at_utc: datetime,
) -> None:
    _require_utc_timestamp(created_at_utc)
    _require_utc_timestamp(updated_at_utc)
    if updated_at_utc < created_at_utc:
        _fail("REVERSED_TIMESTAMP")


def _wrap_identity_error(prefix: str, error: MissionValidationError) -> None:
    _fail(f"{prefix}_{error.code}")


@dataclass(frozen=True)
class AssetSpec:
    """Immutable asset business specification."""

    token_address: str
    decimals: int

    def __post_init__(self) -> None:
        _require_address(self.token_address, "INVALID_TOKEN_ADDRESS")
        _require_integer(self.decimals, "INVALID_TOKEN_DECIMALS", 0)


@dataclass(frozen=True)
class EffectRequest:
    """Immutable intended effect within a Mission request."""

    effect_ref: str
    recipient: str
    amount_base_units: int

    def __post_init__(self) -> None:
        _require_nonempty_string(self.effect_ref, "INVALID_EFFECT_REF")
        _require_address(self.recipient, "INVALID_RECIPIENT")
        _require_integer(
            self.amount_base_units,
            "INVALID_AMOUNT_BASE_UNITS",
            1,
        )


@dataclass(frozen=True)
class MissionRequest:
    """Immutable durable business intent, without provider/runtime fields."""

    schema_version: str
    mission_namespace: str
    mission_ref: str
    mission_type: str
    chain_id: int
    asset: AssetSpec
    effects: tuple[EffectRequest, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.asset, AssetSpec):
            _fail("INVALID_ASSET_TYPE")
        if not isinstance(self.effects, tuple):
            _fail("INVALID_EFFECT_REQUESTS_CONTAINER")
        if not self.effects:
            _fail("EMPTY_EFFECTS")
        if any(not isinstance(effect, EffectRequest) for effect in self.effects):
            _fail("INVALID_EFFECT_REQUEST_TYPE")
        _require_nonempty_string(
            self.mission_namespace,
            "INVALID_MISSION_NAMESPACE",
        )
        _require_nonempty_string(self.mission_ref, "INVALID_MISSION_REF")
        _require_nonempty_string(self.mission_type, "INVALID_MISSION_TYPE")
        _require_integer(self.chain_id, "INVALID_CHAIN_ID", 1)
        self.build_identity()

    @classmethod
    def from_mapping(cls, value: Any) -> MissionRequest:
        """Construct from an exact-field public mapping, rejecting extras."""

        if not isinstance(value, Mapping):
            _fail("INVALID_REQUEST_SHAPE")
        _require_exact_fields(value, _REQUEST_FIELDS)

        asset_value = value["asset"]
        if not isinstance(asset_value, Mapping):
            _fail("INVALID_ASSET_SHAPE")
        _require_exact_fields(asset_value, _ASSET_FIELDS)

        effects_value = value["effects"]
        if not isinstance(effects_value, list):
            _fail("INVALID_EFFECTS_SHAPE")
        effect_requests: list[EffectRequest] = []
        for effect_value in effects_value:
            if not isinstance(effect_value, Mapping):
                _fail("INVALID_EFFECT_SHAPE")
            _require_exact_fields(effect_value, _EFFECT_REQUEST_FIELDS)
            effect_requests.append(
                EffectRequest(
                    effect_ref=effect_value["effect_ref"],
                    recipient=effect_value["recipient"],
                    amount_base_units=effect_value["amount_base_units"],
                )
            )

        return cls(
            schema_version=value["schema_version"],
            mission_namespace=value["mission_namespace"],
            mission_ref=value["mission_ref"],
            mission_type=value["mission_type"],
            chain_id=value["chain_id"],
            asset=AssetSpec(
                token_address=asset_value["token_address"],
                decimals=asset_value["decimals"],
            ),
            effects=tuple(effect_requests),
        )

    def to_identity_document(self) -> dict[str, Any]:
        """Return the exact public Mission Identity document shape."""

        return {
            "mission_namespace": self.mission_namespace,
            "mission_ref": self.mission_ref,
            "mission_type": self.mission_type,
            "chain_id": self.chain_id,
            "asset": {
                "token_address": self.asset.token_address,
                "decimals": self.asset.decimals,
            },
            "effects": [
                {
                    "effect_ref": effect.effect_ref,
                    "recipient": effect.recipient,
                    "amount_base_units": effect.amount_base_units,
                }
                for effect in self.effects
            ],
        }

    def build_identity(self) -> MissionIdentity:
        """Validate and derive identity through the accepted public API."""

        try:
            return build_mission_identity(
                self.to_identity_document(),
                schema_version=self.schema_version,
            )
        except MissionValidationError as error:
            _wrap_identity_error("REQUEST", error)


@dataclass(frozen=True)
class EffectRecord:
    """Immutable record for one explicitly referenced Mission effect."""

    mission_key: str
    effect_ref: str
    effect_id: str
    chain_id: int
    token_address: str
    token_decimals: int
    recipient: str
    amount_base_units: int
    state: EffectState
    created_at_utc: datetime
    updated_at_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.state, EffectState):
            _fail("INVALID_EFFECT_STATE")
        _require_integer(self.chain_id, "INVALID_CHAIN_ID", 1)
        _require_address(self.token_address, "INVALID_TOKEN_ADDRESS")
        _require_integer(self.token_decimals, "INVALID_TOKEN_DECIMALS", 0)
        _require_address(self.recipient, "INVALID_RECIPIENT")
        _require_integer(
            self.amount_base_units,
            "INVALID_AMOUNT_BASE_UNITS",
            1,
        )
        _require_timestamp_order(self.created_at_utc, self.updated_at_utc)
        try:
            expected_effect_id = derive_effect_id(
                self.mission_key,
                self.effect_ref,
            )
        except MissionValidationError as error:
            _wrap_identity_error("EFFECT", error)
        if self.effect_id != expected_effect_id:
            _fail("EFFECT_ID_MISMATCH")


@dataclass(frozen=True)
class MissionRecord:
    """Immutable Mission aggregate with explicit effect-reference identity."""

    schema_version: str
    mission_key: str
    content_fingerprint: str
    request: MissionRequest
    state: MissionState
    effects: tuple[EffectRecord, ...]
    created_at_utc: datetime
    updated_at_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.request, MissionRequest):
            _fail("INVALID_REQUEST_TYPE")
        if not isinstance(self.state, MissionState):
            _fail("INVALID_MISSION_STATE")
        if not isinstance(self.effects, tuple):
            _fail("INVALID_EFFECTS_CONTAINER")
        if any(not isinstance(effect, EffectRecord) for effect in self.effects):
            _fail("INVALID_EFFECT_RECORD_TYPE")
        _require_timestamp_order(self.created_at_utc, self.updated_at_utc)

        identity = self.request.build_identity()
        if self.schema_version != self.request.schema_version:
            _fail("SCHEMA_VERSION_MISMATCH")
        if self.schema_version != identity.schema_version:
            _fail("SCHEMA_VERSION_MISMATCH")
        if self.mission_key != identity.mission_key:
            _fail("MISSION_KEY_MISMATCH")
        if self.content_fingerprint != identity.content_fingerprint:
            _fail("CONTENT_FINGERPRINT_MISMATCH")

        expected_requests = {
            effect.effect_ref: effect for effect in self.request.effects
        }
        actual_refs = tuple(effect.effect_ref for effect in self.effects)
        if len(actual_refs) != len(frozenset(actual_refs)):
            _fail("DUPLICATE_EFFECT_REF")
        actual_ref_set = frozenset(actual_refs)
        expected_ref_set = frozenset(expected_requests)
        if expected_ref_set - actual_ref_set:
            _fail("MISSING_EFFECT_REF")
        if actual_ref_set - expected_ref_set:
            _fail("UNEXPECTED_EFFECT_REF")

        identity_effect_ids = frozenset(identity.effect_ids)
        for effect in self.effects:
            if effect.mission_key != self.mission_key:
                _fail("EFFECT_MISSION_KEY_MISMATCH")
            request_effect = expected_requests[effect.effect_ref]
            try:
                expected_effect_id = derive_effect_id(
                    self.mission_key,
                    effect.effect_ref,
                    schema_version=self.schema_version,
                )
            except MissionValidationError as error:
                _wrap_identity_error("EFFECT", error)
            if (
                effect.effect_id != expected_effect_id
                or effect.effect_id not in identity_effect_ids
            ):
                _fail("EFFECT_ID_MISMATCH")
            if (
                effect.chain_id != self.request.chain_id
                or effect.token_address != self.request.asset.token_address
                or effect.token_decimals != self.request.asset.decimals
                or effect.recipient != request_effect.recipient
                or effect.amount_base_units
                != request_effect.amount_base_units
            ):
                _fail("EFFECT_ECONOMIC_MISMATCH")

    @property
    def effect_ids_by_ref(self) -> Mapping[str, str]:
        """Return an immutable deterministic effect_ref to effect_id view."""

        return MappingProxyType(
            {
                effect.effect_ref: effect.effect_id
                for effect in sorted(
                    self.effects,
                    key=lambda item: item.effect_ref,
                )
            }
        )

    def effect_id_for(self, effect_ref: str) -> str:
        """Look up an effect ID by business reference, never by position."""

        if not isinstance(effect_ref, str):
            _fail("UNKNOWN_EFFECT_REF")
        try:
            return self.effect_ids_by_ref[effect_ref]
        except KeyError:
            _fail("UNKNOWN_EFFECT_REF")


def create_initial_mission_record(
    request: MissionRequest,
    created_at_utc: datetime,
) -> MissionRecord:
    """Purely build the initial RECEIVED/PLANNED Mission aggregate."""

    if not isinstance(request, MissionRequest):
        _fail("INVALID_REQUEST_TYPE")
    _require_utc_timestamp(created_at_utc)
    identity = request.build_identity()

    requests_by_ref = {
        effect.effect_ref: effect for effect in request.effects
    }
    effect_ids_by_ref = {
        effect_ref: derive_effect_id(
            identity.mission_key,
            effect_ref,
            schema_version=request.schema_version,
        )
        for effect_ref in requests_by_ref
    }
    if frozenset(effect_ids_by_ref.values()) != frozenset(
        identity.effect_ids
    ):
        _fail("IDENTITY_EFFECT_SET_MISMATCH")

    effects = tuple(
        EffectRecord(
            mission_key=identity.mission_key,
            effect_ref=effect_ref,
            effect_id=effect_ids_by_ref[effect_ref],
            chain_id=request.chain_id,
            token_address=request.asset.token_address,
            token_decimals=request.asset.decimals,
            recipient=requests_by_ref[effect_ref].recipient,
            amount_base_units=requests_by_ref[
                effect_ref
            ].amount_base_units,
            state=EffectState.PLANNED,
            created_at_utc=created_at_utc,
            updated_at_utc=created_at_utc,
        )
        for effect_ref in sorted(requests_by_ref)
    )
    return MissionRecord(
        schema_version=request.schema_version,
        mission_key=identity.mission_key,
        content_fingerprint=identity.content_fingerprint,
        request=request,
        state=MissionState.RECEIVED,
        effects=effects,
        created_at_utc=created_at_utc,
        updated_at_utc=created_at_utc,
    )
