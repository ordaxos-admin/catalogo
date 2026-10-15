import unittest

from materialize_external_source_review import safe_source_url, selected_review, selected_reviews


class ExternalSourceReviewTests(unittest.TestCase):
    def source_map(self):
        return {
            "missingSource": {
                "position": 1,
                "sourcePath": "/wp-content/uploads/a.webp",
                "sha256": "a" * 64,
                "bytes": 123,
            },
            "images": [
                {
                    "position": 1,
                    "sourcePath": "/wp-content/uploads/a.webp",
                    "sha256": "a" * 64,
                    "bytes": 123,
                    "stagingVerified": False,
                    "reviewState": "visual_review_required",
                }
            ],
        }

    def multi_source_map(self):
        return {
            "reviewSources": [
                {"position": 1, "sourcePath": "/wp-content/uploads/a.webp", "sha256": "a" * 64, "bytes": 123},
                {"position": 3, "sourcePath": "/wp-content/uploads/c.webp", "sha256": "c" * 64, "bytes": 456},
            ],
            "images": [
                {"position": 1, "sourcePath": "/wp-content/uploads/a.webp", "sha256": "a" * 64, "bytes": 123, "stagingVerified": False, "reviewState": "visual_review_required"},
                {"position": 2, "sourcePath": "/wp-content/uploads/b.webp", "sha256": "b" * 64, "bytes": 200, "stagingVerified": True, "watermarkStatus": "clear"},
                {"position": 3, "sourcePath": "/wp-content/uploads/c.webp", "sha256": "c" * 64, "bytes": 456, "stagingVerified": False, "reviewState": "visual_review_required"},
            ],
        }

    def test_selects_exact_missing_review_row(self):
        row = selected_review(self.source_map())
        self.assertEqual(row["sha256"], "a" * 64)

    def test_selects_multiple_explicit_review_rows(self):
        rows = selected_reviews(self.multi_source_map())
        self.assertEqual([row["position"] for row in rows], [1, 3])
        self.assertEqual([row["sha256"] for row in rows], ["a" * 64, "c" * 64])

    def test_single_accessor_rejects_multiple_reviews(self):
        with self.assertRaisesRegex(RuntimeError, "múltiplas"):
            selected_review(self.multi_source_map())

    def test_rejects_already_staged_source(self):
        data = self.source_map()
        data["images"][0]["stagingVerified"] = True
        with self.assertRaisesRegex(RuntimeError, "ausente do staging"):
            selected_review(data)

    def test_rejects_duplicate_review_position(self):
        data = self.multi_source_map()
        data["reviewSources"][1]["position"] = 1
        with self.assertRaisesRegex(RuntimeError, "position inválida/duplicada"):
            selected_reviews(data)

    def test_safe_source_url_preserves_authorized_origin(self):
        self.assertEqual(
            safe_source_url("https://example.com", "/wp-content/a.webp"),
            "https://example.com/wp-content/a.webp",
        )

    def test_rejects_traversal_and_query(self):
        with self.assertRaises(RuntimeError):
            safe_source_url("https://example.com", "/wp-content/../secret.webp")
        with self.assertRaises(RuntimeError):
            safe_source_url("https://example.com", "/wp-content/a.webp?x=1")


if __name__ == "__main__":
    unittest.main()
