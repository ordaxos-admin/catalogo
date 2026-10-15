import base64
import unittest

from discover_external_staging_candidates import (
    candidate_state,
    decode_b64_text,
    extension_from_path,
    safe_source_path,
    select_products,
    staging_key,
    validate_exact_product,
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

    def test_select_products_preserves_bounded_prefix_without_exact_target(self):
        products = [{"id": 1}, {"id": 2}, {"id": 3}]
        self.assertEqual([row["id"] for row in select_products(products, 2)], [1, 2])

    def test_select_products_uses_exact_target_even_beyond_bounded_prefix(self):
        products = [{"id": 1}, {"id": 2}, {"id": 3}]
        self.assertEqual(
            [row["id"] for row in select_products(products, 1, product_id=3)],
            [3],
        )

    def test_select_products_fails_closed_when_exact_target_is_missing(self):
        products = [{"id": 1}, {"id": 2}]
        with self.assertRaisesRegex(RuntimeError, "deve existir exatamente uma vez"):
            select_products(products, 2, product_id=3)

    def test_select_products_fails_closed_when_exact_target_is_duplicated(self):
        products = [{"id": 3}, {"id": 3}]
        with self.assertRaisesRegex(RuntimeError, "encontrado=2"):
            select_products(products, 2, product_id=3)

    def test_validate_exact_product_accepts_requested_id(self):
        product = {"id": 13687, "name": "Zoro"}
        self.assertIs(validate_exact_product(product, 13687), product)

    def test_validate_exact_product_rejects_non_object(self):
        with self.assertRaisesRegex(RuntimeError, "não retornou objeto"):
            validate_exact_product([], 13687)

    def test_validate_exact_product_rejects_wrong_id(self):
        with self.assertRaisesRegex(RuntimeError, "esperado=13687"):
            validate_exact_product({"id": 13688}, 13687)

    def test_validate_exact_product_rejects_non_positive_id(self):
        with self.assertRaisesRegex(RuntimeError, "deve ser positivo"):
            validate_exact_product({"id": 1}, 0)

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
