import unittest

from apply_existing_gallery_promotion import (
    build_d1_statements,
    is_candidate_current,
    validate_current_manifest,
    validate_current_state,
)


class ApplyExistingGalleryPromotionTests(unittest.TestCase):
    def model(self):
        return {
            "id": "mdl_target",
            "slug": "kaido",
            "code": "TS-KAIDO",
            "image_count": 1,
            "cover_storage_key": "media/mdl_target/old/card.webp",
            "gallery_manifest_key": "gallery/mdl_target/old.json",
            "gallery_version": 10,
            "public_gallery_version": 3,
            "published": 1,
        }

    def evidence(self):
        return {
            "identityReview": {
                "targetModelId": "mdl_target",
                "targetSlug": "kaido",
                "targetCode": "TS-KAIDO",
                "targetImageCountBeforePromotion": 1,
            },
            "metricReview": {
                "existingCover": {
                    "storageKey": "media/mdl_target/old/card.webp",
                }
            },
        }

    def candidate(self):
        return {
            "modelId": "mdl_target",
            "imageCount": 7,
            "coverStorageKey": "media/mdl_target/new/card.webp",
            "galleryManifestKey": "gallery/mdl_target/new.json",
            "galleryVersion": 20,
        }

    def test_current_state_is_bound_to_reviewed_model(self):
        validate_current_state(self.evidence(), self.model())
        changed = self.model()
        changed["cover_storage_key"] = "media/mdl_target/other/card.webp"
        with self.assertRaisesRegex(RuntimeError, "capa atual mudou"):
            validate_current_state(self.evidence(), changed)

    def test_current_manifest_contract_must_match_d1(self):
        manifest = {
            "version": 10,
            "modelId": "mdl_target",
            "sourceHierarchy": ["Animes & Desenhos", "One Piece", "Kaido"],
            "images": [
                {
                    "role": "cover",
                    "variantKeys": {"card": "media/mdl_target/old/card.webp"},
                }
            ],
        }
        hierarchy = validate_current_manifest(manifest, self.model())
        self.assertEqual(hierarchy[-1], "Kaido")
        manifest["version"] = 11
        with self.assertRaisesRegex(RuntimeError, "gallery_version"):
            validate_current_manifest(manifest, self.model())

    def test_candidate_current_requires_all_gallery_pointers(self):
        current = self.model()
        candidate = self.candidate()
        self.assertFalse(is_candidate_current(current, candidate))
        current.update({
            "image_count": 7,
            "cover_storage_key": candidate["coverStorageKey"],
            "gallery_manifest_key": candidate["galleryManifestKey"],
            "gallery_version": candidate["galleryVersion"],
        })
        self.assertTrue(is_candidate_current(current, candidate))

    def test_d1_update_is_optimistically_bound_to_old_state(self):
        source_index = [
            {
                "model_id": "mdl_target",
                "image_id": "img_a",
                "position": 0,
                "role": "cover",
                "source_sha256": "a" * 64,
            },
            {
                "model_id": "mdl_target",
                "image_id": "img_b",
                "position": 1,
                "role": "gallery",
                "source_sha256": "b" * 64,
            },
        ]
        statements = build_d1_statements(self.model(), self.candidate(), source_index)
        self.assertEqual(len(statements), 3)
        self.assertIn("INSERT INTO model_image_sources", statements[0]["sql"])
        update = statements[-1]
        self.assertIn("WHERE id=?", update["sql"])
        self.assertIn("image_count=?", update["sql"])
        self.assertIn("cover_storage_key=?", update["sql"])
        self.assertIn("gallery_manifest_key=?", update["sql"])
        self.assertIn("gallery_version=?", update["sql"])
        self.assertEqual(update["params"][-4:], [
            1,
            "media/mdl_target/old/card.webp",
            "gallery/mdl_target/old.json",
            10,
        ])


if __name__ == "__main__":
    unittest.main()
