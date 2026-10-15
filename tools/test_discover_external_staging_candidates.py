import base64
import unittest

from discover_external_staging_candidates import (
    candidate_state,
    decode_b64_text,
    extension_from_path,
    safe_source_path,
    staging_key,
)


class DiscoverExternalStagingCandidatesTests(unittest.TestCase):
    def test_decode_b64_text(self):
        value = base64.b64encode(b"https://example.test").decode("ascii")
        self.assertEqual(decode_b64_text(value, "origin"), "https://example.test")

    def test_safe_source_path_requires_same_https_origin(self):
        self.assertEqual(
            safe_source_path("https://example.test", "https://example.test/media/a.webp"),
            "/media/a.webp",
        )
        with self.assertRaises(RuntimeError):
            safe_source_path("https://example.test", "https://evil.test/media/a.webp")
        with self.assertRaises(RuntimeError):
            safe_source_path("https://example.test", "http://example.test/media/a.webp")
        with self.assertRaises(RuntimeError):
            safe_source_path("https://example.test", "https://example.test/media/a.webp?x=1")

    def test_extension_and_staging_key_are_canonical(self):
        sha = "a" * 64
        self.assertEqual(extension_from_path("/media/file.jpeg"), "jpg")
        self.assertEqual(
            staging_key("external/", sha, ".jpeg"),
            f"external/sha256/aa/{sha}.jpg",
        )

    def test_candidate_state_fails_closed_for_incomplete_staging(self):
        images = [
            {"stagingVerified": True, "productionOwners": []},
            {"stagingVerified": False, "productionOwners": []},
        ]
        self.assertEqual(candidate_state(images), "incomplete_staging")

    def test_candidate_state_surfaces_exact_sha_ownership_for_identity_review(self):
        images = [
            {"stagingVerified": True, "productionOwners": ["mdl_existing"]},
            {"stagingVerified": True, "productionOwners": []},
        ]
        self.assertEqual(candidate_state(images), "identity_review_required_exact_sha_overlap")

    def test_candidate_state_never_auto_promotes_complete_gallery(self):
        images = [
            {"stagingVerified": True, "productionOwners": []},
            {"stagingVerified": True, "productionOwners": []},
        ]
        self.assertEqual(candidate_state(images), "visual_review_required")


if __name__ == "__main__":
    unittest.main()
