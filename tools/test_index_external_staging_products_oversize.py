from __future__ import annotations

import urllib.parse
import unittest

from index_external_staging_products import scan_page


class OversizedReverseIndexTests(unittest.TestCase):
    def test_known_oversized_source_is_review_only_and_not_downloaded(self):
        sha = "a" * 64
        normal_size = 10
        oversized_size = 101
        inventory = {
            f"src/sha256/aa/{sha}.webp": normal_size,
            f"src/sha256/bb/{'b' * 64}.webp": oversized_size,
        }
        products = [{
            "id": 42,
            "name": "Mixed",
            "images": [
                {"id": 1, "src": "https://example.com/normal.webp"},
                {"id": 2, "src": "https://example.com/oversized.webp"},
            ],
        }]
        sizes = {
            "https://example.com/normal.webp": normal_size,
            "https://example.com/oversized.webp": oversized_size,
        }
        fetched: list[str] = []

        def fetch_image(url, _path, _limit):
            fetched.append(url)
            if url.endswith("oversized.webp"):
                self.fail("oversized source must not be downloaded")
            return sha, normal_size

        matched, completed, count, total, stopped, next_offset, returned = scan_page(
            origin="https://example.com", page=1, per_page=20, inventory=inventory,
            staging_prefix="src", max_image_bytes=100, remaining_bytes=1000,
            fetch_products=lambda _url: products,
            fetch_image=fetch_image,
            fetch_size=lambda url: sizes[url],
        )

        self.assertEqual(fetched, ["https://example.com/normal.webp"])
        self.assertEqual((completed, returned, count, total, stopped, next_offset), (1, 1, 1, 10, False, 1))
        self.assertEqual(matched[0]["stagingCoverage"], "1/2")
        self.assertEqual(matched[0]["reviewOnlyImageCount"], 1)
        oversized = matched[0]["images"][1]
        self.assertFalse(oversized["stagingVerified"])
        self.assertTrue(oversized["reviewOnly"])
        self.assertEqual(oversized["bytes"], oversized_size)
        self.assertEqual(oversized["hashSkippedReason"], "source_exceeds_max_image_bytes")

    def test_unknown_length_oversize_fetch_failure_becomes_review_only(self):
        sha = "c" * 64
        inventory = {f"src/sha256/cc/{sha}.webp": 10}
        products = [{
            "id": 43,
            "name": "Unknown size",
            "images": [
                {"id": 1, "src": "https://example.com/normal.webp"},
                {"id": 2, "src": "https://example.com/oversized.webp"},
            ],
        }]

        def fetch_image(url, _path, _limit):
            if url.endswith("oversized.webp"):
                raise RuntimeError("imagem excede limite individual de 100 bytes")
            return sha, 10

        matched, completed, count, total, stopped, next_offset, returned = scan_page(
            origin="https://example.com", page=1, per_page=20, inventory=inventory,
            staging_prefix="src", max_image_bytes=100, remaining_bytes=1000,
            fetch_products=lambda _url: products,
            fetch_image=fetch_image,
            fetch_size=lambda _url: None,
        )

        self.assertEqual((completed, returned, count, total, stopped, next_offset), (1, 1, 1, 10, False, 1))
        self.assertEqual(matched[0]["reviewOnlyImageCount"], 1)
        oversized = matched[0]["images"][1]
        self.assertIsNone(oversized["bytes"])
        self.assertTrue(oversized["reviewOnly"])
        self.assertEqual(
            oversized["hashSkippedReason"],
            "source_exceeds_max_image_bytes_unknown_length",
        )

    def test_oversized_only_product_is_not_falsely_matched(self):
        oversized_size = 101
        products = [{
            "id": 44,
            "name": "Only oversized",
            "images": [{"id": 1, "src": "https://example.com/oversized.webp"}],
        }]
        matched, completed, count, total, stopped, next_offset, returned = scan_page(
            origin="https://example.com", page=1, per_page=20,
            inventory={f"src/sha256/dd/{'d' * 64}.webp": oversized_size},
            staging_prefix="src", max_image_bytes=100, remaining_bytes=1000,
            fetch_products=lambda _url: products,
            fetch_image=lambda *_args: self.fail("oversized source must not be downloaded"),
            fetch_size=lambda _url: oversized_size,
        )
        self.assertEqual(matched, [])
        self.assertEqual((completed, returned, count, total, stopped, next_offset), (1, 1, 0, 0, False, 1))

    def test_source_page_is_stably_ordered_by_ascending_product_id(self):
        requested: list[str] = []

        def fetch_products(url):
            requested.append(url)
            return []

        scan_page(
            origin="https://example.com", page=7, per_page=20, inventory={},
            staging_prefix="src", max_image_bytes=100, remaining_bytes=1000,
            fetch_products=fetch_products,
            fetch_image=lambda *_args: self.fail("empty page must not fetch images"),
        )
        self.assertEqual(len(requested), 1)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(requested[0]).query)
        self.assertEqual(query["page"], ["7"])
        self.assertEqual(query["per_page"], ["20"])
        self.assertEqual(query["orderby"], ["id"])
        self.assertEqual(query["order"], ["asc"])

    def test_unicode_source_path_is_percent_encoded_before_size_and_download(self):
        sha = "e" * 64
        products = [{
            "id": 45,
            "name": "Unicode media",
            "images": [{"id": 1, "src": "https://example.com/uploads/ação.webp"}],
        }]
        requested_sizes: list[str] = []
        downloaded: list[str] = []

        def fetch_size(url):
            requested_sizes.append(url)
            return None

        def fetch_image(url, _path, _limit):
            downloaded.append(url)
            return sha, 10

        matched, completed, count, total, stopped, next_offset, returned = scan_page(
            origin="https://example.com", page=1, per_page=20,
            inventory={f"src/sha256/ee/{sha}.webp": 10},
            staging_prefix="src", max_image_bytes=100, remaining_bytes=1000,
            fetch_products=lambda _url: products,
            fetch_image=fetch_image,
            fetch_size=fetch_size,
        )
        expected = "https://example.com/uploads/a%C3%A7%C3%A3o.webp"
        self.assertEqual(requested_sizes, [expected])
        self.assertEqual(downloaded, [expected])
        self.assertTrue(matched[0]["completeStaging"])
        self.assertEqual((completed, returned, count, total, stopped, next_offset), (1, 1, 1, 10, False, 1))


if __name__ == "__main__":
    unittest.main()
