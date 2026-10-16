from __future__ import annotations

import unittest

from materialize_identity_visual_review import (
    normalize_api_base,
    production_candidate,
    public_media_url,
    source_rows,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


class IdentityVisualReviewTests(unittest.TestCase):
    def test_source_rows_require_contiguous_unique_verified_metadata(self) -> None:
        rows = source_rows({"sourceImages": [
            {"position": 2, "sourcePath": "/b.jpg", "sha256": SHA_B, "bytes": 20},
            {"position": 1, "sourcePath": "/a.jpg", "sha256": SHA_A, "bytes": 10},
        ]})
        self.assertEqual([row["position"] for row in rows], [1, 2])

    def test_source_rows_reject_duplicate_sha(self) -> None:
        with self.assertRaises(RuntimeError):
            source_rows({"sourceImages": [
                {"position": 1, "sourcePath": "/a.jpg", "sha256": SHA_A, "bytes": 10},
                {"position": 2, "sourcePath": "/b.jpg", "sha256": SHA_A, "bytes": 20},
            ]})

    def test_candidate_requires_canonical_model_and_media_key(self) -> None:
        row = production_candidate({"productionCandidate": {
            "modelId": "mdl_abc",
            "slug": "big-boss",
            "name": "Big Boss",
            "franchise": "Metal Gear",
            "coverStorageKey": "media/mdl_abc/img_x/card.webp",
            "sourceSha256": SHA_A,
        }})
        self.assertEqual(row["modelId"], "mdl_abc")
        self.assertEqual(row["sourceSha256"], SHA_A)

    def test_public_media_url_encodes_reserved_characters_but_keeps_path(self) -> None:
        url = public_media_url(
            "https://catalog.example.test/",
            "media/mdl_abc/image name/card.webp",
        )
        self.assertEqual(url, "https://catalog.example.test/media/media/mdl_abc/image%20name/card.webp")

    def test_api_base_is_https_origin_only(self) -> None:
        self.assertEqual(normalize_api_base("https://catalog.example.test/"), "https://catalog.example.test")
        for invalid in ("http://catalog.example.test", "https://catalog.example.test/x?y=1", "catalog.example.test"):
            with self.assertRaises(RuntimeError):
                normalize_api_base(invalid)


if __name__ == "__main__":
    unittest.main()
