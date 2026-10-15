from __future__ import annotations

import unittest

from materialize_external_gallery_review import review_rows


class ExternalGalleryReviewTests(unittest.TestCase):
    def test_accepts_contiguous_visual_review_gallery(self):
        rows = review_rows({"images": [
            {"position": 2, "sourcePath": "/b.webp", "sha256": "b" * 64, "bytes": 20, "reviewState": "visual_review_required"},
            {"position": 1, "sourcePath": "/a.webp", "sha256": "a" * 64, "bytes": 10, "reviewState": "visual_review_required"},
        ]})
        self.assertEqual([row["position"] for row in rows], [1, 2])

    def test_rejects_duplicate_position(self):
        with self.assertRaisesRegex(RuntimeError, "duplicada"):
            review_rows({"images": [
                {"position": 1, "sourcePath": "/a.webp", "sha256": "a" * 64, "bytes": 10, "reviewState": "visual_review_required"},
                {"position": 1, "sourcePath": "/b.webp", "sha256": "b" * 64, "bytes": 20, "reviewState": "visual_review_required"},
            ]})

    def test_rejects_non_contiguous_gallery(self):
        with self.assertRaisesRegex(RuntimeError, "contíguas"):
            review_rows({"images": [
                {"position": 1, "sourcePath": "/a.webp", "sha256": "a" * 64, "bytes": 10, "reviewState": "visual_review_required"},
                {"position": 3, "sourcePath": "/c.webp", "sha256": "c" * 64, "bytes": 30, "reviewState": "visual_review_required"},
            ]})

    def test_rejects_preapproved_row(self):
        with self.assertRaisesRegex(RuntimeError, "não exige"):
            review_rows({"images": [
                {"position": 1, "sourcePath": "/a.webp", "sha256": "a" * 64, "bytes": 10, "reviewState": "approved"},
            ]})

    def test_rejects_invalid_sha_and_bytes(self):
        with self.assertRaisesRegex(RuntimeError, "sha256"):
            review_rows({"images": [
                {"position": 1, "sourcePath": "/a.webp", "sha256": "bad", "bytes": 10, "reviewState": "visual_review_required"},
            ]})
        with self.assertRaisesRegex(RuntimeError, "bytes"):
            review_rows({"images": [
                {"position": 1, "sourcePath": "/a.webp", "sha256": "a" * 64, "bytes": 0, "reviewState": "visual_review_required"},
            ]})


if __name__ == "__main__":
    unittest.main()
