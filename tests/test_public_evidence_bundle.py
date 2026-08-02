from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from tools.verify_public_evidence import (
    EvidenceVerificationError,
    MANIFEST_PATH,
    verify,
)


class PublicEvidenceBundleTests(unittest.TestCase):
    def test_current_manifest_verifies(self) -> None:
        manifest = verify()
        self.assertEqual(manifest["classification"], "SANITIZED_PUBLIC")
        self.assertEqual(manifest["runtime_evidence"]["status"], "NOT_YET_COLLECTED")

    def test_manifest_has_no_runtime_transaction_claim(self) -> None:
        manifest = verify()
        runtime = manifest["runtime_evidence"]
        self.assertIsNone(runtime["transaction_hash"])
        self.assertIsNone(runtime["explorer_url"])
        self.assertEqual(manifest["external_actions_represented"]["funds_moved"], 0)

    def test_each_curated_artifact_is_not_transaction_evidence(self) -> None:
        manifest = verify()
        self.assertTrue(manifest["artifacts"])
        self.assertTrue(
            all(item["is_transaction_evidence"] is False for item in manifest["artifacts"])
        )

    def test_tampered_artifact_digest_fails_closed(self) -> None:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(value)
        tampered["artifacts"][0]["sha256"] = "0" * 64
        with patch("tools.verify_public_evidence._load_manifest", return_value=tampered):
            with self.assertRaises(EvidenceVerificationError) as caught:
                verify()
        self.assertEqual(caught.exception.code, "ARTIFACT_DIGEST_MISMATCH")

    def test_false_runtime_claim_fails_closed(self) -> None:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(value)
        tampered["runtime_evidence"]["transaction_hash"] = "0x" + "11" * 32
        with patch("tools.verify_public_evidence._load_manifest", return_value=tampered):
            with self.assertRaises(EvidenceVerificationError) as caught:
                verify()
        self.assertEqual(caught.exception.code, "RUNTIME_EVIDENCE_FALSE_CLAIM")

    def test_pending_runtime_claim_cannot_have_merge_commit(self) -> None:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(value)
        pending = next(
            item for item in tampered["claims"] if item["status"] == "PENDING_RUNTIME"
        )
        pending["merge_commit"] = "1" * 40
        with patch("tools.verify_public_evidence._load_manifest", return_value=tampered):
            with self.assertRaises(EvidenceVerificationError) as caught:
                verify()
        self.assertEqual(caught.exception.code, "PENDING_RUNTIME_HAS_COMMIT")


if __name__ == "__main__":
    unittest.main()
