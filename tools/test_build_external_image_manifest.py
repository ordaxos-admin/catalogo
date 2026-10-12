from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

MODULE_PATH = TOOLS_DIR / "build_external_image_manifest.py"
SPEC = importlib.util.spec_from_file_location("build_external_image_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

ExternalManifestError = module.ExternalManifestError
build_manifest = module.build_manifest


class BuildExternalImageManifestTests(unittest.TestCase):
    SOURCE_ID = "vendor-a"

    def product(self) -> dict:
        return {
            "sourceId": self.SOURCE_ID,
            "sourceProductKey": "vendor-a:juri-han",
            "incomingModel": "vendor-a / juri-han",
            "sourceUrl": "https://vendor.example/product/juri-han/",
            "displayName": "Juri Han",
            "sourceTitle": "Juri Han STL – Street Fighter",
            "canonicalCategoryCandidate": "Games",
            "franchiseCandidate": "Street Fighter",
            "readyForMaterialization": True,
        }

    @staticmethod
    def plan(position: int, filename: str) -> dict:
        return {
            "sourceProductKey": "vendor-a:juri-han",
            "position": position,
            "sourceImageId": str(100 + position),
            "sourceUrl": f"https://cdn.vendor.example/uploads/{filename}",
            "targetPath": f"vendor-a/juri-han/{filename}",
        }

    def test_multiple_images_share_one_incoming_model_and_keep_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_dir = root / self.SOURCE_ID / "juri-han"
            target_dir.mkdir(parents=True)
            Image.new("RGB", (100, 80), (255, 0, 0)).save(target_dir / "001.webp", "WEBP")
            Image.new("RGB", (120, 90), (0, 0, 255)).save(target_dir / "002.webp", "WEBP")

            rows, summary = build_manifest(
                [self.product()],
                [self.plan(1, "001.webp"), self.plan(2, "002.webp")],
                images_root=root,
            )

            self.assertEqual(len(rows), 2)
            self.assertEqual({row["public_model_key"] for row in rows}, {"vendor-a / juri-han"})
            self.assertEqual({row["category"] for row in rows}, {"Games"})
            self.assertEqual({row["franchise"] for row in rows}, {"Street Fighter"})
            self.assertTrue(all(row["status"] == "OK" for row in rows))
            self.assertTrue(all(len(row["sha256"]) == 64 for row in rows))
            self.assertEqual(rows[0]["source_provenance"]["kind"], "AUTHORIZED_EXTERNAL_MEDIA")
            self.assertEqual(summary["products"], 1)
            self.assertEqual(summary["images"], 2)
            self.assertEqual(summary["networkDownloads"], 0)

    def test_unreviewed_product_is_rejected_fail_closed(self) -> None:
        product = self.product()
        product["readyForMaterialization"] = False
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "ainda exige revisão"):
                build_manifest([product], [], images_root=Path(tmp))

    def test_missing_materialized_image_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "imagem materializada ausente"):
                build_manifest(
                    [self.product()],
                    [self.plan(1, "001.webp")],
                    images_root=Path(tmp),
                )

    def test_path_traversal_is_rejected(self) -> None:
        bad_plan = self.plan(1, "001.webp")
        bad_plan["targetPath"] = "../escape.webp"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "targetPath inseguro"):
                build_manifest([self.product()], [bad_plan], images_root=Path(tmp))


if __name__ == "__main__":
    unittest.main()
