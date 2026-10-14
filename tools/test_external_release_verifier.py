from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_external_batch_release import model
from verify_external_batch_release import verify
from verify_public_catalog import CatalogSmokeError


class ExternalReleaseVerifierTests(unittest.TestCase):
    def candidate(self) -> dict:
        row = model()
        row["galleryVersion"] = 123456789
        return row

    @staticmethod
    def public_row(candidate: dict, public_revision: int = 1) -> dict:
        # Public catalog intentionally omits gallery_manifest_key because it is
        # a private R2 storage pointer. The public gallery endpoint is the proof
        # that the Worker can load and validate that private manifest.
        return {
            "id": candidate["id"],
            "slug": candidate["slug"],
            "code": candidate["code"],
            "image_count": candidate["imageCount"],
            "gallery_version": public_revision,
            "cover_storage_key": candidate["coverStorageKey"],
        }

    def test_physical_gallery_version_is_independent_from_public_revision(self) -> None:
        candidate = self.candidate()
        public = self.public_row(candidate, public_revision=1)
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models.jsonl"
            models.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            with patch(
                "verify_external_batch_release.request_json",
                return_value={"items": [public], "nextCursor": None},
            ), patch(
                "verify_external_batch_release.verify_public_model_gallery",
                return_value={"slug": candidate["slug"], "pages": 1, "images": 1, "version": 1},
            ):
                result = verify(models, "https://catalog.example")
        self.assertTrue(result["ok"])
        self.assertEqual(result["galleries"][0]["physicalGalleryVersion"], 123456789)
        self.assertEqual(result["galleries"][0]["publicGalleryVersion"], 1)

    def test_private_manifest_key_is_not_required_in_public_catalog(self) -> None:
        candidate = self.candidate()
        public = self.public_row(candidate, public_revision=2)
        self.assertNotIn("gallery_manifest_key", public)
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models.jsonl"
            models.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            with patch(
                "verify_external_batch_release.request_json",
                return_value={"items": [public], "nextCursor": None},
            ), patch(
                "verify_external_batch_release.verify_public_model_gallery",
                return_value={"slug": candidate["slug"], "pages": 1, "images": 1, "version": 2},
            ):
                result = verify(models, "https://catalog.example")
        self.assertTrue(result["ok"])

    def test_public_gallery_revision_must_be_positive(self) -> None:
        candidate = self.candidate()
        public = self.public_row(candidate, public_revision=0)
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models.jsonl"
            models.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            with patch(
                "verify_external_batch_release.request_json",
                return_value={"items": [public], "nextCursor": None},
            ):
                with self.assertRaisesRegex(CatalogSmokeError, "revisão pública de galeria inválida"):
                    verify(models, "https://catalog.example")


if __name__ == "__main__":
    unittest.main()
