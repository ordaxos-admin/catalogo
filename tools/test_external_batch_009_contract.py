from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from materialize_external_media_batch import load_jsonl, validate_descriptor, validate_evidence


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_PATH = ROOT / "config/external-batches/external-source-001-batch-009.json"
REVIEW_PATH = ROOT / "docs/evidence/external-source-001-johnny-silverhand-identity-review-2026-10-15.json"


class ExternalBatch009ContractTests(unittest.TestCase):
    def descriptor_and_evidence(self):
        descriptor = json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        evidence = load_jsonl(ROOT / descriptor["evidence"])
        products = validate_descriptor(descriptor, DESCRIPTOR_PATH)
        return descriptor, evidence, products

    def test_johnny_identity_is_deterministic_and_authorized_as_new(self) -> None:
        descriptor, _, _ = self.descriptor_and_evidence()
        product = descriptor["products"][0]
        identity = "Games / Cyberpunk / Johnny Silverhand / johnny-silverhand"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.assertEqual(product["expectedIdentityKey"], identity)
        self.assertEqual(product["expectedModelId"], "mdl_" + digest[:20])
        self.assertEqual(product["expectedCode"], "TS-" + digest[:12].upper())
        self.assertEqual(product["expectedSlug"], "cyberpunk-johnny-silverhand")
        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.assertTrue(review["creatorCorroborated"])
        self.assertTrue(review["visualAuditComplete"])
        self.assertEqual(review["watermarkStatus"], "clear")
        self.assertEqual(review["identityReview"]["decision"], "new_model")
        self.assertEqual(review["identityReview"]["productionCandidatesReviewed"], 1)
        self.assertTrue(review["identityReview"]["allCandidatesVisuallyDistinct"])
        self.assertEqual(review["identityReview"]["candidateModelIds"], ["mdl_7f346517153d7c99dcd8"])

    def test_batch_has_six_unique_staged_clear_images(self) -> None:
        descriptor, evidence, products = self.descriptor_and_evidence()
        self.assertEqual(len(evidence), 6)
        self.assertEqual({row["position"] for row in evidence}, set(range(1, 7)))
        self.assertEqual(len({row["sha256"] for row in evidence}), 6)
        self.assertTrue(all(row["stagingVerified"] is True for row in evidence))
        self.assertEqual({row["review"] for row in evidence}, {"clear"})
        validate_evidence(evidence, descriptor, products)
        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(review["sourceSha256"]), {row["sha256"] for row in evidence})
        self.assertEqual(review["mediaReview"]["perceptualPairsAtOrBelowThreshold"], 0)
        self.assertGreater(review["mediaReview"]["minimumPairDhashDistance"], review["mediaReview"]["visualThreshold"])
        self.assertFalse(review["mediaReview"]["blockingThirdPartyBrandingObserved"])

    def test_existing_johnny_is_visually_distinct(self) -> None:
        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        identity = review["identityReview"]
        self.assertEqual(identity["exactProductionShaOwners"], 0)
        self.assertEqual(identity["minimumCandidateDhashDistance"], 24)
        self.assertGreater(identity["minimumCandidateDhashDistance"], identity["visualThreshold"])
        self.assertTrue(review["decision"]["newModelAuthorized"])
        self.assertTrue(review["decision"]["clearMediaPromotable"])
        self.assertEqual(review["decision"]["productionWrites"], 0)


if __name__ == "__main__":
    unittest.main()
