"""Run one bounded, sanitized KeeperHub readiness probe.

This command performs read-only requests only. It has no simulation, signing,
broadcast, Workflow, MCP, x402, Marketplace, or mainnet execution capability.

The organization key is read once from ``KEEPERHUB_API_KEY`` and removed from
this process environment before any request. It is never printed, logged, or
serialized.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexus_vector.integrations.keeperhub_http_transport import (  # noqa: E402
    KeeperHubHttpTransport,
    KeeperHubHttpTransportError,
)
from nexus_vector.integrations.keeperhub_simulation_runtime import (  # noqa: E402
    KeeperHubReadOnlyRuntimeClient,
    KeeperHubSimulationRuntimeError,
)

_API_KEY_ENV = "KEEPERHUB_API_KEY"
_BASE_SEPOLIA_CHAIN_ID = 84532
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_ALLOWED_BALANCE_FIELDS = frozenset(
    {
        "balance",
        "balancewei",
        "chainid",
        "chainname",
        "decimals",
        "error",
        "isnative",
        "istestnet",
        "name",
        "nativebalance",
        "nativebalanceraw",
        "network",
        "success",
        "supportedtokens",
        "symbol",
        "tokenaddress",
        "type",
    }
)
_SAFE_BALANCE_TEXT_FIELDS = frozenset(
    {
        "balance",
        "balancewei",
        "chainname",
        "error",
        "name",
        "nativebalance",
        "nativebalanceraw",
        "network",
        "symbol",
        "type",
    }
)
_MAX_SAFE_BALANCE_TEXT = 256
_MAX_SANITIZE_NODES = 10_000


class ReadinessClient(Protocol):
    def get_wallet_readiness(self): ...

    def list_chains(self): ...

    def get_wallet_balances(self): ...


def _mask_address(value: str) -> str:
    if _EVM_ADDRESS.fullmatch(value) is None:
        return "<redacted>"
    return f"{value[:8]}…{value[-6:]}"


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _safe_balance_text(value: str, key_hint: str | None) -> str:
    if key_hint not in _SAFE_BALANCE_TEXT_FIELDS:
        return "<redacted>"
    if (
        not value
        or len(value) > _MAX_SAFE_BALANCE_TEXT
        or value.strip() != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return "<redacted>"
    return value


def _sanitize_balance_payload(value: Any) -> Any:
    """Keep bounded balance facts while redacting identifiers and unknown fields."""

    visited = 0

    def sanitize(current: Any, key_hint: str | None = None) -> Any:
        nonlocal visited
        visited += 1
        if visited > _MAX_SANITIZE_NODES:
            raise ValueError("BALANCE_PAYLOAD_TOO_COMPLEX")

        if isinstance(current, Mapping):
            result: dict[str, Any] = {}
            for key, item in current.items():
                if not isinstance(key, str):
                    raise ValueError("INVALID_BALANCE_RESPONSE_KEY")
                normalized = _normalized_key(key)
                if normalized in _ALLOWED_BALANCE_FIELDS:
                    result[key] = sanitize(item, normalized)
                elif normalized in {"balances", "tokens", "data", "items", "result"}:
                    result[key] = sanitize(item, normalized)
                else:
                    result[key] = "<redacted>"
            return result
        if isinstance(current, (list, tuple)):
            return [sanitize(item, key_hint) for item in current]
        if isinstance(current, str):
            if _EVM_ADDRESS.fullmatch(current):
                return _mask_address(current)
            return _safe_balance_text(current, key_hint)
        if current is None or type(current) in {bool, int, float}:
            return current
        return "<redacted>"

    return sanitize(value)


def _transport_stop(error: KeeperHubHttpTransportError) -> dict[str, Any]:
    result: dict[str, Any] = {
        "probe": "KEEPERHUB_READINESS_V1",
        "status": "STOP",
        "reason": error.code,
        "retry": "FORBIDDEN",
    }
    if error.http_status is not None:
        result["http_status"] = error.http_status
    if error.provider_error_code is not None:
        result["provider_error_code"] = error.provider_error_code
    return result


def run_probe(client: ReadinessClient) -> tuple[int, dict[str, Any]]:
    """Run each approved GET at most once and return sanitized evidence."""

    result: dict[str, Any] = {
        "probe": "KEEPERHUB_READINESS_V1",
        "requests": {
            "wallet": "NOT_CALLED",
            "chains": "NOT_CALLED",
            "balances": "NOT_CALLED",
        },
        "status": "STOP",
    }

    readiness = client.get_wallet_readiness()
    result["requests"]["wallet"] = "PASS"
    result["wallet"] = {
        "has_wallet": readiness.has_wallet,
        "is_active": readiness.is_active,
        "ready": readiness.ready,
        "wallet_address_masked": (
            _mask_address(readiness.wallet_address)
            if readiness.wallet_address is not None
            else None
        ),
    }
    if not readiness.ready:
        result["reason"] = "WALLET_NOT_READY"
        return 2, result

    chains = client.list_chains()
    result["requests"]["chains"] = "PASS"
    eligible = tuple(
        chain
        for chain in chains
        if chain.eligible_for_testnet_execution
    )
    result["eligible_testnets"] = [
        {
            "chain_id": chain.chain_id,
            "name": chain.name,
            "chain_type": chain.chain_type,
        }
        for chain in eligible
    ]
    if not any(chain.chain_id == _BASE_SEPOLIA_CHAIN_ID for chain in eligible):
        result["reason"] = "BASE_SEPOLIA_NOT_ELIGIBLE"
        return 2, result

    balances = client.get_wallet_balances()
    result["requests"]["balances"] = "PASS"
    result["balances_sanitized"] = _sanitize_balance_payload(balances)
    result["status"] = "PASS"
    result["reason"] = "READINESS_SURFACES_PASS"
    return 0, result


def main() -> int:
    api_key = os.environ.pop(_API_KEY_ENV, None)
    if api_key is None:
        print(
            json.dumps(
                {
                    "probe": "KEEPERHUB_READINESS_V1",
                    "status": "STOP",
                    "reason": "LOCAL_API_KEY_NOT_SET",
                },
                sort_keys=True,
            )
        )
        return 2

    try:
        transport = KeeperHubHttpTransport(api_key)
        client = KeeperHubReadOnlyRuntimeClient(transport)
        exit_code, result = run_probe(client)
    except KeeperHubHttpTransportError as error:
        exit_code = 2
        result = _transport_stop(error)
    except KeeperHubSimulationRuntimeError as error:
        exit_code = 2
        result = {
            "probe": "KEEPERHUB_READINESS_V1",
            "status": "STOP",
            "reason": error.code,
            "retry": "FORBIDDEN",
        }
    except ValueError as error:
        exit_code = 2
        result = {
            "probe": "KEEPERHUB_READINESS_V1",
            "status": "STOP",
            "reason": str(error),
            "retry": "FORBIDDEN",
        }
    except Exception:
        exit_code = 2
        result = {
            "probe": "KEEPERHUB_READINESS_V1",
            "status": "STOP",
            "reason": "UNEXPECTED_READINESS_FAILURE",
            "retry": "FORBIDDEN",
        }
    finally:
        api_key = None

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
