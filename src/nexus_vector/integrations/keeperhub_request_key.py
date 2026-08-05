"""Deterministic KeeperHub request keys bound to canonical effect identity."""

from __future__ import annotations

import hashlib
import re

_EFFECT_ID_PATTERN = re.compile(r"eff_[0-9a-f]{64}")
_REQUEST_KEY_DOMAIN = b"nexus-vector:keeperhub-request-key:v1\x00"
_REQUEST_KEY_PREFIX = "khreq_v1_"


class KeeperHubRequestKeyError(ValueError):
    """Machine-classifiable request-key error without value echo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def derive_keeperhub_request_key(effect_id: str) -> str:
    """Return one stable provider key for one canonical economic effect."""

    if (
        not isinstance(effect_id, str)
        or _EFFECT_ID_PATTERN.fullmatch(effect_id) is None
    ):
        raise KeeperHubRequestKeyError("INVALID_EFFECT_ID")
    digest = hashlib.sha256(
        _REQUEST_KEY_DOMAIN + effect_id.encode("ascii")
    ).hexdigest()
    return f"{_REQUEST_KEY_PREFIX}{digest}"
