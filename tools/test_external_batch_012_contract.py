from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from creator_attribution_release import load_creator_attribution_authorizations
from materialize_external_media_batch import load_jsonl, validate_descriptor, validate_evidence


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_PATH = ROOT / "config/external-batches/external-source-001-batch-012.json"
REVIEW_PATH = ROOT / "docs/evidence/external-source-001-malevola-dispatch-gallery-review-2026-10-16.json"
CREATOR_PROOF_PATH = ROOT / "docs/evidence/external-source-001-malevola-dispatch-creator-attribution-release-2026-10-16.json"


class ExternalBatch012ContractTests(unittest.TestCase):
    def descriptor_evidence_and_products(self):
        descriptor = json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        evidence = load_jsonl(ROOT / descriptor["evidence"])
        products = validate_descriptor(descriptor, DESCRIPTOR_PATH)
        creator_authorizations = load_creator_attribution_authorizations(ROOT, descriptor, evidence)
        return descriptor, evidence, products, creator_authorizations

    def test_malevola_identity_is_deterministic_and_cross_franchise(self) -> None:
        descriptor, _, _, _ = self.descriptor_evidence_and_products()
        product = descriptor["products"][0]
        identity = "Games / Dispatch / Malevola / malevola"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.assertEqual(product["expectedIdentityKey"], identity)
        self.assertEqual(product["expectedModelId"], "mdl_" + digest[:20])
        self.assertEqual(product["expectedCode"], "TS-" + digest[:12].upper())
        self.assertEqual(product["expectedSlug"], "dispatch-malevola")

        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(review["identityReview"]["decision"], "new_model")
        self.assertTrue(review["identityReview"]["allCandidatesSemanticallyDistinct"])
        self.assertEqual(review["identityReview"]["semanticDistinctnessReason"], "different_franchise")
        self.assertTrue(review["visualReview"]["artifactId"] > 0)
        self.assertEqual(review["visualReview"]["result"], "creator_attribution")
        self.assertTrue(review["decision"]["sourceReviewComplete"])
        self.assertTrue(review["decision"]["galleryMediaReviewApproved"])
        self.assertTrue(review["decision"]["newModelApproved"])
        self.assertEqual(review["decision"]["productionWrites"], 0)

    def test_batch_is_bounded_to_five_static_verified_images(self) -> None:
        descriptor, evidence, products, creator_authorizations = self.descriptor_evidence_and_products()
        self.assertEqual(descriptor["expectedProducts"], 1)
        self.assertEqual(descriptor["expectedImages"], 5)
        self.assertEqual(len(evidence), 5)
        self.assertEqual({row["position"] for row in evidence}, set(range(1, 6)))
        self.assertEqual(len({row["sha256"] for row in evidence}), 5)
        self.assertTrue(all(row["stagingVerified"] is True for row in evidence))
        self.assertTrue(all(row["review"] == "creator_attribution" for row in evidence))
        validate_evidence(evidence, descriptor, products, creator_authorizations)

        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        excluded = review["excludedSourceMedia"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["sourceImageId"], "22188")
        self.assertEqual(excluded[0]["state"], "review_only_oversize")
        self.assertFalse(excluded[0]["promotableMedia"])
        self.assertNotIn(excluded[0]["sha256"], {row["sha256"] for row in evidence})

    def test_creator_attribution_is_bounded_to_neko_static_renders(self) -> None:
        descriptor, evidence, _, creator_authorizations = self.descriptor_evidence_and_products()
        proof = json.loads(CREATOR_PROOF_PATH.read_text(encoding="utf-8"))
        self.assertEqual(proof["creator"], "Neko Figurines")
        self.assertTrue(proof["creatorCorroborated"])
        self.assertTrue(proof["visualAuditComplete"])
        self.assertEqual(proof["watermarkStatus"], "creator_attribution")
        identity = proof["identityReview"]
        self.assertEqual(identity["decision"], "new_model")
        self.assertTrue(identity["allCandidatesSemanticallyDistinct"])
        self.assertEqual(identity["semanticDistinctnessReason"], "different_franchise")
        creator_rows = {row["sha256"] for row in evidence if row["review"] == "creator_attribution"}
        self.assertEqual(creator_authorizations, {22180: creator_rows})
        self.assertEqual(set(proof["sourceSha256"]), creator_rows)
        self.assertFalse(proof["excludedSourceMedia"]["promotableMedia"])
        self.assertEqual(descriptor["creatorAttributionEvidence"], str(CREATOR_PROOF_PATH.relative_to(ROOT)))


if __name__ == "__main__":
    unittest.main()
