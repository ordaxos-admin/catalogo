import threading
import time
import unittest

from index_external_staging_products import prefetch_source_sizes


class SourceSizePrefetchTests(unittest.TestCase):
    def test_prefetch_is_bounded_concurrent_and_deduplicates_urls(self):
        products = [{
            "id": 1,
            "images": [
                {"src": f"https://example.com/media/{index}.webp"}
                for index in range(8)
            ] + [{"src": "https://example.com/media/0.webp"}],
        }]
        lock = threading.Lock()
        active = 0
        max_active = 0
        calls: list[str] = []

        def fake_fetch_size(url: str) -> int:
            nonlocal active, max_active
            with lock:
                calls.append(url)
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return 100 + int(url.rsplit("/", 1)[-1].split(".", 1)[0])

        result = prefetch_source_sizes(
            products,
            origin="https://example.com",
            product_offset=0,
            fetch_size=fake_fetch_size,
            max_workers=6,
        )

        self.assertEqual(len(result), 8)
        self.assertEqual(len(calls), 8)
        self.assertEqual(len(set(calls)), 8)
        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, 6)
        self.assertEqual(result["https://example.com/media/7.webp"], 107)

    def test_prefetch_respects_product_offset(self):
        products = [
            {"id": 1, "images": [{"src": "https://example.com/old.webp"}]},
            {"id": 2, "images": [{"src": "https://example.com/new.webp"}]},
        ]
        calls: list[str] = []

        result = prefetch_source_sizes(
            products,
            origin="https://example.com",
            product_offset=1,
            fetch_size=lambda url: calls.append(url) or 55,
        )

        self.assertEqual(calls, ["https://example.com/new.webp"])
        self.assertEqual(result, {"https://example.com/new.webp": 55})


if __name__ == "__main__":
    unittest.main()
