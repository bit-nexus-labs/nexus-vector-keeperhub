"""Independent Base Sepolia ERC-20 transfer verification over JSON-RPC.

This module is intentionally provider-neutral with respect to KeeperHub. It
reads public chain state only and produces the existing VerificationObservation
contract consumed by ExecutionReconciliationService.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from nexus_vector.domain.verification_evidence import (
    ObservedTransfer,
    VerificationObservation,
    VerificationObservationStatus,
)

BASE_SEPOLIA_CHAIN_ID = 84532
BASE_SEPOLIA_RPC_URL = "https://sepolia.base.org"
ERC20_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)

_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
_HASH_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")
_HEX_QUANTITY_PATTERN = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)")
_TOPIC_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")
_MAX_RESPONSE_BYTES = 1_048_576
_USER_AGENT = "NexusVector-BaseSepoliaVerifier/1.0"


class BaseSepoliaVerificationError(RuntimeError):
    """Fail-closed, machine-classifiable chain verification failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise BaseSepoliaVerificationError(code)


def _address(value: Any, code: str) -> str:
    if not isinstance(value, str) or _ADDRESS_PATTERN.fullmatch(value) is None:
        _fail(code)
    return value.lower()


def _hash(value: Any, code: str) -> str:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        _fail(code)
    return value.lower()


def _hex_quantity(value: Any, code: str) -> int:
    if not isinstance(value, str) or _HEX_QUANTITY_PATTERN.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = int(value, 16)
    except ValueError:
        _fail(code)
    if parsed < 0:
        _fail(code)
    return parsed


def _topic_address(value: Any, code: str) -> str:
    if not isinstance(value, str) or _TOPIC_PATTERN.fullmatch(value) is None:
        _fail(code)
    return "0x" + value[-40:].lower()


def _uint256_data(value: Any, code: str) -> int:
    if not isinstance(value, str) or _TOPIC_PATTERN.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = int(value, 16)
    except ValueError:
        _fail(code)
    if parsed < 1:
        _fail(code)
    return parsed


class BaseSepoliaJsonRpcTransport:
    """Small standard-library JSON-RPC client with bounded responses."""

    def __init__(
        self,
        endpoint: str = BASE_SEPOLIA_RPC_URL,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        if endpoint != BASE_SEPOLIA_RPC_URL:
            _fail("UNAPPROVED_RPC_ENDPOINT")
        if not isinstance(timeout_seconds, (int, float)) or not (0 < timeout_seconds <= 30):
            _fail("INVALID_RPC_TIMEOUT")
        self._endpoint = endpoint
        self._timeout_seconds = float(timeout_seconds)
        self._next_id = 1
        self.calls = 0

    def call(self, method: str, params: Sequence[Any]) -> Any:
        if not isinstance(method, str) or not method.startswith("eth_"):
            _fail("INVALID_RPC_METHOD")
        if not isinstance(params, Sequence) or isinstance(params, (str, bytes, bytearray)):
            _fail("INVALID_RPC_PARAMS")
        request_id = self._next_id
        self._next_id += 1
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": list(params),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=payload,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        self.calls += 1
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                if response.status != 200:
                    _fail("RPC_HTTP_STATUS")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except BaseSepoliaVerificationError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            _fail("RPC_TRANSPORT_ERROR")
        if len(raw) > _MAX_RESPONSE_BYTES:
            _fail("RPC_RESPONSE_TOO_LARGE")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail("RPC_RESPONSE_INVALID_JSON")
        if not isinstance(document, dict):
            _fail("RPC_RESPONSE_INVALID_SHAPE")
        if document.get("jsonrpc") != "2.0" or document.get("id") != request_id:
            _fail("RPC_RESPONSE_ID_MISMATCH")
        if document.get("error") is not None:
            _fail("RPC_PROVIDER_ERROR")
        if "result" not in document:
            _fail("RPC_RESULT_MISSING")
        return document["result"]


class BaseSepoliaErc20TransferVerifier:
    """Observe one transaction and return exact independent transfer evidence."""

    def __init__(
        self,
        transport: Any,
        *,
        transaction_hash: str,
        expected_token_address: str,
        expected_sender: str,
        expected_recipient: str,
        expected_amount_base_units: int,
    ) -> None:
        if not hasattr(transport, "call"):
            _fail("INVALID_RPC_TRANSPORT")
        self._transport = transport
        self._transaction_hash = _hash(transaction_hash, "INVALID_TRANSACTION_HASH")
        self._expected_token = _address(expected_token_address, "INVALID_EXPECTED_TOKEN")
        self._expected_sender = _address(expected_sender, "INVALID_EXPECTED_SENDER")
        self._expected_recipient = _address(expected_recipient, "INVALID_EXPECTED_RECIPIENT")
        if type(expected_amount_base_units) is not int or expected_amount_base_units < 1:
            _fail("INVALID_EXPECTED_AMOUNT")
        self._expected_amount = expected_amount_base_units
        self.last_observation: VerificationObservation | None = None

    @property
    def transaction_hash(self) -> str:
        return self._transaction_hash

    def observe(self, attempt: object) -> VerificationObservation:
        del attempt  # Economic identity is independently rechecked by reconciliation.
        chain_id = _hex_quantity(
            self._transport.call("eth_chainId", []),
            "INVALID_RPC_CHAIN_ID",
        )
        if chain_id != BASE_SEPOLIA_CHAIN_ID:
            _fail("WRONG_RPC_CHAIN")

        receipt = self._transport.call(
            "eth_getTransactionReceipt",
            [self._transaction_hash],
        )
        if receipt is None:
            return self._remember(
                VerificationObservation(VerificationObservationStatus.NOT_FOUND)
            )
        if not isinstance(receipt, Mapping):
            _fail("INVALID_TRANSACTION_RECEIPT")
        receipt_hash = _hash(receipt.get("transactionHash"), "INVALID_RECEIPT_TRANSACTION_HASH")
        if receipt_hash != self._transaction_hash:
            _fail("RECEIPT_TRANSACTION_HASH_MISMATCH")
        status = _hex_quantity(receipt.get("status"), "INVALID_RECEIPT_STATUS")
        if status != 1:
            _fail("TRANSACTION_REVERTED")
        block_number = _hex_quantity(
            receipt.get("blockNumber"),
            "INVALID_RECEIPT_BLOCK_NUMBER",
        )
        block_hash = _hash(receipt.get("blockHash"), "INVALID_RECEIPT_BLOCK_HASH")
        latest_block = _hex_quantity(
            self._transport.call("eth_blockNumber", []),
            "INVALID_LATEST_BLOCK_NUMBER",
        )
        if latest_block < block_number:
            _fail("LATEST_BLOCK_BEFORE_RECEIPT")
        confirmations = latest_block - block_number + 1

        logs = receipt.get("logs")
        if not isinstance(logs, list):
            _fail("INVALID_RECEIPT_LOGS")

        all_transfers: list[ObservedTransfer] = []
        expected_token_transfers: list[ObservedTransfer] = []
        for log in logs:
            parsed = self._parse_transfer_log(
                log,
                receipt_block_number=block_number,
                receipt_block_hash=block_hash,
                confirmations=confirmations,
            )
            if parsed is None:
                continue
            all_transfers.append(parsed)
            if parsed.token_address == self._expected_token:
                expected_token_transfers.append(parsed)

        selected = self._select_transfer(
            all_transfers,
            expected_token_transfers,
        )
        if selected is None:
            return self._remember(
                VerificationObservation(VerificationObservationStatus.NOT_FOUND)
            )
        if selected is ...:
            return self._remember(
                VerificationObservation(VerificationObservationStatus.AMBIGUOUS)
            )
        return self._remember(
            VerificationObservation(
                VerificationObservationStatus.VERIFIED_TRANSFER,
                selected,
            )
        )

    def _parse_transfer_log(
        self,
        log: Any,
        *,
        receipt_block_number: int,
        receipt_block_hash: str,
        confirmations: int,
    ) -> ObservedTransfer | None:
        if not isinstance(log, Mapping):
            return None
        topics = log.get("topics")
        if not isinstance(topics, list) or not topics:
            return None
        topic0 = topics[0]
        if not isinstance(topic0, str) or topic0.lower() != ERC20_TRANSFER_TOPIC:
            return None

        token_raw = log.get("address")
        token_is_expected = (
            isinstance(token_raw, str)
            and _ADDRESS_PATTERN.fullmatch(token_raw) is not None
            and token_raw.lower() == self._expected_token
        )
        try:
            token = _address(token_raw, "INVALID_LOG_TOKEN_ADDRESS")
            if len(topics) != 3:
                _fail("INVALID_TRANSFER_TOPIC_COUNT")
            sender = _topic_address(topics[1], "INVALID_TRANSFER_SENDER_TOPIC")
            recipient = _topic_address(topics[2], "INVALID_TRANSFER_RECIPIENT_TOPIC")
            amount = _uint256_data(log.get("data"), "INVALID_TRANSFER_AMOUNT_DATA")
            log_index = _hex_quantity(log.get("logIndex"), "INVALID_TRANSFER_LOG_INDEX")
            log_block_number = _hex_quantity(
                log.get("blockNumber"),
                "INVALID_TRANSFER_BLOCK_NUMBER",
            )
            log_block_hash = _hash(log.get("blockHash"), "INVALID_TRANSFER_BLOCK_HASH")
            log_transaction_hash = _hash(
                log.get("transactionHash"),
                "INVALID_TRANSFER_TRANSACTION_HASH",
            )
            if (
                log_block_number != receipt_block_number
                or log_block_hash != receipt_block_hash
                or log_transaction_hash != self._transaction_hash
            ):
                _fail("TRANSFER_RECEIPT_BINDING_MISMATCH")
            return ObservedTransfer(
                chain_id=BASE_SEPOLIA_CHAIN_ID,
                token_address=token,
                sender=sender,
                recipient=recipient,
                amount_base_units=amount,
                transaction_hash=self._transaction_hash,
                block_hash=receipt_block_hash,
                log_index=log_index,
                confirmations=confirmations,
            )
        except BaseSepoliaVerificationError:
            if token_is_expected:
                raise
            return None

    def _select_transfer(
        self,
        all_transfers: list[ObservedTransfer],
        expected_token_transfers: list[ObservedTransfer],
    ) -> ObservedTransfer | None | type(Ellipsis):
        if len(expected_token_transfers) == 1:
            return expected_token_transfers[0]
        if len(expected_token_transfers) > 1:
            exact = [
                transfer
                for transfer in expected_token_transfers
                if self._economic_match(transfer)
            ]
            if len(exact) == 1:
                return exact[0]
            return ...
        if len(all_transfers) == 1:
            # Let ExecutionReconciliationService classify token mismatch as BLOCKED.
            return all_transfers[0]
        if len(all_transfers) > 1:
            return ...
        return None

    def _economic_match(self, transfer: ObservedTransfer) -> bool:
        return (
            transfer.token_address == self._expected_token
            and transfer.sender == self._expected_sender
            and transfer.recipient == self._expected_recipient
            and transfer.amount_base_units == self._expected_amount
        )

    def _remember(self, observation: VerificationObservation) -> VerificationObservation:
        self.last_observation = observation
        return observation
