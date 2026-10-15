import unittest

from preflight_exact_source_gallery_evidence import validate_evidence


class ExactSourceGalleryPreflightTests(unittest.TestCase):
    def evidence(self):
        return {
            "incomingModel": "external-source-001 / product:42",
            "identityReview": {
                "decision": "map_existing",
                "mapExistingApproved": True,
                "exactSourceShaOverlap": True,
                "targetModelId": "mdl_existing",
            },
            "decision": {
                "galleryMediaReviewApproved": True,
                "newModelApproved": False,
                "batch005Approved": False,
            },
            "metricReview": {"existingCover": {"sourceSha256": "a" * 64}},
            "images": [
                {
                    "position": 1,
                    "sha256": "a" * 64,
                    "bytes": 100,
                    "stagingVerified": True,
                    "watermarkStatus": "creator_attribution",
                    "proposedAction": "skip_exact",
                },
                {
                    "position": 2,
                    "sha256": "b" * 64,
                    "bytes": 200,
                    "stagingVerified": True,
                    "watermarkStatus": "clear",
                    "proposedAction": "add_view",
                },
            ],
        }

    def test_accepts_one_exact_anchor(self):
        target, incoming, images, anchor = validate_evidence(self.evidence())
        self.assertEqual(target, "mdl_existing")
        self.assertEqual(incoming, "external-source-001 / product:42")
        self.assertEqual(len(images), 2)
        self.assertEqual(anchor["sourceSha256"], "a" * 64)

    def test_rejects_missing_exact_overlap(self):
        data = self.evidence()
        data["identityReview"]["exactSourceShaOverlap"] = False
        with self.assertRaisesRegex(RuntimeError, "overlap SHA"):
            validate_evidence(data)

    def test_rejects_multiple_exact_anchors(self):
        data = self.evidence()
        data["images"][1]["proposedAction"] = "skip_exact"
        with self.assertRaisesRegex(RuntimeError, "exatamente um skip_exact"):
            validate_evidence(data)

    def test_rejects_anchor_sha_mismatch(self):
        data = self.evidence()
        data["metricReview"]["existingCover"]["sourceSha256"] = "c" * 64
        with self.assertRaisesRegex(RuntimeError, "não corresponde"):
            validate_evidence(data)


if __name__ == "__main__":
    unittest.main()
