from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from creator_attribution_release import (
    load_creator_attribution_authorizations,
    validate_creator_attribution_proof,
)
from materialize_external_media_batch import load_jsonl, validate_descriptor, validate_evidence


ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_PATH = ROOT / "config/external-batches/external-source-001-batch-007.json"
REVIEW_PATH = ROOT / "docs/evidence/external-source-001-zoro-3-identity-review-2026-10-15.json"


class ExternalBatch007ContractTests(unittest.TestCase):
    def descriptor_and_evidence(self):
        descriptor = json.loads(DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        evidence = load_jsonl(ROOT / descriptor["evidence"])
        products = validate_descriptor(descriptor, DESCRIPTOR_PATH)
        return descriptor, evidence, products

    def test_zoro_identity_is_deterministic_and_visually_authorized_as_new(self) -> None:
        descriptor, _, _ = self.descriptor_and_evidence()
        product = descriptor["products"][0]
        identity = "Animes & Desenhos / One Piece / Roronoa Zoro 3 Diorama / roronoa-zoro-3-diorama"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.assertEqual(product["expectedIdentityKey"], identity)
        self.assertEqual(product["expectedModelId"], "mdl_" + digest[:20])
        self.assertEqual(product["expectedCode"], "TS-" + digest[:12].upper())
        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.assertTrue(review["creatorCorroborated"])
        self.assertTrue(review["visualAuditComplete"])
        self.assertEqual(review["watermarkStatus"], "creator_attribution")
        self.assertEqual(review["identityReview"]["decision"], "new_model")
        self.assertEqual(review["identityReview"]["productionCandidatesReviewed"], 1)
        self.assertTrue(review["identityReview"]["allCandidatesVisuallyDistinct"])
        self.assertGreater(review["identityReview"]["minimumCandidateDhashDistance"], review["identityReview"]["visualThreshold"])

    def test_batch_has_seven_unique_staged_creator_attribution_images(self) -> None:
        descriptor, evidence, _ = self.descriptor_and_evidence()
        self.assertEqual(len(evidence), 7)
        self.assertEqual({row["position"] for row in evidence}, set(range(1, 8)))
        self.assertEqual(len({row["sha256"] for row in evidence}), 7)
        self.assertTrue(all(row["stagingVerified"] is True for row in evidence))
        self.assertEqual({row["review"] for row in evidence}, {"creator_attribution"})
        review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(review["sourceSha256"]), {row["sha256"] for row in evidence})
        self.assertEqual(review["mediaReview"]["perceptualPairsAtOrBelowThreshold"], 0)
        self.assertFalse(review["mediaReview"]["blockingThirdPartyBrandingObserved"])

    def test_creator_attribution_requires_exact_versioned_proof(self) -> None:
        descriptor, evidence, products = self.descriptor_and_evidence()
        with self.assertRaisesRegex(RuntimeError, "creator_attribution sem prova autorizada"):
            validate_evidence(evidence, descriptor, products)
        authorizations = load_creator_attribution_authorizations(ROOT, descriptor, evidence)
        validate_evidence(evidence, descriptor, products, authorizations)
        self.assertEqual(authorizations, {13687: {row["sha256"] for row in evidence}})

    def test_mismatched_creator_attribution_sha_fails_closed(self) -> None:
        descriptor, evidence, _ = self.descriptor_and_evidence()
        proof = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        proof["sourceSha256"] = list(proof["sourceSha256"])
        proof["sourceSha256"][0] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "SHAs da prova"):
            validate_creator_attribution_proof(proof, descriptor, evidence)

    def test_missing_creator_proof_path_fails_closed(self) -> None:
        descriptor, evidence, _ = self.descriptor_and_evidence()
        descriptor = dict(descriptor)
        descriptor.pop("creatorAttributionEvidence")
        with self.assertRaisesRegex(RuntimeError, "exige creatorAttributionEvidence"):
            load_creator_attribution_authorizations(ROOT, descriptor, evidence)


if __name__ == "__main__":
    unittest.main()
