import unittest

from preflight_existing_gallery_evidence import staging_key, validate_evidence


class ExistingGalleryPreflightTests(unittest.TestCase):
    def base_evidence(self):
        return {
            "incomingModel": "external-source-001 / product:42",
            "identityReview": {
                "decision": "map_existing",
                "mapExistingApproved": True,
                "targetModelId": "mdl_existing",
            },
            "decision": {
                "galleryMediaReviewApproved": True,
                "newModelApproved": False,
                "batch005Approved": False,
            },
            "images": [
                {
                    "position": 1,
                    "sha256": "a" * 64,
                    "bytes": 100,
                    "stagingVerified": True,
                    "watermarkStatus": "creator_attribution",
                    "proposedAction": "replace_existing",
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

    def test_accepts_explicit_existing_model_review(self):
        target, incoming, images = validate_evidence(self.base_evidence())
        self.assertEqual(target, "mdl_existing")
        self.assertEqual(incoming, "external-source-001 / product:42")
        self.assertEqual(len(images), 2)

    def test_rejects_new_model_authorization(self):
        evidence = self.base_evidence()
        evidence["decision"]["newModelApproved"] = True
        with self.assertRaisesRegex(RuntimeError, "new_model"):
            validate_evidence(evidence)

    def test_rejects_incomplete_staging(self):
        evidence = self.base_evidence()
        evidence["images"][1]["stagingVerified"] = False
        with self.assertRaisesRegex(RuntimeError, "100%"):
            validate_evidence(evidence)

    def test_rejects_watermarked_input(self):
        evidence = self.base_evidence()
        evidence["images"][0]["watermarkStatus"] = "watermarked"
        with self.assertRaisesRegex(RuntimeError, "watermarkStatus"):
            validate_evidence(evidence)

    def test_staging_key_is_content_addressed(self):
        sha = "c" * 64
        self.assertEqual(staging_key("source", sha, "jpg"), f"source/sha256/cc/{sha}.jpg")


if __name__ == "__main__":
    unittest.main()
