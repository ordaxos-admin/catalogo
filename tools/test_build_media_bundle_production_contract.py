from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from build_media_bundle import build_bundle, stable_id


class MediaBundleProductionContractTests(unittest.TestCase):
    def test_builder_preserves_published_public_identity_from_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Games" / "Saga" / "Heroi"
            model_dir.mkdir(parents=True)
            image = model_dir / "novo.jpg"
            Image.new("RGB", (320, 480), "#334455").save(image)

            identity_key = "Games / Saga / Heroi / novo"
            model_id = stable_id("mdl", identity_key)
            code = "TS-" + hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:12].upper()
            manifest = root / "manifest.jsonl"
            manifest.write_text(json.dumps({
                "path": str(image.relative_to(source)),
                "size": image.stat().st_size,
                "status": "OK",
                "canonical": True,
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "width": 320,
                "height": 480,
                "quality_score": 80.0,
                "model_key": "Games / Saga / Heroi",
                "public_model_key": identity_key,
            }) + "\n", encoding="utf-8")

            contract = root / "production-contract.json"
            contract.write_text(json.dumps({
                "version": 1,
                "source": {"productionModels": 1, "publishedModels": 1},
                "models": [{
                    "id": model_id,
                    "slug": "saga-heroi-publicado",
                    "code": code,
                    "displayName": "Heroi Publicado",
                    "variantName": "Classico",
                    "categoryName": "Games",
                    "categorySlug": "games",
                    "franchiseName": "Saga",
                    "franchiseSlug": "saga",
                    "collection": "Colecao",
                    "folderPath": ["Colecao"],
                    "folderPathKey": "colecao",
                    "published": 1,
                }],
            }), encoding="utf-8")

            output = root / "bundle"
            summary = build_bundle(
                source,
                manifest,
                output,
                include_original=False,
                production_contract_config=contract,
                workers=1,
            )
            model = json.loads((output / "models.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(model["id"], model_id)
            self.assertEqual(model["code"], code)
            self.assertEqual(model["slug"], "saga-heroi-publicado")
            self.assertEqual(model["displayName"], "Heroi Publicado")
            self.assertEqual(model["variantName"], "Classico")
            self.assertEqual(model["folderPath"], ["Colecao"])
            self.assertEqual(summary["productionContractModels"], 1)
            self.assertEqual(summary["productionContractPublishedModels"], 1)


if __name__ == "__main__":
    unittest.main()
