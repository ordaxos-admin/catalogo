from __future__ import annotations

import unittest

from publish_d1_new_models import validate_released_preflight, validate_released_sha_preflight
from test_external_batch_release import model


class ReleasedBatchPreflightTests(unittest.TestCase):
    @staticmethod
    def production_row(candidate: dict) -> dict:
        return {
            "id": candidate["id"],
            "slug": candidate["slug"],
            "code": candidate["code"],
            "name": candidate["displayName"],
            "variant_name": candidate["variantName"],
            "collection": candidate["collection"],
            "folder_path": candidate["folderPathKey"],
            "image_count": candidate["imageCount"],
            "cover_storage_key": candidate["coverStorageKey"],
            "gallery_manifest_key": candidate["galleryManifestKey"],
            "gallery_version": candidate["galleryVersion"],
            "gallery_source_attached": 0,
            "gallery_canonical_model_id": None,
            "category_slug": candidate["categorySlug"],
            "franchise_slug": candidate["franchiseSlug"],
        }

    def test_exact_released_batch_is_valid_read_only_preflight(self) -> None:
        candidate = model()
        summary = validate_released_preflight([candidate], [self.production_row(candidate)])
        self.assertEqual(summary["existingModels"], 1)
        self.assertEqual(summary["newModels"], 0)
        self.assertEqual(summary["unchangedGalleries"], 1)

    def test_partial_release_is_not_accepted_as_idempotent(self) -> None:
        first = model()
        second = model(
            model_id="mdl_fedcba9876543210abcd",
            slug="other-model",
            code="TS-FEDCBA987654",
        )
        second["identityKey"] = "Games / Example / Other Model / other-model"
        second["displayName"] = "Other Model"
        second["collection"] = "Other Model"
        second["folderPath"] = ["Other Model"]
        second["folderPathKey"] = "other-model"
        second["searchText"] = "games example other model"
        with self.assertRaisesRegex(RuntimeError, "publicação parcial ou divergente"):
            validate_released_preflight([first, second], [self.production_row(first)])

    def test_released_sha_preflight_requires_all_images_and_same_owner(self) -> None:
        candidate = model()
        sha = "a" * 64
        sources = [{"source_sha256": sha, "model_id": candidate["id"]}]
        production = [{
            "source_sha256": sha,
            "effective_model_id": candidate["id"],
            "effective_model_slug": candidate["slug"],
        }]
        summary = validate_released_sha_preflight(sources, production)
        self.assertEqual(summary["productionShaMatches"], 1)
        with self.assertRaisesRegex(RuntimeError, "incompleto"):
            validate_released_sha_preflight(sources, [])


if __name__ == "__main__":
    unittest.main()
