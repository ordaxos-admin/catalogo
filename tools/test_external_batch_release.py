from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from materialize_external_media_batch import validate_descriptor, validate_evidence
from publish_d1_new_models import new_only_statements, validate_new_only, validate_r2_proof, validate_sha_new_only
from publish_d1 import build_statements


def model(model_id: str = "mdl_new", slug: str = "new-model", code: str = "TS-NEW") -> dict:
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
        "collection": "",
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

    def test_r2_proof_must_match_bundle_exactly(self) -> None:
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
                "bucket": "tonecos-catalogo-media",
                "objects": {
                    "media/mdl_new/img/card.webp": {
                        "sha256": digest,
                        "size": len(payload),
                        "verified": True,
                    }
                },
            }), encoding="utf-8")
            result = validate_r2_proof(models, proof)
            self.assertTrue(result["ready"])
            self.assertEqual(result["objects"], 1)
            proof.write_text(json.dumps({"version": 1, "objects": {}}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "não corresponde exatamente"):
                validate_r2_proof(models, proof)


class MaterializeEvidenceTests(unittest.TestCase):
    def descriptor(self) -> dict:
        return {
            "version": 1,
            "sourceOrigin": "https://stlforge.cl",
            "evidence": "docs/evidence/test.jsonl",
            "expectedProducts": 1,
            "expectedImages": 1,
            "products": [{
                "productId": 7,
                "displayName": "Example",
                "publicModelKey": "example",
                "sourceFolders": ["Games", "Example", "Example"],
                "expectedImages": 1,
            }],
        }

    def evidence(self) -> list[dict]:
        return [{
            "productId": 7,
            "imageId": "8",
            "position": 1,
            "sourceUrl": "https://stlforge.cl/wp-content/uploads/example.jpg",
            "sha256": "a" * 64,
            "bytes": 10,
            "extension": "jpg",
            "stagingVerified": True,
            "review": "clear",
            "publicModelKey": "example",
        }]

    def test_evidence_requires_authorized_origin_and_clear_review(self) -> None:
        descriptor = self.descriptor()
        products = validate_descriptor(descriptor, Path("batch.json"))
        validate_evidence(self.evidence(), descriptor, products)
        rows = self.evidence()
        rows[0]["sourceUrl"] = "https://example.invalid/a.jpg"
        with self.assertRaisesRegex(RuntimeError, "origem autorizada"):
            validate_evidence(rows, descriptor, products)
        rows = self.evidence()
        rows[0]["review"] = "unknown"
        with self.assertRaisesRegex(RuntimeError, "clear/stagingVerified"):
            validate_evidence(rows, descriptor, products)

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
