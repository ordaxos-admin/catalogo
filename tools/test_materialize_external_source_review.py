import unittest

from materialize_external_source_review import safe_source_url, selected_review


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

    def test_selects_exact_missing_review_row(self):
        row = selected_review(self.source_map())
        self.assertEqual(row["sha256"], "a" * 64)

    def test_rejects_already_staged_source(self):
        data = self.source_map()
        data["images"][0]["stagingVerified"] = True
        with self.assertRaisesRegex(RuntimeError, "ausente do staging"):
            selected_review(data)

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
