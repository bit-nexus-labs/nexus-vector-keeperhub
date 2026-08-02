"""Fail-closed verifier for the sanitized public evidence manifest."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "evidence" / "public_manifest.json"

_ALLOWED_TOP_LEVEL = frozenset(
    {
        "schema",
        "classification",
        "generated_at_utc",
        "project",
        "claims",
        "artifacts",
        "runtime_evidence",
        "redaction",
        "external_actions_represented",
    }
)
_ALLOWED_CLAIM_STATUS = frozenset({"OFFLINE_VERIFIED", "PENDING_RUNTIME"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_FORBIDDEN_KEY_FRAGMENTS = (
    "private_key",
    "seed",
    "mnemonic",
    "api_key",
    "authorization",
    "secret",
    "raw_payload",
)


class EvidenceVerificationError(RuntimeError):
    """Machine-classifiable public evidence verification failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise EvidenceVerificationError(code)


def _load_manifest() -> dict[str, Any]:
    try:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("MANIFEST_UNREADABLE")
    if not isinstance(value, dict) or frozenset(value) != _ALLOWED_TOP_LEVEL:
        _fail("INVALID_TOP_LEVEL_SHAPE")
    return value


def _walk_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail("NON_STRING_KEY")
            lowered = key.lower()
            if any(fragment in lowered for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                _fail("FORBIDDEN_FIELD")
            _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            _walk_keys(child)


def verify() -> dict[str, Any]:
    manifest = _load_manifest()
    _walk_keys(manifest)

    if manifest["schema"] != "nexus-vector.public-evidence.v1":
        _fail("UNSUPPORTED_SCHEMA")
    if manifest["classification"] != "SANITIZED_PUBLIC":
        _fail("INVALID_CLASSIFICATION")

    claims = manifest["claims"]
    if not isinstance(claims, list) or not claims:
        _fail("INVALID_CLAIMS")
    claim_ids: set[str] = set()
    runtime_claims = 0
    for claim in claims:
        if not isinstance(claim, dict) or frozenset(claim) != {
            "claim_id", "status", "merge_commit", "evidence"
        }:
            _fail("INVALID_CLAIM_SHAPE")
        claim_id = claim["claim_id"]
        if not isinstance(claim_id, str) or not claim_id or claim_id in claim_ids:
            _fail("INVALID_OR_DUPLICATE_CLAIM_ID")
        claim_ids.add(claim_id)
        status = claim["status"]
        if status not in _ALLOWED_CLAIM_STATUS:
            _fail("INVALID_CLAIM_STATUS")
        merge_commit = claim["merge_commit"]
        if status == "OFFLINE_VERIFIED":
            if not isinstance(merge_commit, str) or _COMMIT.fullmatch(merge_commit) is None:
                _fail("INVALID_OFFLINE_CLAIM_COMMIT")
        else:
            runtime_claims += 1
            if merge_commit is not None:
                _fail("PENDING_RUNTIME_HAS_COMMIT")
        if not isinstance(claim["evidence"], str) or not claim["evidence"]:
            _fail("INVALID_CLAIM_EVIDENCE")

    if runtime_claims != 1 or "keeperhub_testnet_transaction" not in claim_ids:
        _fail("RUNTIME_CLAIM_BOUNDARY_MISSING")

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        _fail("INVALID_ARTIFACTS")
    paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or frozenset(artifact) != {
            "path", "sha256", "classification", "is_transaction_evidence"
        }:
            _fail("INVALID_ARTIFACT_SHAPE")
        relative = artifact["path"]
        if not isinstance(relative, str) or relative in paths:
            _fail("INVALID_OR_DUPLICATE_ARTIFACT_PATH")
        paths.add(relative)
        if relative.startswith(("/", "\\")) or ".." in Path(relative).parts:
            _fail("UNSAFE_ARTIFACT_PATH")
        expected = artifact["sha256"]
        if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
            _fail("INVALID_ARTIFACT_DIGEST")
        target = ROOT / relative
        try:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            _fail("ARTIFACT_UNREADABLE")
        if actual != expected:
            _fail("ARTIFACT_DIGEST_MISMATCH")
        if artifact["is_transaction_evidence"] is not False:
            _fail("CURATED_ARTIFACT_MISLABELED")

    runtime = manifest["runtime_evidence"]
    if runtime != {
        "keeperhub_request_id": None,
        "transaction_hash": None,
        "explorer_url": None,
        "status": "NOT_YET_COLLECTED",
    }:
        _fail("RUNTIME_EVIDENCE_FALSE_CLAIM")

    redaction = manifest["redaction"]
    if not isinstance(redaction, dict) or not redaction or any(
        value is not False for value in redaction.values()
    ):
        _fail("REDACTION_BOUNDARY_FAILED")

    actions = manifest["external_actions_represented"]
    if not isinstance(actions, dict) or set(actions) != {
        "provider_calls",
        "wallet_operations",
        "signed_transactions",
        "broadcast_transactions",
        "funds_moved",
    } or any(type(value) is not int or value != 0 for value in actions.values()):
        _fail("EXTERNAL_ACTION_BOUNDARY_FAILED")

    return manifest


def main() -> int:
    try:
        manifest = verify()
    except EvidenceVerificationError as error:
        print(f"PUBLIC_EVIDENCE_VERIFY: FAIL ({error.code})")
        return 1
    print(
        "PUBLIC_EVIDENCE_VERIFY: PASS "
        f"({len(manifest['claims'])} claims, {len(manifest['artifacts'])} artifacts)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
