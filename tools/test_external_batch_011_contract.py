from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from creator_attribution_release import load_creator_attribution_authorizations
from materialize_external_media_batch import load_jsonl, validate_descriptor, validate_evidence


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_PATH = ROOT / "config/external-batches/external-source-001-batch-011.json"
REVIEW_PATH = ROOT / "docs/evidence/external-source-001-morrigan-dragon-age-gallery-review-2026-10-16.json"
CREATOR_PROOF_PATH = ROOT / "docs/evidence/external-source-001-morrigan-dragon-age-creator-attribution-release-2026-10-16.json"


class ExternalBatch011ContractTests(unittest.TestCase):
    def descriptor_evidence_and_products(self):
        descriptor = json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        evidence = load_jsonl(ROOT / descriptor["evidence"])
        products = validate_descriptor(descriptor, DESCRIPTOR_PATH)
        creator_authorizations = load_creator_attribution_authorizations(ROOT, descriptor, evidence)
        return descriptor, evidence, products, creator_authorizations

    def test_morrigan_identity_is_deterministic_and_cross_franchise(self) -> None:
        descriptor, _, _, _ = self.descriptor_evidence_and_products()
        product = descriptor["products"][0]
        identity = "Games / Dragon Age / Morrigan / morrigan"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.assertEqual(product["expectedIdentityKey"], identity)
        self.assertEqual(product["expectedModelId"], "mdl_" + digest[:20])
        self.assertEqual(product["expectedCode"], "TS-" + digest[:12].upper())
        self.assertEqual(product["expectedSlug"], "dragon-age-morrigan")

        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(review["identityReview"]["decision"], "new_model")
        self.assertEqual(review["identityReview"]["publishedCandidate"]["franchise"], "DarkStalkers")
        self.assertTrue(review["watermarkReview"]["visualAuditComplete"])
        self.assertFalse(review["watermarkReview"]["blockingThirdPartyBrandingObserved"])
        self.assertTrue(review["decision"]["identityResolved"])
        self.assertTrue(review["decision"]["galleryMediaReviewApproved"])
        self.assertTrue(review["decision"]["newModelApproved"])
        self.assertEqual(review["decision"]["productionWrites"], 0)

    def test_batch_has_nine_unique_verified_images(self) -> None:
        descriptor, evidence, products, creator_authorizations = self.descriptor_evidence_and_products()
        self.assertEqual(descriptor["expectedProducts"], 1)
        self.assertEqual(descriptor["expectedImages"], 9)
        self.assertEqual(len(evidence), 9)
        self.assertEqual({row["position"] for row in evidence}, set(range(1, 10)))
        self.assertEqual(len({row["sha256"] for row in evidence}), 9)
        self.assertTrue(all(row["stagingVerified"] is True for row in evidence))
        self.assertEqual(sum(row["review"] == "creator_attribution" for row in evidence), 8)
        self.assertEqual(sum(row["review"] == "clear" for row in evidence), 1)
        validate_evidence(evidence, descriptor, products, creator_authorizations)

    def test_creator_attribution_is_bounded_to_bulkamancer_branding(self) -> None:
        descriptor, evidence, _, creator_authorizations = self.descriptor_evidence_and_products()
        proof = json.loads(CREATOR_PROOF_PATH.read_text(encoding="utf-8"))
        self.assertEqual(proof["creator"], "Bulkamancer Sculpts")
        self.assertTrue(proof["creatorCorroborated"])
        self.assertTrue(proof["visualAuditComplete"])
        self.assertEqual(proof["watermarkStatus"], "creator_attribution")
        identity = proof["identityReview"]
        self.assertEqual(identity["decision"], "new_model")
        self.assertTrue(identity["allCandidatesSemanticallyDistinct"])
        self.assertEqual(identity["semanticDistinctnessReason"], "different_franchise")
        creator_rows = {row["sha256"] for row in evidence if row["review"] == "creator_attribution"}
        self.assertEqual(creator_authorizations, {36978: creator_rows})
        self.assertEqual(set(proof["sourceSha256"]), creator_rows)
        self.assertNotIn("VIK3DFIGURES", proof["creator"])
        self.assertEqual(descriptor["creatorAttributionEvidence"], str(CREATOR_PROOF_PATH.relative_to(ROOT)))


if __name__ == "__main__":
    unittest.main()
