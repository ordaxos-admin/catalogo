import unittest

from discover_external_staging_candidates import (
    candidate_state,
    canonical_source_url,
    review_artifact_eligible,
)
from enrich_external_candidate_identity import identity_review_state_supported


class IncompleteIdentityReviewTests(unittest.TestCase):
    def test_incomplete_staging_keeps_exact_pixels_for_read_only_review(self):
        images = [
            {"stagingVerified": True, "productionOwners": []},
            {"stagingVerified": False, "productionOwners": []},
        ]
        state = candidate_state(images)
        self.assertEqual(state, "incomplete_staging")
        self.assertTrue(review_artifact_eligible(state))
        self.assertTrue(identity_review_state_supported(state))

    def test_byte_limit_state_is_not_review_artifact_eligible(self):
        self.assertFalse(review_artifact_eligible("discovery_byte_limit_reached"))
        self.assertFalse(identity_review_state_supported("discovery_byte_limit_reached"))

    def test_complete_review_states_remain_supported(self):
        for state in (
            "visual_review_required",
            "identity_review_required_exact_sha_overlap",
        ):
            with self.subTest(state=state):
                self.assertTrue(review_artifact_eligible(state))
                self.assertTrue(identity_review_state_supported(state))

    def test_source_url_is_ascii_encoded_after_same_origin_validation(self):
        self.assertEqual(
            canonical_source_url("https://example.com", "/media/ação.webp"),
            "https://example.com/media/a%C3%A7%C3%A3o.webp",
        )


if __name__ == "__main__":
    unittest.main()
