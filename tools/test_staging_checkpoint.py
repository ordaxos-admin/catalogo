#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import staging_checkpoint as checkpoint


def row(sha: str) -> dict:
    return {
        "sourceId": "external-source-001",
        "productId": 1,
        "imageIdentity": sha[:8],
        "sourceUrl": "https://example.invalid/image.jpg",
        "size": 123,
        "sha256": sha,
        "remoteKey": f"external-source-001/sha256/{sha[:2]}/{sha}.jpg",
        "remoteVerified": True,
        "verifiedAtUnix": 1,
    }


class StagingCheckpointTests(unittest.TestCase):
    def test_baseline_and_journal_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = checkpoint._empty_state()
            base["objects"]["product-1/old.jpg"] = row("a" * 64)
            (root / checkpoint.BASE_STATE_NAME).write_text(
                json.dumps(base), encoding="utf-8"
            )

            checkpoint.append_verified_object(
                root, "product-1/new.jpg", row("b" * 64)
            )
            state = checkpoint.load_state(root)

            self.assertEqual(set(state["objects"]), {
                "product-1/old.jpg",
                "product-1/new.jpg",
            })
            self.assertEqual(state["objects"]["product-1/new.jpg"]["sha256"], "b" * 64)

    def test_trailing_partial_journal_line_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint.append_verified_object(
                root, "product-1/ok.jpg", row("c" * 64)
            )
            journal = root / checkpoint.JOURNAL_NAME
            with journal.open("ab") as handle:
                handle.write(b'{"version":1,"op":"upsert"')

            state = checkpoint.load_state(root)
            self.assertIn("product-1/ok.jpg", state["objects"])

    def test_complete_corrupt_journal_line_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / checkpoint.JOURNAL_NAME).write_bytes(b"{not-json}\n")
            with self.assertRaises(RuntimeError):
                checkpoint.load_state(root)

    def test_append_rejects_unverified_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = row("d" * 64)
            bad["remoteVerified"] = False
            with self.assertRaises(ValueError):
                checkpoint.append_verified_object(root, "product-1/bad.jpg", bad)

    def test_process_lease_is_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = checkpoint.acquire_checkpoint_lease(root)
            try:
                with self.assertRaises(RuntimeError):
                    checkpoint.acquire_checkpoint_lease(root)
            finally:
                first.close()
            second = checkpoint.acquire_checkpoint_lease(root)
            second.close()


if __name__ == "__main__":
    unittest.main()
