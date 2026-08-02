"""Provider-neutral independent transfer evidence contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

_ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
_HASH_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")
_EVIDENCE_FINGERPRINT_DOMAIN = b"nexus-vector:verified-transfer-evidence:v1\x00"


class VerificationEvidenceError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise VerificationEvidenceError(code)


def _address(value: Any, code: str) -> str:
    if not isinstance(value, str) or _ADDRESS_PATTERN.fullmatch(value) is None:
        _fail(code)
    return value.lower()


def normalize_expected_sender(value: Any) -> str:
    return _address(value, "INVALID_EXPECTED_SENDER")


class VerificationObservationStatus(str, Enum):
    VERIFIED_TRANSFER = "VERIFIED_TRANSFER"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class ObservedTransfer:
    chain_id: int
    token_address: str
    sender: str
    recipient: str
    amount_base_units: int
    transaction_hash: str
    block_hash: str
    log_index: int
    confirmations: int

    def __post_init__(self) -> None:
        if type(self.chain_id) is not int or self.chain_id < 1:
            _fail("INVALID_CHAIN_ID")
        object.__setattr__(
            self,
            "token_address",
            _address(self.token_address, "INVALID_TOKEN_ADDRESS"),
        )
        object.__setattr__(self, "sender", _address(self.sender, "INVALID_SENDER"))
        object.__setattr__(
            self,
            "recipient",
            _address(self.recipient, "INVALID_RECIPIENT"),
        )
        if type(self.amount_base_units) is not int or self.amount_base_units < 1:
            _fail("INVALID_AMOUNT_BASE_UNITS")
        for field_name, value in (
            ("transaction_hash", self.transaction_hash),
            ("block_hash", self.block_hash),
        ):
            if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
                _fail(f"INVALID_{field_name.upper()}")
            object.__setattr__(self, field_name, value.lower())
        if type(self.log_index) is not int or self.log_index < 0:
            _fail("INVALID_LOG_INDEX")
        if type(self.confirmations) is not int or self.confirmations < 0:
            _fail("INVALID_CONFIRMATIONS")


@dataclass(frozen=True)
class VerificationObservation:
    status: VerificationObservationStatus
    transfer: ObservedTransfer | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, VerificationObservationStatus):
            _fail("INVALID_OBSERVATION_STATUS")
        if self.status is VerificationObservationStatus.VERIFIED_TRANSFER:
            if not isinstance(self.transfer, ObservedTransfer):
                _fail("MISSING_VERIFIED_TRANSFER")
        elif self.transfer is not None:
            _fail("UNEXPECTED_TRANSFER")


def derive_evidence_fingerprint(transfer: ObservedTransfer) -> str:
    if not isinstance(transfer, ObservedTransfer):
        _fail("INVALID_TRANSFER")
    document = {
        "chain_id": transfer.chain_id,
        "token_address": transfer.token_address,
        "sender": transfer.sender,
        "recipient": transfer.recipient,
        "amount_base_units": transfer.amount_base_units,
        "transaction_hash": transfer.transaction_hash,
        "block_hash": transfer.block_hash,
        "log_index": transfer.log_index,
    }
    material = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "evf_" + hashlib.sha256(
        _EVIDENCE_FINGERPRINT_DOMAIN + material
    ).hexdigest()
