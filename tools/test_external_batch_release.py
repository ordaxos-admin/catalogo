from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from materialize_external_media_batch import staging_key, validate_descriptor, validate_evidence
from publish_d1_new_models import (
    build_transaction,
    new_only_statements,
    validate_new_only,
    validate_r2_proof,
    validate_sha_new_only,
)
from publish_d1 import build_statements


def model(model_id: str = "mdl_0123456789abcdefabcd", slug: str = "new-model", code: str = "TS-0123456789AB") -> dict:
    return {
        "identityKey": "Games / Example / New Model / new-model",
        "id": model_id,
        "slug": slug,
        "code": code,
        "displayName": "New Model",
        "variantName": "",
        "categoryName": "Games",
        "categorySlug": "games",
        "franchiseName": "Example",
        "franchiseSlug": "example",
        "collection": "New Model",
        "folderPath": ["New Model"],
        "folderPathKey": "new-model",
        "searchText": "games example new model",
        "imageCount": 1,
        "coverStorageKey": f"media/{model_id}/img/card.webp",
        "galleryManifestKey": f"gallery/{model_id}/abc.json",
        "galleryVersion": 1,
    }


class NewOnlyPublisherTests(unittest.TestCase):
    def test_existing_model_collision_fails_closed(self) -> None:
        row = model()
        with self.assertRaisesRegex(RuntimeError, "colide com modelo já publicado"):
            validate_new_only([row], [{"id": row["id"], "slug": row["slug"], "code": row["code"]}])

    def test_existing_sha_collision_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "SHA.*já publicados"):
            validate_sha_new_only([
                {"source_sha256": "a" * 64, "effective_model_id": "mdl_old", "effective_model_slug": "old"}
            ])

    def test_new_only_sql_never_updates_existing_model_or_taxonomy(self) -> None:
        statements = new_only_statements([model()])
        self.assertEqual(len(statements), len(build_statements([model()])))
        model_sql = [item["sql"] for item in statements if item["sql"].startswith("INSERT INTO models(")]
        self.assertEqual(len(model_sql), 1)
        self.assertNotIn("ON CONFLICT", model_sql[0])
        for item in statements:
            if item["sql"].startswith("INSERT INTO models("):
                continue
            self.assertIn("DO NOTHING", item["sql"])
            self.assertNotIn("DO UPDATE", item["sql"])
        self.assertFalse(any("DELETE" in item["sql"].upper() for item in statements))

    def test_transaction_combines_model_and_image_statements_once(self) -> None:
        image_statement = {"sql": "INSERT INTO model_image_sources VALUES(?,?,?,?,?,?)", "params": ["a"] * 6}
        with patch("publish_d1_new_models.build_image_source_statements", return_value=[image_statement]):
            transaction = build_transaction([model()], Path("models.jsonl"))
        self.assertEqual(transaction[-1], image_statement)
        self.assertEqual(len(transaction), len(new_only_statements([model()])) + 1)

    def test_r2_proof_must_match_bundle_and_bucket_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r2 = root / "r2"
            media = r2 / "media" / "mdl_new" / "img"
            media.mkdir(parents=True)
            payload = b"webp-bytes"
            file = media / "card.webp"
            file.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            models = root / "models.jsonl"
            models.write_text("{}\n", encoding="utf-8")
            proof = root / "proof.json"
            proof.write_text(json.dumps({
                "version": 1,
                "bucket": "public-media",
                "objects": {
                    "media/mdl_new/img/card.webp": {
                        "sha256": digest,
                        "size": len(payload),
                        "verified": True,
                    }
                },
            }), encoding="utf-8")
            result = validate_r2_proof(models, proof, "public-media")
            self.assertTrue(result["ready"])
            self.assertEqual(result["objects"], 1)
            with self.assertRaisesRegex(RuntimeError, "bucket errado"):
                validate_r2_proof(models, proof, "wrong-bucket")
            proof.write_text(json.dumps({"version": 1, "bucket": "public-media", "objects": {}}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "não corresponde exatamente"):
                validate_r2_proof(models, proof, "public-media")


class MaterializeEvidenceTests(unittest.TestCase):
    @staticmethod
    def b64(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    def descriptor(self) -> dict:
        return {
            "version": 2,
            "sourceId": "external-source-test",
            "sourceOriginB64": self.b64("https://source.example"),
            "stagingBucket": "private-staging",
            "stagingPrefixB64": self.b64("provider-cache"),
            "productionBucket": "public-media",
            "evidence": "docs/evidence/test.jsonl",
            "expectedProducts": 1,
            "expectedImages": 1,
            "products": [{
                "productId": 7,
                "displayName": "Example",
                "publicModelKey": "example",
                "sourceFolders": ["Games", "Example", "Example"],
                "expectedImages": 1,
                "expectedIdentityKey": "Games / Example / Example / example",
                "expectedModelId": "mdl_0123456789abcdefabcd",
                "expectedSlug": "example-example",
                "expectedCode": "TS-0123456789AB",
            }],
        }

    def evidence(self) -> list[dict]:
        return [{
            "productId": 7,
            "imageId": "8",
            "position": 1,
            "sourcePath": "/wp-content/uploads/example.jpg",
            "sha256": "a" * 64,
            "bytes": 10,
            "extension": "jpg",
            "stagingVerified": True,
            "review": "clear",
            "publicModelKey": "example",
        }]

    def test_evidence_requires_safe_source_path_and_clear_review(self) -> None:
        descriptor = self.descriptor()
        products = validate_descriptor(descriptor, Path("batch.json"))
        validate_evidence(self.evidence(), descriptor, products)
        rows = self.evidence()
        rows[0]["sourcePath"] = "../escape.jpg"
        with self.assertRaisesRegex(RuntimeError, "sourcePath inválido"):
            validate_evidence(rows, descriptor, products)
        rows = self.evidence()
        rows[0]["review"] = "unknown"
        with self.assertRaisesRegex(RuntimeError, "clear/stagingVerified"):
            validate_evidence(rows, descriptor, products)

    def test_staging_key_is_derived_only_from_prefix_sha_and_extension(self) -> None:
        row = self.evidence()[0]
        self.assertEqual(
            staging_key("provider-cache", row),
            f"provider-cache/sha256/aa/{'a' * 64}.jpg",
        )

    def test_evidence_rejects_duplicate_sha(self) -> None:
        descriptor = self.descriptor()
        descriptor["expectedImages"] = 2
        descriptor["products"][0]["expectedImages"] = 2
        products = validate_descriptor(descriptor, Path("batch.json"))
        rows = self.evidence() * 2
        rows[1] = dict(rows[1], imageId="9", position=2)
        with self.assertRaisesRegex(RuntimeError, "duplicado"):
            validate_evidence(rows, descriptor, products)


if __name__ == "__main__":
    unittest.main()
