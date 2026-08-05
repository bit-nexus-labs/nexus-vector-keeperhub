"""Network-free planning for the bounded KeeperHub testnet evidence run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from nexus_vector.application.continuation_planner import (
    ContinuationAction,
    ContinuationPlanner,
)
from nexus_vector.application.mission_admission import MissionAdmissionService
from nexus_vector.domain.execution_attempts import (
    ExecutionAttemptPlan,
    build_execution_attempt_plan,
)
from nexus_vector.domain.mission_identity import SCHEMA_VERSION
from nexus_vector.domain.mission_models import (
    AssetSpec,
    EffectRequest,
    MissionRequest,
    MissionState,
)
from nexus_vector.integrations.keeperhub_direct_execution import (
    KEEPERHUB_PROVIDER_NAMESPACE,
    KeeperHubTransferIntent,
    amount_base_units_to_decimal_string,
)
from nexus_vector.integrations.keeperhub_request_key import (
    derive_keeperhub_request_key,
)
from nexus_vector.persistence.sqlite_mission_store import (
    SQLiteMissionStore,
    StoredMission,
)

BASE_SEPOLIA_CHAIN_ID = 84532
BASE_SEPOLIA_USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
BASE_SEPOLIA_USDC_DECIMALS = 6
FLAGSHIP_MISSION_REF = "runtime-evidence-001"
SIMULATION_CANARY_MISSION_REF = "simulation-canary-20260806-v1"


class AttemptLookup(Protocol):
    def get(self, attempt_id: str): ...


class RuntimeEvidencePlanError(RuntimeError):
    """Machine-classifiable planning failure without private-value echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RuntimeEvidencePlanError(code)


def _mission_request(
    *,
    mission_ref: str,
    effects: tuple[EffectRequest, ...],
) -> MissionRequest:
    return MissionRequest(
        schema_version=SCHEMA_VERSION,
        mission_namespace="nexus-vector.keeperhub",
        mission_ref=mission_ref,
        mission_type="mission-safe-agent-payment",
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        asset=AssetSpec(
            token_address=BASE_SEPOLIA_USDC_ADDRESS,
            decimals=BASE_SEPOLIA_USDC_DECIMALS,
        ),
        effects=effects,
    )


def build_flagship_mission_request(recipient_address: str) -> MissionRequest:
    """Build the immutable Anna/Mark two-effect live-evidence Mission."""

    return _mission_request(
        mission_ref=FLAGSHIP_MISSION_REF,
        effects=(
            EffectRequest(
                effect_ref="anna",
                recipient=recipient_address,
                amount_base_units=120_000,
            ),
            EffectRequest(
                effect_ref="mark",
                recipient=recipient_address,
                amount_base_units=70_000,
            ),
        ),
    )


def build_simulation_canary_request(recipient_address: str) -> MissionRequest:
    """Build a separate one-base-unit Mission for simulation-only validation."""

    return _mission_request(
        mission_ref=SIMULATION_CANARY_MISSION_REF,
        effects=(
            EffectRequest(
                effect_ref="provider-canary",
                recipient=recipient_address,
                amount_base_units=1,
            ),
        ),
    )


def admit_and_prepare_mission(
    request: MissionRequest,
    store: SQLiteMissionStore,
    prepared_at_utc: datetime,
) -> StoredMission:
    """Persist a Mission and move it to READY without execution authority."""

    if not isinstance(request, MissionRequest):
        _fail("INVALID_MISSION_REQUEST")
    if not isinstance(store, SQLiteMissionStore):
        _fail("INVALID_MISSION_STORE")

    current = MissionAdmissionService(store).admit(request, prepared_at_utc)
    for expected, target in (
        (MissionState.PERSISTED, MissionState.RECONCILING),
        (MissionState.RECONCILING, MissionState.READY_FOR_EXECUTION),
    ):
        if current.record.state is target:
            continue
        if current.record.state is not expected:
            if current.record.state is MissionState.READY_FOR_EXECUTION:
                break
            _fail("MISSION_NOT_PREPARABLE")
        current = store.transition_mission(
            current.record.mission_key,
            current.revision,
            target,
            max(prepared_at_utc, current.record.updated_at_utc),
        )

    final = store.get(current.record.mission_key)
    if final is None:
        _fail("MISSION_NOT_FOUND")
    if final.record.state is not MissionState.READY_FOR_EXECUTION:
        _fail("MISSION_NOT_READY")
    return final


@dataclass(frozen=True)
class BoundedEffectSelection:
    mission_key: str
    effect_ref: str
    effect_id: str
    amount_base_units: int
    intent: KeeperHubTransferIntent
    attempt_plan: ExecutionAttemptPlan
    maximum_simulation_posts: int
    maximum_broadcast_posts: int
    network_calls_performed: int = 0

    def __post_init__(self) -> None:
        if self.maximum_simulation_posts != 1:
            _fail("INVALID_SIMULATION_BUDGET")
        if self.maximum_broadcast_posts not in {0, 1}:
            _fail("INVALID_BROADCAST_BUDGET")
        if self.network_calls_performed != 0:
            _fail("NETWORK_CALLS_FORBIDDEN_DURING_PLANNING")

    @property
    def amount_decimal_string(self) -> str:
        return amount_base_units_to_decimal_string(
            self.amount_base_units,
            BASE_SEPOLIA_USDC_DECIMALS,
        )


def _select_effect(
    mission: StoredMission,
    attempt_lookup: AttemptLookup,
    effect_ref: str,
    *,
    maximum_broadcast_posts: int,
) -> BoundedEffectSelection:
    if not isinstance(mission, StoredMission):
        _fail("INVALID_STORED_MISSION")
    if not isinstance(effect_ref, str) or not effect_ref:
        _fail("INVALID_EFFECT_REF")
    if not hasattr(attempt_lookup, "get"):
        _fail("INVALID_ATTEMPT_LOOKUP")

    continuation = ContinuationPlanner(attempt_lookup).plan(mission)
    decision = next(
        (
            item
            for item in continuation.decisions
            if item.effect_ref == effect_ref
        ),
        None,
    )
    if decision is None:
        _fail("UNKNOWN_EFFECT_REF")
    if decision.action is not ContinuationAction.EXECUTE_MISSING:
        _fail("EFFECT_NOT_EXECUTABLE")

    effect = next(
        (
            item
            for item in mission.record.effects
            if item.effect_id == decision.effect_id
        ),
        None,
    )
    if effect is None:
        _fail("EFFECT_NOT_FOUND")

    intent = KeeperHubTransferIntent(
        chain_id=effect.chain_id,
        recipient_address=effect.recipient,
        amount_base_units=effect.amount_base_units,
        token_address=effect.token_address,
        token_decimals=effect.token_decimals,
    )
    request_key = derive_keeperhub_request_key(effect.effect_id)
    attempt_plan = build_execution_attempt_plan(
        mission_key=mission.record.mission_key,
        effect_id=effect.effect_id,
        provider_namespace=KEEPERHUB_PROVIDER_NAMESPACE,
        request_key=request_key,
        request_material=intent.request_material,
    )
    return BoundedEffectSelection(
        mission_key=mission.record.mission_key,
        effect_ref=effect.effect_ref,
        effect_id=effect.effect_id,
        amount_base_units=effect.amount_base_units,
        intent=intent,
        attempt_plan=attempt_plan,
        maximum_simulation_posts=1,
        maximum_broadcast_posts=maximum_broadcast_posts,
    )


def select_flagship_effect(
    mission: StoredMission,
    attempt_lookup: AttemptLookup,
    effect_ref: str,
) -> BoundedEffectSelection:
    """Select exactly one missing flagship effect; perform no provider call."""

    if mission.record.request.mission_ref != FLAGSHIP_MISSION_REF:
        _fail("NOT_FLAGSHIP_MISSION")
    if effect_ref not in {"anna", "mark"}:
        _fail("UNKNOWN_FLAGSHIP_EFFECT")
    return _select_effect(
        mission,
        attempt_lookup,
        effect_ref,
        maximum_broadcast_posts=1,
    )


def select_simulation_canary(
    mission: StoredMission,
    attempt_lookup: AttemptLookup,
) -> BoundedEffectSelection:
    """Select the isolated canary with broadcast authority fixed to zero."""

    if mission.record.request.mission_ref != SIMULATION_CANARY_MISSION_REF:
        _fail("NOT_SIMULATION_CANARY")
    return _select_effect(
        mission,
        attempt_lookup,
        "provider-canary",
        maximum_broadcast_posts=0,
    )


def sanitized_mission_snapshot(
    mission: StoredMission,
    attempt_lookup: AttemptLookup,
) -> dict[str, Any]:
    """Return a screenshot-ready view without addresses, keys, or raw IDs."""

    continuation = ContinuationPlanner(attempt_lookup).plan(mission)
    decisions = {item.effect_ref: item for item in continuation.decisions}
    effects = []
    for effect in sorted(
        mission.record.effects,
        key=lambda item: item.effect_ref,
    ):
        decision = decisions[effect.effect_ref]
        effects.append(
            {
                "effect_ref": effect.effect_ref,
                "amount": amount_base_units_to_decimal_string(
                    effect.amount_base_units,
                    effect.token_decimals,
                ),
                "asset": "USDC",
                "effect_state": effect.state.value,
                "continuation_action": decision.action.value,
                "reason": decision.reason_code,
            }
        )
    return {
        "snapshot": "NEXUS_VECTOR_RUNTIME_EVIDENCE_PLAN_V1",
        "mission_ref": mission.record.request.mission_ref,
        "mission_state": mission.record.state.value,
        "chain": "Base Sepolia",
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "effects": effects,
        "total_amount": amount_base_units_to_decimal_string(
            continuation.total_amount_base_units,
            BASE_SEPOLIA_USDC_DECIMALS,
        ),
        "provider_calls": {
            "simulation_posts": 0,
            "broadcast_posts": 0,
            "funds_moved": False,
        },
        "private_values": "REDACTED_BY_CONSTRUCTION",
    }


def sanitized_selection_snapshot(
    selection: BoundedEffectSelection,
) -> dict[str, Any]:
    """Return the bounded selected-effect plan without private identifiers."""

    if not isinstance(selection, BoundedEffectSelection):
        _fail("INVALID_EFFECT_SELECTION")
    return {
        "snapshot": "NEXUS_VECTOR_BOUNDED_EFFECT_SELECTION_V1",
        "effect_ref": selection.effect_ref,
        "amount": selection.amount_decimal_string,
        "asset": "USDC",
        "chain": "Base Sepolia",
        "chain_id": BASE_SEPOLIA_CHAIN_ID,
        "request_key_binding": "DETERMINISTIC_EFFECT_DERIVED",
        "maximum_simulation_posts": selection.maximum_simulation_posts,
        "maximum_broadcast_posts": selection.maximum_broadcast_posts,
        "network_calls_performed": selection.network_calls_performed,
        "broadcast_authorized": False,
        "private_values": "REDACTED_BY_CONSTRUCTION",
    }
