"""Zero-network recovery preflight for Anna/Mark/Leo live-video Mission.

This adapter reuses the reviewed Anna/Mark local durable-state preflight while
binding it to the three-effect Mission schema/root and effect set. It has no
credential loading, network or signing capability.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_BASE_MODULE_PATH = Path(__file__).with_name("anna_mark_video_operator_preflight.py")
_BASE_MODULE_NAME = "_nexus_vector_anna_mark_preflight_for_anna_mark_leo_v1"
_spec = importlib.util.spec_from_file_location(_BASE_MODULE_NAME, _BASE_MODULE_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError("ANNA_MARK_PREFLIGHT_BASE_MODULE_LOAD_FAILED")
base = importlib.util.module_from_spec(_spec)
sys.modules[_BASE_MODULE_NAME] = base
_spec.loader.exec_module(base)

base._SCHEMA = "nexus-vector.anna-mark-leo-video-operator-preflight.v1"
base._MISSION_SCHEMA = "nexus-vector.anna-mark-leo-video-mission.v1"
base._EFFECTS = ("anna", "mark", "leo")


def _run_root(run_ref: str, base_root: Path | None = None) -> Path:
    if not isinstance(run_ref, str) or base._RUN_REF_PATTERN.fullmatch(run_ref) is None:
        base._fail("INVALID_RUN_REF")
    return (
        base_root
        or (Path.home() / ".nexus-vector" / "anna-mark-leo-video-mission-v1")
    ) / run_ref


base._run_root = _run_root

simulation_preflight = base.simulation_preflight
broadcast_preflight = base.broadcast_preflight
OperatorPreflightError = base.OperatorPreflightError


if __name__ == "__main__":
    raise SystemExit(base.main())
