from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from materialize_external_media_batch import load_jsonl, validate_descriptor, validate_evidence


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_PATH = ROOT / "config/external-batches/external-source-001-batch-006.json"
REVIEW_PATH = ROOT / "docs/evidence/external-source-001-kratos-6-identity-review-2026-10-15.json"


class ExternalBatch006ContractTests(unittest.TestCase):
    def test_kratos_identity_is_deterministic_and_visually_authorized_as_new(self) -> None:
        descriptor = json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        product = descriptor["products"][0]
        identity = "Games / God of War / Kratos 6 Diorama / kratos-6-diorama"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.assertEqual(product["expectedIdentityKey"], identity)
        self.assertEqual(product["expectedModelId"], "mdl_" + digest[:20])
        self.assertEqual(product["expectedCode"], "TS-" + digest[:12].upper())
        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.assertTrue(review["creatorCorroborated"])
        self.assertTrue(review["visualAuditComplete"])
        self.assertEqual(review["watermarkStatus"], "creator_attribution")
        self.assertEqual(review["identityReview"]["decision"], "new_model")
        self.assertEqual(review["identityReview"]["productionCandidatesReviewed"], 24)
        self.assertTrue(review["identityReview"]["allCandidatesVisuallyDistinct"])

    def test_batch_has_five_unique_staged_creator_attribution_images(self) -> None:
        descriptor = json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        evidence = load_jsonl(ROOT / descriptor["evidence"])
        self.assertEqual(len(evidence), 5)
        self.assertEqual({row["position"] for row in evidence}, {1, 2, 3, 4, 5})
        self.assertEqual(len({row["sha256"] for row in evidence}), 5)
        self.assertTrue(all(row["stagingVerified"] is True for row in evidence))
        self.assertEqual({row["review"] for row in evidence}, {"creator_attribution"})
        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(review["sourceSha256"]), {row["sha256"] for row in evidence})

    def test_current_release_validator_remains_fail_closed_until_creator_proof_is_integrated(self) -> None:
        descriptor = json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        products = validate_descriptor(descriptor, DESCRIPTOR_PATH)
        evidence = load_jsonl(ROOT / descriptor["evidence"])
        with self.assertRaisesRegex(RuntimeError, "clear/stagingVerified"):
            validate_evidence(evidence, descriptor, products)


if __name__ == "__main__":
    unittest.main()
