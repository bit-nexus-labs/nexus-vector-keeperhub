"""Durable state and exact network plan for the MCP OAuth read probe."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _keeperhub_mcp_oauth_runtime import (
    AUTH_METADATA_URL,
    MCP_URL,
    PROBE,
    REGISTER_URL,
    RESOURCE_METADATA_URL,
    TOKEN_URL,
    ProbeError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = (
    REPO_ROOT
    / "results_private"
    / "keeperhub_mcp_oauth_read_handshake_v1_state.json"
)


@dataclass(frozen=True)
class _PlannedRequest:
    method: str
    url: str
    bucket: str


_REQUEST_PLAN = {
    "RESOURCE_DISCOVERY": _PlannedRequest(
        "GET", RESOURCE_METADATA_URL, "discovery_gets"
    ),
    "OAUTH_DISCOVERY": _PlannedRequest(
        "GET", AUTH_METADATA_URL, "discovery_gets"
    ),
    "CLIENT_REGISTRATION": _PlannedRequest(
        "POST", REGISTER_URL, "registration_posts"
    ),
    "TOKEN_EXCHANGE": _PlannedRequest("POST", TOKEN_URL, "token_posts"),
    "MCP_INITIALIZE": _PlannedRequest("POST", MCP_URL, "mcp_posts"),
    "MCP_INITIALIZED_NOTIFICATION": _PlannedRequest(
        "POST", MCP_URL, "mcp_posts"
    ),
    "MCP_TOOLS_LIST": _PlannedRequest("POST", MCP_URL, "mcp_posts"),
    "MCP_SESSION_CLEANUP": _PlannedRequest(
        "DELETE", MCP_URL, "mcp_deletes"
    ),
}

_MCP_STAGE_METHODS = {
    "MCP_INITIALIZE": "initialize",
    "MCP_INITIALIZED_NOTIFICATION": "notifications/initialized",
    "MCP_TOOLS_LIST": "tools/list",
}


class _NetworkBudget:
    """Fail closed before any request outside the exact diagnostic plan."""

    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self._consumed_stages: set[str] = set()

    def consume(
        self,
        stage: str,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
    ) -> str:
        planned = _REQUEST_PLAN.get(stage)
        if planned is None:
            raise ProbeError("UNPLANNED_NETWORK_STAGE", stage=stage)
        if stage in self._consumed_stages:
            raise ProbeError("NETWORK_STAGE_ALREADY_CONSUMED", stage=stage)
        if method != planned.method or url != planned.url:
            raise ProbeError("NETWORK_REQUEST_PLAN_MISMATCH", stage=stage)
        expected_rpc_method = _MCP_STAGE_METHODS.get(stage)
        if expected_rpc_method is not None:
            try:
                payload = json.loads((body or b"").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ProbeError("INVALID_PLANNED_MCP_BODY", stage=stage) from None
            if (
                not isinstance(payload, Mapping)
                or payload.get("jsonrpc") != "2.0"
                or payload.get("method") != expected_rpc_method
            ):
                raise ProbeError("MCP_METHOD_PLAN_MISMATCH", stage=stage)
        self._consumed_stages.add(stage)
        self._counts[planned.bucket] += 1
        return planned.bucket


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: Mapping[str, Any], *, create: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ProbeError("LOCAL_STATE_PATH_IS_SYMLINK", stage="LOCAL_PREFLIGHT")
    raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    if create:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            raise ProbeError(
                "DURABLE_CLAIM_ALREADY_EXISTS",
                stage="LOCAL_PREFLIGHT",
            ) from None
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ProbeError(
                "LOCAL_STATE_CLAIM_FAILED",
                stage="LOCAL_PREFLIGHT",
            ) from None
        return

    if not path.is_file():
        raise ProbeError("LOCAL_STATE_MISSING", stage="LOCAL_STATE_FINALIZE")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProbeError(
            "LOCAL_STATE_FINALIZE_FAILED",
            stage="LOCAL_STATE_FINALIZE",
        ) from None


def _create_claim(path: Path, run_id: str) -> None:
    _atomic_write_json(
        path,
        {
            "probe": PROBE,
            "run_id": run_id,
            "state": "CLAIMED",
            "created_at_utc": _utc_now(),
            "retry": "FORBIDDEN_WITHOUT_MANUAL_RECOVERY",
        },
        create=True,
    )


def _finalize_claim(path: Path, result: Mapping[str, Any]) -> None:
    safe_fields = {
        "probe": PROBE,
        "state": "TERMINAL",
        "completed_at_utc": _utc_now(),
        "status": result.get("status"),
        "reason": result.get("reason"),
        "stage": result.get("stage"),
        "scripted_requests": result.get("scripted_requests"),
        "oauth_client_record_created": result.get(
            "oauth_client_record_created", False
        ),
        "session_cleanup": result.get("session_cleanup"),
        "funds_moved": False,
        "retry": "FORBIDDEN_WITHOUT_MANUAL_RECOVERY",
    }
    _atomic_write_json(path, safe_fields, create=False)


def _base_result(run_id: str, counts: Mapping[str, int]) -> dict[str, Any]:
    return {
        "probe": PROBE,
        "support_run_id": run_id,
        "scope_requested": "mcp:read",
        "scripted_requests": dict(counts),
        "browser_flow": "USER_INTERACTIVE",
        "oauth_client_record_created": False,
        "tool_calls": 0,
        "execute_calls": 0,
        "workflow_calls": 0,
        "simulation_posts": 0,
        "broadcast_posts": 0,
        "signing_requests": 0,
        "funds_moved": False,
        "access_token_persisted": False,
        "refresh_token_persisted": False,
        "mcp_session_persisted": False,
    }
