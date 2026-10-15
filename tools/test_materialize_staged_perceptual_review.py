import unittest

from materialize_staged_perceptual_review import review_assets


class StagedPerceptualReviewTests(unittest.TestCase):
    def evidence(self):
        return {
            "assets": [
                {"file": "left.webp", "sha256": "a" * 64, "bytes": 10},
                {"file": "right.webp", "sha256": "b" * 64, "bytes": 20},
            ],
            "perceptualReview": {
                "candidatePairs": [
                    {
                        "leftSha256": "a" * 64,
                        "rightSha256": "b" * 64,
                        "state": "explicit_visual_decision_required",
                        "automaticPromotionAllowed": False,
                    }
                ]
            },
        }

    def test_selects_only_explicit_pair_assets(self):
        rows = review_assets(self.evidence())
        self.assertEqual([row["file"] for row in rows], ["left.webp", "right.webp"])

    def test_rejects_automatic_promotion(self):
        data = self.evidence()
        data["perceptualReview"]["candidatePairs"][0]["automaticPromotionAllowed"] = True
        with self.assertRaisesRegex(RuntimeError, "fail-closed"):
            review_assets(data)

    def test_rejects_pair_without_matching_asset(self):
        data = self.evidence()
        data["perceptualReview"]["candidatePairs"][0]["rightSha256"] = "c" * 64
        with self.assertRaisesRegex(RuntimeError, "sem asset correspondente"):
            review_assets(data)


if __name__ == "__main__":
    unittest.main()
