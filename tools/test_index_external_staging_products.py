from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from index_external_staging_products import (
    empty_state, load_state, scan_page, save_state, summarize_product,
)


class StagedProductIndexerTests(unittest.TestCase):
    def test_only_products_with_staged_sha_are_retained(self):
        sha_a, sha_b = "a" * 64, "b" * 64
        inventory = {f"src/sha256/{sha_a[:2]}/{sha_a}.webp": 10}
        products = [
            {"id": 1, "name": "Matched", "slug": "matched", "images": [{"id": 11, "src": "https://example.com/a.webp"}]},
            {"id": 2, "name": "Ignored", "slug": "ignored", "images": [{"id": 22, "src": "https://example.com/b.webp"}]},
        ]
        hashes = {"https://example.com/a.webp": (sha_a, 10), "https://example.com/b.webp": (sha_b, 12)}
        matched, returned, count, total, stopped = scan_page(
            origin="https://example.com", page=1, per_page=20, inventory=inventory,
            staging_prefix="src", max_image_bytes=100, remaining_bytes=1000,
            fetch_products=lambda _url: products,
            fetch_image=lambda url, _path, _limit: hashes[url],
        )
        self.assertEqual([row["productId"] for row in matched], [1])
        self.assertEqual(matched[0]["stagingCoverage"], "1/1")
        self.assertTrue(matched[0]["completeStaging"])
        self.assertEqual(returned, 2)
        self.assertEqual(count, 2)
        self.assertEqual(total, 22)
        self.assertFalse(stopped)

    def test_partial_product_is_preserved_but_not_complete(self):
        sha_a, sha_b = "a" * 64, "b" * 64
        inventory = {f"src/sha256/aa/{sha_a}.webp": 10}
        products = [{"id": 7, "name": "Partial", "images": [
            {"id": 1, "src": "https://example.com/a.webp"},
            {"id": 2, "src": "https://example.com/b.webp"},
        ]}]
        hashes = {"https://example.com/a.webp": (sha_a, 10), "https://example.com/b.webp": (sha_b, 11)}
        matched, returned, *_ = scan_page(
            origin="https://example.com", page=2, per_page=20, inventory=inventory,
            staging_prefix="src", max_image_bytes=100, remaining_bytes=1000,
            fetch_products=lambda _url: products,
            fetch_image=lambda url, _path, _limit: hashes[url],
        )
        self.assertEqual(returned, 1)
        self.assertEqual(matched[0]["stagingCoverage"], "1/2")
        self.assertFalse(matched[0]["completeStaging"])

    def test_byte_bound_stops_before_committing_partial_product(self):
        sha = "a" * 64
        products = [{"id": 9, "name": "Too large", "images": [
            {"id": 1, "src": "https://example.com/a.webp"},
            {"id": 2, "src": "https://example.com/b.webp"},
        ]}]
        matched, returned, count, total, stopped = scan_page(
            origin="https://example.com", page=3, per_page=20,
            inventory={f"src/sha256/aa/{sha}.webp": 8}, staging_prefix="src",
            max_image_bytes=100, remaining_bytes=10,
            fetch_products=lambda _url: products,
            fetch_image=lambda _url, _path, _limit: (sha, 8),
        )
        self.assertEqual(matched, [])
        self.assertEqual(returned, 1)
        self.assertEqual(count, 1)
        self.assertEqual(total, 8)
        self.assertTrue(stopped)

    def test_empty_source_page_reports_zero_products(self):
        matched, returned, count, total, stopped = scan_page(
            origin="https://example.com", page=99, per_page=20, inventory={},
            staging_prefix="src", max_image_bytes=100, remaining_bytes=1000,
            fetch_products=lambda _url: [],
            fetch_image=lambda *_args: self.fail("no image should be fetched"),
        )
        self.assertEqual((matched, returned, count, total, stopped), ([], 0, 0, 0, False))

    def test_checkpoint_roundtrip_and_source_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            state = empty_state("source-a", 1440)
            state["pagesCompleted"] = [4, 5]
            state["matchedProducts"]["10"] = {"productId": 10}
            save_state(path, state)
            restored = load_state(path, "source-a", 1441)
            self.assertEqual(restored["pagesCompleted"], [4, 5])
            self.assertEqual(restored["stagingInventoryObjects"], 1441)
            with self.assertRaisesRegex(RuntimeError, "outra fonte"):
                load_state(path, "source-b", 1441)

    def test_read_only_checkpoint_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            state = empty_state("source-a", 10)
            state["productionWrites"] = 1
            path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                load_state(path, "source-a", 10)

    def test_summary_handles_duplicate_sha_views_without_merging(self):
        sha = "c" * 64
        product = {"id": 12, "name": "Views", "images": [{}, {}]}
        images = [{"sha256": sha, "stagingVerified": True}, {"sha256": sha, "stagingVerified": True}]
        row = summarize_product(product, images)
        self.assertEqual(row["sourceImageCount"], 2)
        self.assertEqual(row["stagedImageCount"], 2)
        self.assertTrue(row["completeStaging"])
        self.assertEqual(len(row["images"]), 2)


if __name__ == "__main__":
    unittest.main()
