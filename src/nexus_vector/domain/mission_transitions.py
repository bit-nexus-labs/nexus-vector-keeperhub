"""Pure state transitions for immutable Mission domain records."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from nexus_vector.domain.mission_models import (
    EffectRecord,
    EffectState,
    MissionModelValidationError,
    MissionRecord,
    MissionState,
)


_MISSION_TRANSITIONS = {
    MissionState.RECEIVED: frozenset(
        {
            MissionState.VALIDATED,
            MissionState.MISSION_CONFLICT,
            MissionState.BLOCKED,
        }
    ),
    MissionState.VALIDATED: frozenset(
        {
            MissionState.PERSISTED,
            MissionState.MISSION_CONFLICT,
            MissionState.BLOCKED,
        }
    ),
    MissionState.PERSISTED: frozenset(
        {
            MissionState.RECONCILING,
            MissionState.BLOCKED,
        }
    ),
    MissionState.RECONCILING: frozenset(
        {
            MissionState.READY_FOR_EXECUTION,
            MissionState.EXECUTION_UNKNOWN,
            MissionState.VERIFICATION_FAILED,
            MissionState.MANUAL_REVIEW_REQUIRED,
            MissionState.BLOCKED,
            MissionState.COMPLETED,
        }
    ),
    MissionState.READY_FOR_EXECUTION: frozenset(
        {
            MissionState.EXECUTING,
            MissionState.RECONCILING,
            MissionState.MANUAL_REVIEW_REQUIRED,
            MissionState.BLOCKED,
        }
    ),
    MissionState.EXECUTING: frozenset(
        {
            MissionState.VERIFYING,
            MissionState.EXECUTION_UNKNOWN,
            MissionState.RECONCILING,
            MissionState.MANUAL_REVIEW_REQUIRED,
            MissionState.BLOCKED,
        }
    ),
    MissionState.VERIFYING: frozenset(
        {
            MissionState.COMPLETED,
            MissionState.EXECUTION_UNKNOWN,
            MissionState.VERIFICATION_FAILED,
            MissionState.RECONCILING,
            MissionState.MANUAL_REVIEW_REQUIRED,
            MissionState.BLOCKED,
        }
    ),
    MissionState.EXECUTION_UNKNOWN: frozenset(
        {
            MissionState.RECONCILING,
            MissionState.MANUAL_REVIEW_REQUIRED,
            MissionState.BLOCKED,
        }
    ),
    MissionState.VERIFICATION_FAILED: frozenset(
        {
            MissionState.RECONCILING,
            MissionState.MANUAL_REVIEW_REQUIRED,
            MissionState.BLOCKED,
        }
    ),
    MissionState.COMPLETED: frozenset(),
    MissionState.MISSION_CONFLICT: frozenset(),
    MissionState.BLOCKED: frozenset(),
    MissionState.MANUAL_REVIEW_REQUIRED: frozenset(),
}

_EFFECT_TRANSITIONS = {
    EffectState.PLANNED: frozenset(
        {EffectState.RESERVED, EffectState.BLOCKED}
    ),
    EffectState.RESERVED: frozenset(
        {EffectState.SUBMITTED, EffectState.BLOCKED}
    ),
    EffectState.SUBMITTED: frozenset(
        {
            EffectState.EXECUTION_UNKNOWN,
            EffectState.CHAIN_CONFIRMED,
            EffectState.FAILED_FINAL,
            EffectState.BLOCKED,
        }
    ),
    EffectState.EXECUTION_UNKNOWN: frozenset(
        {
            EffectState.CHAIN_CONFIRMED,
            EffectState.FAILED_FINAL,
            EffectState.BLOCKED,
        }
    ),
    EffectState.CHAIN_CONFIRMED: frozenset(),
    EffectState.FAILED_FINAL: frozenset(),
    EffectState.BLOCKED: frozenset(),
}


class MissionTransitionError(ValueError):
    """Machine-classifiable transition error with no input echo."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise MissionTransitionError(code)


def _require_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail("INVALID_TIMESTAMP")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError):
        _fail("INVALID_TIMESTAMP")
    if offset != timedelta(0):
        _fail("NON_UTC_TIMESTAMP")
    return value


def _require_not_earlier(
    timestamp: object,
    current_timestamp: datetime,
) -> datetime:
    checked_timestamp = _require_utc_timestamp(timestamp)
    if checked_timestamp < current_timestamp:
        _fail("TIMESTAMP_BEFORE_CURRENT")
    return checked_timestamp


def transition_mission(
    record: MissionRecord,
    target_state: MissionState,
    updated_at_utc: datetime,
) -> MissionRecord:
    """Return a Mission in one explicitly allowed next state."""

    if not isinstance(record, MissionRecord):
        _fail("INVALID_MISSION_RECORD")
    if not isinstance(target_state, MissionState):
        _fail("INVALID_MISSION_TARGET_STATE")
    timestamp = _require_not_earlier(
        updated_at_utc,
        record.updated_at_utc,
    )
    if target_state not in _MISSION_TRANSITIONS[record.state]:
        _fail("MISSION_TRANSITION_NOT_ALLOWED")
    if target_state is MissionState.COMPLETED and any(
        effect.state is not EffectState.CHAIN_CONFIRMED
        for effect in record.effects
    ):
        _fail("MISSION_COMPLETION_REQUIRES_CONFIRMED_EFFECTS")
    return replace(
        record,
        state=target_state,
        updated_at_utc=timestamp,
    )


def transition_effect(
    record: EffectRecord,
    target_state: EffectState,
    updated_at_utc: datetime,
) -> EffectRecord:
    """Return an Effect in one explicitly allowed next state."""

    if not isinstance(record, EffectRecord):
        _fail("INVALID_EFFECT_RECORD")
    if not isinstance(target_state, EffectState):
        _fail("INVALID_EFFECT_TARGET_STATE")
    timestamp = _require_not_earlier(
        updated_at_utc,
        record.updated_at_utc,
    )
    if target_state not in _EFFECT_TRANSITIONS[record.state]:
        _fail("EFFECT_TRANSITION_NOT_ALLOWED")
    return replace(
        record,
        state=target_state,
        updated_at_utc=timestamp,
    )


def transition_mission_effect(
    record: MissionRecord,
    effect_ref: str,
    target_state: EffectState,
    updated_at_utc: datetime,
) -> MissionRecord:
    """Transition one canonically referenced Effect in a Mission."""

    if not isinstance(record, MissionRecord):
        _fail("INVALID_MISSION_RECORD")
    timestamp = _require_not_earlier(
        updated_at_utc,
        record.updated_at_utc,
    )
    try:
        selected_effect_id = record.effect_id_for(effect_ref)
    except MissionModelValidationError:
        _fail("UNKNOWN_EFFECT_REF")

    selected_effect = next(
        (
            effect
            for effect in record.effects
            if effect.effect_id == selected_effect_id
        ),
        None,
    )
    if selected_effect is None:
        _fail("UNKNOWN_EFFECT_REF")

    transitioned_effect = transition_effect(
        selected_effect,
        target_state,
        timestamp,
    )
    effects = tuple(
        transitioned_effect
        if effect.effect_id == selected_effect_id
        else effect
        for effect in record.effects
    )
    return replace(
        record,
        effects=effects,
        updated_at_utc=timestamp,
    )
