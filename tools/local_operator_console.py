#!/usr/bin/env python3
"""Read-only localhost UI for sanitized Nexus Vector runtime evidence.

This process intentionally has no KeeperHub transport, no write endpoints, no
credential surface, and no broadcast capability. It serves static assets and
strictly validated sanitized JSON snapshots from files selected by the local
operator at process start.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "operator_console"

_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
_ALLOWED_BOUNDARY_KEYS = frozenset(
    {
        "authorization_state",
        "broadcast_authorized",
        "request_fingerprint_binding",
    }
)
_FORBIDDEN_KEY_FRAGMENTS = (
    "api_key",
    "private_key",
    "seed",
    "mnemonic",
    "authorization",
    "secret",
    "recipient_address",
    "sender_address",
    "token_address",
    "request_key",
    "request_fingerprint",
)

_CANARY_TOP_LEVEL = frozenset(
    {
        "schema",
        "classification",
        "captured_at_local",
        "probe",
        "purpose",
        "mission_ref",
        "effect_ref",
        "chain",
        "chain_id",
        "asset",
        "amount",
        "status",
        "decision",
        "authorization_state",
        "provider_summary",
        "simulation_posts",
        "broadcast_posts",
        "broadcast_authorized",
        "funds_moved",
        "retry",
        "action_sheet_binding",
        "request_fingerprint_binding",
        "private_values",
        "claim_boundary",
    }
)
_CANARY_PROVIDER = frozenset(
    {
        "gas_estimate",
        "http_status",
        "provider_status",
        "simulated_return_present",
        "success",
        "value",
        "would_revert",
    }
)
_MISSION_TOP_LEVEL = frozenset(
    {
        "snapshot",
        "mission_ref",
        "mission_state",
        "chain",
        "chain_id",
        "effects",
        "total_amount",
        "provider_calls",
        "private_values",
    }
)
_MISSION_EFFECT = frozenset(
    {
        "effect_ref",
        "amount",
        "asset",
        "effect_state",
        "continuation_action",
        "reason",
    }
)
_MISSION_PROVIDER_CALLS = frozenset(
    {"simulation_posts", "broadcast_posts", "funds_moved"}
)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


class OperatorConsoleError(RuntimeError):
    """Machine-classifiable local console failure without private-value echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise OperatorConsoleError(code)


def _walk_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail("NON_STRING_KEY")
            lowered = key.casefold()
            if (
                lowered not in _ALLOWED_BOUNDARY_KEYS
                and any(fragment in lowered for fragment in _FORBIDDEN_KEY_FRAGMENTS)
            ):
                _fail("FORBIDDEN_FIELD")
            _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            _walk_keys(child)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        value = json.loads(raw)
    except FileNotFoundError:
        _fail("EVIDENCE_FILE_NOT_FOUND")
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("EVIDENCE_FILE_INVALID")
    if not isinstance(value, dict):
        _fail("EVIDENCE_ROOT_NOT_OBJECT")
    if _ADDRESS_PATTERN.search(raw) is not None:
        _fail("UNREDACTED_ADDRESS_PRESENT")
    _walk_keys(value)
    return value


def _require_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    return value


def validate_canary_evidence(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one sanitized simulation-only canary result."""

    if frozenset(value) != _CANARY_TOP_LEVEL:
        _fail("INVALID_CANARY_SHAPE")
    if value["schema"] != "nexus-vector.keeperhub-simulation-evidence.v1":
        _fail("UNSUPPORTED_CANARY_SCHEMA")
    if value["classification"] != "SANITIZED_PUBLIC":
        _fail("INVALID_CANARY_CLASSIFICATION")
    if value["probe"] != "KEEPERHUB_RUNTIME_EVIDENCE_CANARY_V1":
        _fail("INVALID_CANARY_PROBE")
    if value["purpose"] != "POST_FIX_PROVIDER_REGRESSION_VALIDATION_V1":
        _fail("INVALID_CANARY_PURPOSE")
    if value["mission_ref"] != "simulation-canary-20260806-v1":
        _fail("INVALID_CANARY_MISSION")
    if value["effect_ref"] != "provider-canary":
        _fail("INVALID_CANARY_EFFECT")
    if value["chain"] != "Base Sepolia" or value["chain_id"] != 84532:
        _fail("INVALID_CANARY_CHAIN")
    if value["asset"] != "USDC" or value["amount"] != "0.000001":
        _fail("INVALID_CANARY_AMOUNT")
    if value["status"] != "PASS":
        _fail("CANARY_NOT_PASS")
    if value["decision"] != "ELIGIBLE_FOR_BROADCAST_APPROVAL":
        _fail("INVALID_CANARY_DECISION")
    if value["authorization_state"] != "ELIGIBLE_FOR_BROADCAST_APPROVAL":
        _fail("INVALID_CANARY_AUTHORIZATION_STATE")
    if value["simulation_posts"] != 1:
        _fail("INVALID_CANARY_SIMULATION_COUNT")
    if value["broadcast_posts"] != 0:
        _fail("CANARY_BROADCAST_PRESENT")
    if value["broadcast_authorized"] is not False:
        _fail("CANARY_BROADCAST_AUTHORIZED")
    if value["funds_moved"] is not False:
        _fail("CANARY_FUNDS_MOVED")
    if value["retry"] != "NOT_REQUIRED":
        _fail("INVALID_CANARY_RETRY")
    if value["action_sheet_binding"] != "MATCH":
        _fail("CANARY_ACTION_SHEET_MISMATCH")
    if value["request_fingerprint_binding"] != "MATCH":
        _fail("CANARY_FINGERPRINT_MISMATCH")
    if value["private_values"] != "REDACTED_BY_CONSTRUCTION":
        _fail("CANARY_REDACTION_FAILED")
    if value["claim_boundary"] != "SIMULATION_ONLY_NOT_TRANSACTION_EVIDENCE":
        _fail("CANARY_CLAIM_BOUNDARY_INVALID")
    _require_string(value["captured_at_local"], "INVALID_CANARY_CAPTURE_TIME")

    provider = value["provider_summary"]
    if not isinstance(provider, dict) or frozenset(provider) != _CANARY_PROVIDER:
        _fail("INVALID_CANARY_PROVIDER_SHAPE")
    _require_string(provider["gas_estimate"], "INVALID_GAS_ESTIMATE")
    if provider["http_status"] != 200:
        _fail("INVALID_PROVIDER_HTTP_STATUS")
    if provider["provider_status"] != "simulated":
        _fail("INVALID_PROVIDER_STATUS")
    if provider["simulated_return_present"] is not True:
        _fail("SIMULATED_RETURN_MISSING")
    if provider["success"] is not True:
        _fail("CANARY_PROVIDER_NOT_SUCCESSFUL")
    if provider["value"] != "0":
        _fail("CANARY_NATIVE_VALUE_NONZERO")
    if provider["would_revert"] is not False:
        _fail("CANARY_WOULD_REVERT")
    return value


def validate_mission_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    """Validate a sanitized, network-free Mission planning snapshot."""

    if frozenset(value) != _MISSION_TOP_LEVEL:
        _fail("INVALID_MISSION_SHAPE")
    if value["snapshot"] != "NEXUS_VECTOR_RUNTIME_EVIDENCE_PLAN_V1":
        _fail("INVALID_MISSION_SNAPSHOT")
    if value["mission_ref"] != "runtime-evidence-001":
        _fail("INVALID_MISSION_REF")
    if value["mission_state"] != "READY_FOR_EXECUTION":
        _fail("INVALID_MISSION_STATE")
    if value["chain"] != "Base Sepolia" or value["chain_id"] != 84532:
        _fail("INVALID_MISSION_CHAIN")
    if value["total_amount"] != "0.19":
        _fail("INVALID_MISSION_TOTAL")
    if value["private_values"] != "REDACTED_BY_CONSTRUCTION":
        _fail("MISSION_REDACTION_FAILED")

    calls = value["provider_calls"]
    if not isinstance(calls, dict) or frozenset(calls) != _MISSION_PROVIDER_CALLS:
        _fail("INVALID_MISSION_PROVIDER_CALLS")
    if calls != {
        "simulation_posts": 0,
        "broadcast_posts": 0,
        "funds_moved": False,
    }:
        _fail("MISSION_PROVIDER_ACTIVITY_PRESENT")

    effects = value["effects"]
    if not isinstance(effects, list) or len(effects) != 2:
        _fail("INVALID_MISSION_EFFECTS")
    normalized: dict[str, dict[str, Any]] = {}
    for effect in effects:
        if not isinstance(effect, dict) or frozenset(effect) != _MISSION_EFFECT:
            _fail("INVALID_MISSION_EFFECT_SHAPE")
        ref = _require_string(effect["effect_ref"], "INVALID_EFFECT_REF")
        if ref in normalized:
            _fail("DUPLICATE_EFFECT_REF")
        if effect["asset"] != "USDC":
            _fail("INVALID_EFFECT_ASSET")
        if effect["effect_state"] != "PLANNED":
            _fail("INVALID_EFFECT_STATE")
        if effect["continuation_action"] != "EXECUTE_MISSING":
            _fail("INVALID_CONTINUATION_ACTION")
        normalized[ref] = effect
    if set(normalized) != {"anna", "mark"}:
        _fail("INVALID_FLAGSHIP_EFFECT_SET")
    if normalized["anna"]["amount"] != "0.12":
        _fail("INVALID_ANNA_AMOUNT")
    if normalized["mark"]["amount"] != "0.07":
        _fail("INVALID_MARK_AMOUNT")
    return value


def _empty_canary() -> dict[str, Any]:
    return {
        "loaded": False,
        "evidence_level": "LIVE_SIMULATION_NOT_LOADED",
        "status": "WAITING_FOR_SANITIZED_EVIDENCE",
        "simulation_posts": 0,
        "broadcast_posts": 0,
        "broadcast_authorized": False,
        "funds_moved": False,
    }


def _empty_mission() -> dict[str, Any]:
    return {
        "loaded": False,
        "evidence_level": "OFFLINE_PLAN_NOT_LOADED",
        "mission_ref": "runtime-evidence-001",
        "mission_state": "NOT_LOADED",
        "chain": "Base Sepolia",
        "chain_id": 84532,
        "effects": [
            {
                "effect_ref": "anna",
                "amount": "0.12",
                "asset": "USDC",
                "effect_state": "UNKNOWN",
                "continuation_action": "LOAD_SANITIZED_PLAN",
                "reason": "READ_ONLY_SOURCE_NOT_CONFIGURED",
            },
            {
                "effect_ref": "mark",
                "amount": "0.07",
                "asset": "USDC",
                "effect_state": "UNKNOWN",
                "continuation_action": "LOAD_SANITIZED_PLAN",
                "reason": "READ_ONLY_SOURCE_NOT_CONFIGURED",
            },
        ],
        "total_amount": "0.19",
        "provider_calls": {
            "simulation_posts": 0,
            "broadcast_posts": 0,
            "funds_moved": False,
        },
    }


@dataclass(frozen=True)
class SnapshotProvider:
    canary_path: Path | None = None
    mission_path: Path | None = None

    def snapshot(self) -> dict[str, Any]:
        canary: dict[str, Any]
        mission: dict[str, Any]
        errors: list[str] = []

        if self.canary_path is None:
            canary = _empty_canary()
        else:
            try:
                canary = dict(validate_canary_evidence(_load_json(self.canary_path)))
                canary["loaded"] = True
                canary["evidence_level"] = "LIVE_SIMULATION"
            except OperatorConsoleError as error:
                canary = _empty_canary()
                canary["status"] = "STOP"
                errors.append(f"CANARY:{error.code}")

        if self.mission_path is None:
            mission = _empty_mission()
        else:
            try:
                mission = dict(validate_mission_snapshot(_load_json(self.mission_path)))
                mission["loaded"] = True
                mission["evidence_level"] = "OFFLINE_PLAN"
            except OperatorConsoleError as error:
                mission = _empty_mission()
                mission["mission_state"] = "STOP"
                errors.append(f"MISSION:{error.code}")

        return {
            "schema": "nexus-vector.operator-console.snapshot.v1",
            "surface": "LOCAL_READ_ONLY_OPERATOR_CONSOLE",
            "mode": "LIVE_TESTNET_RUNTIME_READ_ONLY",
            "host": HOST,
            "chain": "Base Sepolia",
            "chain_id": 84532,
            "mainnet_blocked": True,
            "browser_capabilities": {
                "keeperhub_transport": False,
                "credential_access": False,
                "signing": False,
                "broadcast": False,
                "filesystem_selection": False,
                "write_endpoints": False,
            },
            "canary": canary,
            "mission": mission,
            "errors": errors,
        }


def _safe_asset_path(raw_path: str) -> Path | None:
    path = raw_path or "/"
    if path == "/":
        path = "/index.html"
    pure = PurePosixPath(path.lstrip("/"))
    if pure.is_absolute() or ".." in pure.parts or len(pure.parts) != 1:
        return None
    target = ASSET_ROOT / pure.name
    if target.suffix not in _CONTENT_TYPES:
        return None
    return target


class _Handler(BaseHTTPRequestHandler):
    server_version = "NexusVectorOperatorConsole/1.0"
    provider: SnapshotProvider

    def log_message(self, format: str, *args: object) -> None:
        print(f"OPERATOR_CONSOLE_HTTP: {format % args}", file=sys.stderr)

    def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
        )
        self.end_headers()

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"status": "STOP", "reason": "QUERY_FORBIDDEN"},
            )
            return
        if parsed.path == "/api/runtime/snapshot":
            self._write_json(HTTPStatus.OK, self.provider.snapshot())
            return
        if parsed.path == "/api/runtime/health":
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "PASS",
                    "surface": "LOCAL_READ_ONLY_OPERATOR_CONSOLE",
                    "host": HOST,
                    "write_endpoints": False,
                },
            )
            return

        target = _safe_asset_path(parsed.path)
        if target is None:
            self._write_json(
                HTTPStatus.NOT_FOUND,
                {"status": "STOP", "reason": "NOT_FOUND"},
            )
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._write_json(
                HTTPStatus.NOT_FOUND,
                {"status": "STOP", "reason": "ASSET_NOT_FOUND"},
            )
            return
        self._headers(HTTPStatus.OK, _CONTENT_TYPES[target.suffix], len(body))
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        self._write_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {
                "status": "STOP",
                "reason": "READ_ONLY_CONSOLE_NO_WRITE_ENDPOINTS",
                "provider_calls": 0,
                "funds_moved": False,
            },
        )

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def build_server(
    provider: SnapshotProvider,
    *,
    port: int = DEFAULT_PORT,
) -> ThreadingHTTPServer:
    if not isinstance(provider, SnapshotProvider):
        _fail("INVALID_SNAPSHOT_PROVIDER")
    if type(port) is not int or not 0 <= port <= 65535:
        _fail("INVALID_PORT")

    class BoundHandler(_Handler):
        pass

    BoundHandler.provider = provider
    server = ThreadingHTTPServer((HOST, port), BoundHandler)
    server.daemon_threads = True
    return server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Serve the Nexus Vector Local Operator Console on 127.0.0.1. "
            "This phase is GET-only and has no KeeperHub execution capability."
        )
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--canary-evidence", type=Path)
    parser.add_argument("--mission-snapshot", type=Path)
    parser.add_argument("--open-browser", action="store_true")
    return parser


def _remove_inherited_authority() -> None:
    for name in (
        "KEEPERHUB_API_KEY",
        "NEXUS_VECTOR_CANARY_APPROVAL",
        "NEXUS_VECTOR_BROADCAST_APPROVAL",
        "NEXUS_VECTOR_APPROVE_TESTNET_WRITE",
    ):
        os.environ.pop(name, None)


def main() -> int:
    arguments = _parser().parse_args()
    _remove_inherited_authority()
    provider = SnapshotProvider(
        canary_path=arguments.canary_evidence,
        mission_path=arguments.mission_snapshot,
    )
    try:
        server = build_server(provider, port=arguments.port)
    except (OperatorConsoleError, OSError) as error:
        code = error.code if isinstance(error, OperatorConsoleError) else "SERVER_BIND_FAILED"
        print(json.dumps({"status": "STOP", "reason": code}, sort_keys=True))
        return 2

    actual_port = int(server.server_address[1])
    url = f"http://{HOST}:{actual_port}/"
    print(
        json.dumps(
            {
                "status": "PASS",
                "surface": "LOCAL_READ_ONLY_OPERATOR_CONSOLE",
                "url": url,
                "host": HOST,
                "write_endpoints": False,
                "keeperhub_transport": False,
                "broadcast_capability": False,
                "funds_moved": False,
            },
            sort_keys=True,
        )
    )
    if arguments.open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print("LOCAL_OPERATOR_CONSOLE: STOPPED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
