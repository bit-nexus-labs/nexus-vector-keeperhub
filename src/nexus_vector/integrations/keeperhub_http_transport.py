"""Bounded KeeperHub HTTPS transport with explicit credential injection."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

from nexus_vector.integrations.keeperhub_direct_execution import (
    KeeperHubTransportResponse,
)

_KEEPERHUB_BASE_URL = "https://app.keeperhub.com/api"
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_API_KEY_LENGTH = 512
_MAX_TIMEOUT_SECONDS = 60
_EVM_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")


class KeeperHubHttpTransportError(RuntimeError):
    """Machine-classifiable transport failure without secret or body echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise KeeperHubHttpTransportError(code)


def _required_text(value: Any, code: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(code)
    return value


def _api_key(value: Any) -> str:
    key = _required_text(value, "INVALID_API_KEY", maximum=_MAX_API_KEY_LENGTH)
    if not key.startswith("kh_") or len(key) <= 3:
        _fail("INVALID_API_KEY")
    return key


def _canonical_address(value: Any) -> str:
    if not isinstance(value, str) or _EVM_ADDRESS_PATTERN.fullmatch(value) is None:
        _fail("INVALID_WALLET_ADDRESS")
    return value.casefold()


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _fail("HTTP_REDIRECT_BLOCKED")


class _Opener(Protocol):
    def open(self, request: Request, *, timeout: float): ...


@dataclass(frozen=True)
class KeeperHubWalletReadiness:
    has_wallet: bool
    is_active: bool
    wallet_address: str | None
    organization_id: str | None

    @property
    def ready(self) -> bool:
        return (
            self.has_wallet
            and self.is_active
            and self.wallet_address is not None
            and self.organization_id is not None
        )


@dataclass(frozen=True)
class KeeperHubChain:
    chain_id: int
    name: str
    chain_type: str
    is_testnet: bool
    is_enabled: bool
    explorer_url: str | None

    @property
    def eligible_for_testnet_execution(self) -> bool:
        return (
            self.chain_type == "evm"
            and self.is_testnet
            and self.is_enabled
        )


class KeeperHubHttpTransport:
    """No-retry JSON transport for approved KeeperHub REST surfaces."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 10.0,
        opener: _Opener | None = None,
        base_url: str = _KEEPERHUB_BASE_URL,
    ) -> None:
        if base_url != _KEEPERHUB_BASE_URL:
            _fail("UNAPPROVED_BASE_URL")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < float(timeout_seconds) <= _MAX_TIMEOUT_SECONDS
        ):
            _fail("INVALID_TIMEOUT")
        if opener is not None and not callable(getattr(opener, "open", None)):
            _fail("INVALID_HTTP_OPENER")
        self._api_key = _api_key(api_key)
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener or build_opener(_NoRedirectHandler())
        self._base_url = base_url

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(base_url={self._base_url!r}, "
            f"timeout_seconds={self._timeout_seconds!r})"
        )

    def post_transfer(
        self,
        body: Mapping[str, Any],
        *,
        idempotency_key: str | None,
    ) -> KeeperHubTransportResponse:
        if not isinstance(body, Mapping):
            _fail("INVALID_TRANSFER_BODY")
        if idempotency_key is not None:
            _required_text(
                idempotency_key,
                "INVALID_IDEMPOTENCY_KEY",
                maximum=256,
            )
        status, payload, headers = self._json_request(
            method="POST",
            path="/execute/transfer",
            body=dict(body),
            idempotency_key=idempotency_key,
        )
        if not isinstance(payload, Mapping):
            _fail("INVALID_TRANSFER_RESPONSE")
        return KeeperHubTransportResponse(status, payload, headers)

    def get_execution_status(
        self,
        provider_reference: str,
    ) -> KeeperHubTransportResponse:
        reference = _required_text(
            provider_reference,
            "INVALID_PROVIDER_REFERENCE",
            maximum=256,
        )
        encoded = quote(reference, safe="")
        status, payload, headers = self._json_request(
            method="GET",
            path=f"/execute/{encoded}/status",
        )
        if not isinstance(payload, Mapping):
            _fail("INVALID_STATUS_RESPONSE")
        return KeeperHubTransportResponse(status, payload, headers)

    def get_wallet_readiness(self) -> KeeperHubWalletReadiness:
        status, payload, _ = self._json_request(
            method="GET",
            path="/user/wallet",
        )
        if status != 200 or not isinstance(payload, Mapping):
            _fail("WALLET_READINESS_UNKNOWN")
        has_wallet = payload.get("hasWallet")
        if type(has_wallet) is not bool:
            _fail("INVALID_WALLET_RESPONSE")
        if not has_wallet:
            return KeeperHubWalletReadiness(
                has_wallet=False,
                is_active=False,
                wallet_address=None,
                organization_id=None,
            )
        is_active = payload.get("isActive")
        if type(is_active) is not bool:
            _fail("INVALID_WALLET_RESPONSE")
        organization_id = _required_text(
            payload.get("organizationId"),
            "INVALID_ORGANIZATION_ID",
        )
        return KeeperHubWalletReadiness(
            has_wallet=True,
            is_active=is_active,
            wallet_address=_canonical_address(payload.get("walletAddress")),
            organization_id=organization_id,
        )

    def list_chains(self) -> tuple[KeeperHubChain, ...]:
        status, payload, _ = self._json_request(
            method="GET",
            path="/chains",
        )
        if status != 200 or not isinstance(payload, list):
            _fail("CHAIN_CATALOG_UNKNOWN")
        chains: list[KeeperHubChain] = []
        seen_ids: set[int] = set()
        for item in payload:
            if not isinstance(item, Mapping):
                _fail("INVALID_CHAIN_RESPONSE")
            chain_id = item.get("chainId")
            if type(chain_id) is not int or chain_id < 1 or chain_id in seen_ids:
                _fail("INVALID_CHAIN_ID")
            seen_ids.add(chain_id)
            name = _required_text(item.get("name"), "INVALID_CHAIN_NAME")
            chain_type = item.get("chainType")
            if chain_type not in {"evm", "solana"}:
                _fail("INVALID_CHAIN_TYPE")
            is_testnet = item.get("isTestnet")
            is_enabled = item.get("isEnabled")
            if type(is_testnet) is not bool or type(is_enabled) is not bool:
                _fail("INVALID_CHAIN_FLAGS")
            explorer_url = item.get("explorerUrl")
            if explorer_url is not None:
                explorer_url = _required_text(
                    explorer_url,
                    "INVALID_EXPLORER_URL",
                    maximum=2_048,
                )
                if not explorer_url.startswith("https://"):
                    _fail("INVALID_EXPLORER_URL")
            chains.append(
                KeeperHubChain(
                    chain_id=chain_id,
                    name=name,
                    chain_type=chain_type,
                    is_testnet=is_testnet,
                    is_enabled=is_enabled,
                    explorer_url=explorer_url,
                )
            )
        return tuple(chains)

    def _json_request(
        self,
        *,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        if method not in {"GET", "POST"}:
            _fail("UNSUPPORTED_HTTP_METHOD")
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            _fail("INVALID_API_PATH")
        if method == "GET" and body is not None:
            _fail("GET_BODY_NOT_ALLOWED")
        if method == "POST" and body is None:
            _fail("POST_BODY_REQUIRED")

        encoded_body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        if body is not None:
            try:
                encoded_body = json.dumps(
                    body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError, OverflowError):
                _fail("INVALID_JSON_REQUEST")
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        request = Request(
            self._base_url + path,
            data=encoded_body,
            headers=headers,
            method=method,
        )
        try:
            response = self._opener.open(
                request,
                timeout=self._timeout_seconds,
            )
            try:
                return self._decode_response(response)
            finally:
                response.close()
        except HTTPError as error:
            try:
                return self._decode_response(error)
            finally:
                error.close()
        except KeeperHubHttpTransportError:
            raise
        except (TimeoutError, URLError, OSError):
            _fail("NETWORK_OUTCOME_UNKNOWN")

    @staticmethod
    def _decode_response(response) -> tuple[int, Any, dict[str, str]]:
        try:
            status = int(getattr(response, "status", response.getcode()))
        except (AttributeError, TypeError, ValueError):
            _fail("INVALID_HTTP_RESPONSE")
        headers = {
            str(key): str(value)
            for key, value in response.headers.items()
        }
        content_type = next(
            (
                value
                for key, value in headers.items()
                if key.casefold() == "content-type"
            ),
            None,
        )
        if content_type is None or not content_type.casefold().startswith(
            "application/json"
        ):
            _fail("INVALID_RESPONSE_CONTENT_TYPE")
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            _fail("RESPONSE_TOO_LARGE")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail("INVALID_JSON_RESPONSE")
        return status, payload, headers
