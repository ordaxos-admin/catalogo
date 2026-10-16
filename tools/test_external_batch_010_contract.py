from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from materialize_external_media_batch import load_jsonl, validate_descriptor, validate_evidence


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_PATH = ROOT / "config/external-batches/external-source-001-batch-010.json"
REVIEW_PATH = ROOT / "docs/evidence/external-source-001-lilith-darkstalkers-gallery-review-2026-10-16.json"


class ExternalBatch010ContractTests(unittest.TestCase):
    def descriptor_and_evidence(self):
        descriptor = json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        evidence = load_jsonl(ROOT / descriptor["evidence"])
        products = validate_descriptor(descriptor, DESCRIPTOR_PATH)
        return descriptor, evidence, products

    def test_lilith_identity_is_deterministic_and_authorized_as_new(self) -> None:
        descriptor, _, _ = self.descriptor_and_evidence()
        product = descriptor["products"][0]
        identity = "Games / DarkStalkers / Lilith / lilith"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.assertEqual(product["expectedIdentityKey"], identity)
        self.assertEqual(product["expectedModelId"], "mdl_" + digest[:20])
        self.assertEqual(product["expectedCode"], "TS-" + digest[:12].upper())
        self.assertEqual(product["expectedSlug"], "darkstalkers-lilith")

        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.assertTrue(review["watermarkReview"]["visualAuditComplete"])
        self.assertFalse(review["watermarkReview"]["blockingThirdPartyBrandingObserved"])
        self.assertEqual(review["watermarkReview"]["decision"], "clear")
        self.assertEqual(review["identityReview"]["decision"], "new_model")
        self.assertTrue(review["decision"]["sourceReviewComplete"])
        self.assertTrue(review["decision"]["identityResolved"])
        self.assertTrue(review["decision"]["galleryMediaReviewApproved"])
        self.assertTrue(review["decision"]["newModelApproved"])
        self.assertTrue(review["decision"]["batchCreationAuthorized"])
        self.assertEqual(review["decision"]["productionWrites"], 0)

    def test_batch_has_five_unique_staged_clear_images(self) -> None:
        descriptor, evidence, products = self.descriptor_and_evidence()
        self.assertEqual(descriptor["expectedProducts"], 1)
        self.assertEqual(descriptor["expectedImages"], 5)
        self.assertEqual(len(evidence), 5)
        self.assertEqual({row["position"] for row in evidence}, set(range(1, 6)))
        self.assertEqual(len({row["sha256"] for row in evidence}), 5)
        self.assertTrue(all(row["stagingVerified"] is True for row in evidence))
        self.assertEqual({row["review"] for row in evidence}, {"clear"})
        validate_evidence(evidence, descriptor, products)

        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        review_shas = {row["sha256"] for row in review["images"]}
        self.assertEqual(review_shas, {row["sha256"] for row in evidence})
        self.assertTrue(all(row["promotableMedia"] is True for row in review["images"]))
        self.assertEqual({row["watermarkStatus"] for row in review["images"]}, {"clear"})


if __name__ == "__main__":
    unittest.main()
