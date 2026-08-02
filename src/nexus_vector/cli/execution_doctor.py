"""Strict JSON CLI for sanitized Execution Doctor replay snapshots."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

from nexus_vector.application.continuation_planner import (
    ContinuationAction,
    EffectContinuationDecision,
    MissionContinuationPlan,
)
from nexus_vector.application.execution_doctor import (
    ChainObservationState,
    DoctorAction,
    EffectObservation,
    ExecutionDoctor,
    ExecutionDoctorError,
    ProviderObservationState,
)
from nexus_vector.domain.mission_models import MissionState

_TOP_LEVEL_FIELDS = frozenset(
    {
        "mission_key",
        "mission_state",
        "minimum_confirmations",
        "decisions",
        "observations",
    }
)
_DECISION_FIELDS = frozenset(
    {
        "effect_ref",
        "effect_id",
        "amount_base_units",
        "action",
        "reason_code",
    }
)
_MISSION_KEY_PATTERN = re.compile(r"msn_[0-9a-f]{64}")
_EFFECT_ID_PATTERN = re.compile(r"eff_[0-9a-f]{64}")

_OBSERVATION_FIELDS = frozenset(
    {
        "effect_id",
        "provider_state",
        "chain_state",
        "confirmations",
    }
)


def _fail(code: str) -> None:
    raise ExecutionDoctorError(code)


def _exact_mapping(
    value: Any,
    fields: frozenset[str],
    code: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    if frozenset(value) != fields or any(
        not isinstance(key, str) for key in value
    ):
        _fail(code)
    return value


def build_report_from_snapshot(value: Any) -> dict[str, Any]:
    root = _exact_mapping(
        value,
        _TOP_LEVEL_FIELDS,
        "INVALID_SNAPSHOT_SHAPE",
    )
    mission_key = root["mission_key"]
    if (
        not isinstance(mission_key, str)
        or _MISSION_KEY_PATTERN.fullmatch(mission_key) is None
    ):
        _fail("INVALID_MISSION_KEY")

    decisions_value = root["decisions"]
    observations_value = root["observations"]
    if not isinstance(decisions_value, list) or not decisions_value:
        _fail("INVALID_DECISIONS")
    if not isinstance(observations_value, list):
        _fail("INVALID_OBSERVATIONS")

    decisions: list[EffectContinuationDecision] = []
    seen_effect_ids: set[str] = set()
    for raw in decisions_value:
        item = _exact_mapping(
            raw,
            _DECISION_FIELDS,
            "INVALID_DECISION_SHAPE",
        )
        for field_name in ("effect_ref", "effect_id", "reason_code"):
            if not isinstance(item[field_name], str) or not item[field_name]:
                _fail(f"INVALID_{field_name.upper()}")
        if _EFFECT_ID_PATTERN.fullmatch(item["effect_id"]) is None:
            _fail("INVALID_EFFECT_ID")
        try:
            action = ContinuationAction(item["action"])
        except (TypeError, ValueError):
            _fail("INVALID_CONTINUATION_ACTION")
        amount = item["amount_base_units"]
        if type(amount) is not int or amount < 1:
            _fail("INVALID_EFFECT_AMOUNT")
        decision = EffectContinuationDecision(
            effect_ref=item["effect_ref"],
            effect_id=item["effect_id"],
            amount_base_units=amount,
            action=action,
            reason_code=item["reason_code"],
        )
        if decision.effect_id in seen_effect_ids:
            _fail("DUPLICATE_EFFECT_ID")
        seen_effect_ids.add(decision.effect_id)
        decisions.append(decision)

    observations: dict[str, EffectObservation] = {}
    for raw in observations_value:
        item = _exact_mapping(
            raw,
            _OBSERVATION_FIELDS,
            "INVALID_OBSERVATION_SHAPE",
        )
        if (
            not isinstance(item["effect_id"], str)
            or _EFFECT_ID_PATTERN.fullmatch(item["effect_id"]) is None
        ):
            _fail("INVALID_EFFECT_ID")
        try:
            observation = EffectObservation(
                effect_id=item["effect_id"],
                provider_state=ProviderObservationState(
                    item["provider_state"]
                ),
                chain_state=ChainObservationState(item["chain_state"]),
                confirmations=item["confirmations"],
            )
        except (TypeError, ValueError):
            _fail("INVALID_OBSERVATION_VALUE")
        if observation.effect_id in observations:
            _fail("DUPLICATE_OBSERVATION")
        observations[observation.effect_id] = observation

    try:
        mission_state = MissionState(root["mission_state"])
    except (TypeError, ValueError):
        _fail("INVALID_MISSION_STATE")

    totals = {
        action: sum(
            decision.amount_base_units
            for decision in decisions
            if decision.action is action
        )
        for action in ContinuationAction
    }
    plan = MissionContinuationPlan(
        mission_key=mission_key,
        mission_state=mission_state,
        decisions=tuple(decisions),
        total_amount_base_units=sum(
            decision.amount_base_units for decision in decisions
        ),
        skipped_amount_base_units=totals[
            ContinuationAction.SKIP_VERIFIED
        ],
        executable_amount_base_units=totals[
            ContinuationAction.EXECUTE_MISSING
        ],
        unresolved_amount_base_units=totals[
            ContinuationAction.RECONCILE_REQUIRED
        ],
        manual_review_amount_base_units=totals[
            ContinuationAction.MANUAL_REVIEW
        ],
    )
    report = ExecutionDoctor().diagnose(
        plan,
        observations,
        minimum_confirmations=root["minimum_confirmations"],
    )
    return {
        "mission_key": report.mission_key,
        "next_action": report.next_action.value,
        "amounts": {
            "total": report.total_amount_base_units,
            "skipped": report.skipped_amount_base_units,
            "executable": report.executable_amount_base_units,
            "unresolved": report.unresolved_amount_base_units,
            "manual_review": report.manual_review_amount_base_units,
        },
        "diagnoses": [
            {
                "effect_ref": diagnosis.effect_ref,
                "effect_id": diagnosis.effect_id,
                "action": diagnosis.action.value,
                "diagnosis_code": diagnosis.diagnosis_code,
            }
            for diagnosis in report.diagnoses
        ],
    }


def run(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        stderr.write("EXECUTION_DOCTOR_USAGE_ERROR\n")
        return 2
    try:
        snapshot = json.loads(
            Path(arguments[0]).read_text(encoding="utf-8")
        )
        report = build_report_from_snapshot(snapshot)
    except (
        OSError,
        json.JSONDecodeError,
        ExecutionDoctorError,
        TypeError,
        ValueError,
    ) as error:
        code = getattr(error, "code", "EXECUTION_DOCTOR_INPUT_ERROR")
        stderr.write(f"{code}\n")
        return 1
    stdout.write(
        json.dumps(
            report,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
