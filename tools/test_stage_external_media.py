import base64
import json
import tempfile
import unittest
from pathlib import Path

from stage_external_media import (
    extract_evidence_rows,
    extension_for,
    select_rows,
    source_path,
    staging_key,
    validate_source_contract,
)


class StageExternalMediaTests(unittest.TestCase):
    def test_source_contract_decodes_neutral_origin_and_prefix(self):
        contract = {
            "version": 1,
            "sourceId": "external-source-001",
            "sourceOriginB64": base64.b64encode(b"https://example.test").decode("ascii"),
            "stagingBucket": "private-staging",
            "stagingPrefixB64": base64.b64encode(b"example-source").decode("ascii"),
        }
        source_id, origin, prefix, bucket = validate_source_contract(contract)
        self.assertEqual(source_id, "external-source-001")
        self.assertEqual(origin, "https://example.test")
        self.assertEqual(prefix, "example-source")
        self.assertEqual(bucket, "private-staging")

    def test_extracts_nested_images_and_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images_path = root / "images.json"
            images_path.write_text(json.dumps({"images": [{"sha256": "a" * 64}]}), encoding="utf-8")
            candidates_path = root / "candidates.json"
            candidates_path.write_text(json.dumps({"candidates": [{"sha256": "b" * 64}]}), encoding="utf-8")
            self.assertEqual(extract_evidence_rows(images_path)[0]["sha256"], "a" * 64)
            self.assertEqual(extract_evidence_rows(candidates_path)[0]["sha256"], "b" * 64)

    def test_private_staging_accepts_unknown_review_for_future_visual_audit(self):
        sha = "a" * 64
        rows = [{
            "sha256": sha,
            "bytes": 123,
            "sourceObjectPath": "/media/image.webp",
            "watermarkStatus": "unknown",
        }]
        selected = select_rows(rows, [sha])
        self.assertEqual(selected, rows)

    def test_known_watermarked_media_is_rejected(self):
        sha = "b" * 64
        rows = [{
            "sha256": sha,
            "bytes": 123,
            "sourceObjectPath": "/media/image.webp",
            "watermarkStatus": "watermarked",
        }]
        with self.assertRaisesRegex(RuntimeError, "watermark"):
            select_rows(rows, [sha])

    def test_selected_sha_must_exist_in_evidence(self):
        with self.assertRaisesRegex(RuntimeError, "ausente"):
            select_rows([], ["c" * 64])

    def test_source_path_and_content_addressed_key_fail_closed(self):
        row = {"sourceObjectPath": "/media/image.jpeg"}
        self.assertEqual(source_path(row), "/media/image.jpeg")
        self.assertEqual(extension_for(source_path(row)), "jpg")
        sha = "d" * 64
        self.assertEqual(staging_key("source", sha, "jpg"), f"source/sha256/dd/{sha}.jpg")
        with self.assertRaises(RuntimeError):
            source_path({"sourceObjectPath": "/media/../secret.webp"})


if __name__ == "__main__":
    unittest.main()
