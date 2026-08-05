"""Verify local KeeperHub wallet bindings before any simulation is prepared.

This tool is deliberately narrower than the general readiness probe. It:

* validates the private local wallet registry before any network request;
* performs at most one read-only ``GET /api/user/wallet`` request;
* proves that the live KeeperHub Organization Wallet matches the locally
  recorded sender;
* proves that sender, recipient, and the pinned Base Sepolia USDC token
  contract are three distinct EVM addresses;
* has no simulation, signing, broadcast, Workflow, MCP, x402, Marketplace,
  mainnet, or funds-moving capability.

No full wallet address, API key, organization identifier, or raw provider body
is printed or serialized.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexus_vector.integrations.keeperhub_http_transport import (  # noqa: E402
    KeeperHubHttpTransport,
    KeeperHubHttpTransportError,
    KeeperHubWalletReadiness,
)

_API_KEY_ENV = "KEEPERHUB_API_KEY"
_BASE_SEPOLIA_CHAIN_ID = 84532
_BASE_SEPOLIA_USDC = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_MAX_REGISTRY_BYTES = 32_768

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "updated_at_utc",
        "network",
        "wallets",
        "tokens",
        "safety",
    }
)
_NETWORK_KEYS = frozenset({"name", "chain_id", "environment"})
_WALLET_KEYS = frozenset(
    {"keeperhub_organization_wallet", "personal_recipient_wallet"}
)
_TOKEN_CONTAINER_KEYS = frozenset({"base_sepolia_usdc"})
_TOKEN_KEYS = frozenset(
    {"role", "symbol", "decimals", "contract_address"}
)
_SAFETY_KEYS = frozenset(
    {
        "mainnet_blocked",
        "contains_seed_phrase",
        "contains_wallet_private_key",
        "contains_turnkey_signing_key",
        "api_key_storage",
    }
)


class WalletBindingPreflightError(RuntimeError):
    """Machine-classifiable local failure without sensitive-value echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise WalletBindingPreflightError(code)


def _canonical_address(value: Any, code: str) -> str:
    if not isinstance(value, str) or _EVM_ADDRESS.fullmatch(value) is None:
        _fail(code)
    return value.casefold()


def _exact_mapping(value: Any, expected: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value.keys()) != expected:
        _fail(code)
    return value


def _bounded_text(value: Any, code: str, *, maximum: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(code)
    return value


@dataclass(frozen=True)
class LocalWalletBinding:
    keeperhub_sender: str
    personal_recipient: str
    token_contract: str
    chain_id: int


def _parse_registry(payload: Any) -> LocalWalletBinding:
    root = _exact_mapping(payload, _TOP_LEVEL_KEYS, "INVALID_REGISTRY_SCHEMA")

    if root["schema_version"] != 1:
        _fail("UNSUPPORTED_REGISTRY_VERSION")
    _bounded_text(root["updated_at_utc"], "INVALID_REGISTRY_TIMESTAMP")

    network = _exact_mapping(
        root["network"],
        _NETWORK_KEYS,
        "INVALID_REGISTRY_NETWORK",
    )
    if (
        network["name"] != "Base Sepolia"
        or network["chain_id"] != _BASE_SEPOLIA_CHAIN_ID
        or network["environment"] != "testnet"
    ):
        _fail("BASE_SEPOLIA_BINDING_REQUIRED")

    wallets = _exact_mapping(
        root["wallets"],
        _WALLET_KEYS,
        "INVALID_REGISTRY_WALLETS",
    )
    sender = _canonical_address(
        wallets["keeperhub_organization_wallet"],
        "INVALID_KEEPERHUB_SENDER",
    )
    recipient = _canonical_address(
        wallets["personal_recipient_wallet"],
        "INVALID_PERSONAL_RECIPIENT",
    )

    tokens = _exact_mapping(
        root["tokens"],
        _TOKEN_CONTAINER_KEYS,
        "INVALID_REGISTRY_TOKENS",
    )
    token = _exact_mapping(
        tokens["base_sepolia_usdc"],
        _TOKEN_KEYS,
        "INVALID_BASE_SEPOLIA_USDC_BINDING",
    )
    token_contract = _canonical_address(
        token["contract_address"],
        "INVALID_BASE_SEPOLIA_USDC_CONTRACT",
    )
    if (
        token["role"] != "TOKEN_CONTRACT_NOT_WALLET"
        or token["symbol"] != "USDC"
        or token["decimals"] != 6
        or token_contract != _BASE_SEPOLIA_USDC
    ):
        _fail("BASE_SEPOLIA_USDC_BINDING_MISMATCH")

    safety = _exact_mapping(
        root["safety"],
        _SAFETY_KEYS,
        "INVALID_REGISTRY_SAFETY",
    )
    if safety["mainnet_blocked"] is not True:
        _fail("MAINNET_MUST_BE_BLOCKED")
    if (
        safety["contains_seed_phrase"] is not False
        or safety["contains_wallet_private_key"] is not False
        or safety["contains_turnkey_signing_key"] is not False
    ):
        _fail("PRIVATE_SIGNING_MATERIAL_DECLARATION_INVALID")
    if safety["api_key_storage"] != "WINDOWS_DPAPI_CLIXML":
        _fail("UNAPPROVED_API_KEY_STORAGE")

    if sender == recipient:
        _fail("SENDER_RECIPIENT_COLLISION")
    if sender == token_contract:
        _fail("SENDER_TOKEN_CONTRACT_COLLISION")
    if recipient == token_contract:
        _fail("RECIPIENT_TOKEN_CONTRACT_COLLISION")

    return LocalWalletBinding(
        keeperhub_sender=sender,
        personal_recipient=recipient,
        token_contract=token_contract,
        chain_id=_BASE_SEPOLIA_CHAIN_ID,
    )


def _load_registry(path: Path) -> LocalWalletBinding:
    if not isinstance(path, Path):
        _fail("INVALID_REGISTRY_PATH")
    try:
        if path.is_symlink() or not path.is_file():
            _fail("LOCAL_WALLET_REGISTRY_NOT_FOUND")
        size = path.stat().st_size
        if size < 2 or size > _MAX_REGISTRY_BYTES:
            _fail("INVALID_REGISTRY_SIZE")
        raw = path.read_bytes()
    except WalletBindingPreflightError:
        raise
    except OSError:
        _fail("LOCAL_WALLET_REGISTRY_UNREADABLE")

    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("INVALID_REGISTRY_JSON")
    return _parse_registry(payload)


def _default_registry_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not isinstance(local_app_data, str) or not local_app_data:
        _fail("LOCALAPPDATA_NOT_SET")
    return (
        Path(local_app_data)
        / "NexusVector"
        / "Config"
        / "wallets.private-local.json"
    )


class WalletReadinessClient(Protocol):
    def get_wallet_readiness(self) -> KeeperHubWalletReadiness: ...


class KeeperHubWalletBindingReadOnlyClient:
    """Expose exactly one KeeperHub read surface to this preflight."""

    def __init__(self, transport: KeeperHubHttpTransport) -> None:
        if not isinstance(transport, KeeperHubHttpTransport):
            _fail("INVALID_KEEPERHUB_HTTP_TRANSPORT")
        self._transport = transport

    def get_wallet_readiness(self) -> KeeperHubWalletReadiness:
        return self._transport.get_wallet_readiness()


def _base_result() -> dict[str, Any]:
    return {
        "probe": "KEEPERHUB_WALLET_BINDING_PREFLIGHT_V1",
        "status": "STOP",
        "reason": "NOT_RUN",
        "requests": {"wallet": "NOT_CALLED"},
        "get_requests": 0,
        "post_requests": 0,
        "simulation_posts": 0,
        "broadcast_posts": 0,
        "funds_moved": False,
    }


def run_preflight(
    client: WalletReadinessClient,
    binding: LocalWalletBinding,
) -> tuple[int, dict[str, Any]]:
    result = _base_result()
    result["local_registry"] = {
        "schema_version": 1,
        "chain_id": binding.chain_id,
        "chain_binding": "MATCH",
        "mainnet_blocked": True,
        "token_contract_binding": "MATCH",
        "recipient_shape": "PASS",
        "recipient_is_distinct": True,
    }

    readiness = client.get_wallet_readiness()
    result["get_requests"] = 1
    result["requests"]["wallet"] = "PASS"

    if not isinstance(readiness, KeeperHubWalletReadiness):
        result["reason"] = "INVALID_WALLET_READINESS_RESULT"
        result["retry"] = "FORBIDDEN"
        return 2, result
    if not readiness.ready or readiness.wallet_address is None:
        result["reason"] = "KEEPERHUB_WALLET_NOT_READY"
        result["retry"] = "REVIEW_BEFORE_REPEAT"
        return 2, result

    if readiness.wallet_address.casefold() != binding.keeperhub_sender:
        result["keeperhub_wallet_binding"] = "MISMATCH"
        result["reason"] = "KEEPERHUB_WALLET_BINDING_MISMATCH"
        result["retry"] = "MANUAL_LOCAL_REVIEW_REQUIRED"
        return 2, result

    result["keeperhub_wallet_binding"] = "MATCH"
    result["status"] = "PASS"
    result["reason"] = "WALLET_BINDINGS_VERIFIED"
    result["retry"] = "NOT_REQUIRED"
    return 0, result


def _transport_failure(error: KeeperHubHttpTransportError) -> dict[str, Any]:
    result = _base_result()
    result["get_requests"] = 1
    result["requests"]["wallet"] = "OUTCOME_UNKNOWN"
    result["reason"] = error.code
    result["retry"] = (
        "REVIEW_BEFORE_REPEAT"
        if error.code == "NETWORK_OUTCOME_UNKNOWN"
        else "FORBIDDEN"
    )
    if error.code == "NETWORK_OUTCOME_UNKNOWN":
        result["status"] = "OUTCOME_UNKNOWN"
    if error.http_status is not None:
        result["http_status"] = error.http_status
    if error.provider_error_code is not None:
        result["provider_error_code"] = error.provider_error_code
    return result


def main() -> int:
    api_key = os.environ.pop(_API_KEY_ENV, None)

    try:
        binding = _load_registry(_default_registry_path())
        if api_key is None:
            _fail("LOCAL_API_KEY_NOT_SET")

        transport = KeeperHubHttpTransport(api_key)
        client = KeeperHubWalletBindingReadOnlyClient(transport)
        exit_code, result = run_preflight(client, binding)
    except WalletBindingPreflightError as error:
        exit_code = 2
        result = _base_result()
        result["reason"] = error.code
        result["retry"] = "LOCAL_CORRECTION_REQUIRED"
    except KeeperHubHttpTransportError as error:
        exit_code = 2
        result = _transport_failure(error)
    except Exception:
        exit_code = 2
        result = _base_result()
        result["reason"] = "UNEXPECTED_WALLET_BINDING_FAILURE"
        result["retry"] = "FORBIDDEN"
    finally:
        api_key = None

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
