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
    PRODUCT_ID = "42"
    PRODUCT_KEY = "vendor-a:product:42"
    INCOMING_MODEL = "vendor-a / product:42"

    def product(self, image_count: int = 2) -> dict:
        return {
            "version": 1,
            "sourceId": self.SOURCE_ID,
            "sourceProductId": self.PRODUCT_ID,
            "sourceProductKey": self.PRODUCT_KEY,
            "incomingModel": self.INCOMING_MODEL,
            "sourceUrl": "https://vendor.example/product/juri-han/",
            "displayName": "Juri Han",
            "sourceTitle": "Juri Han STL – Street Fighter",
            "canonicalCategoryCandidate": "Games",
            "franchiseCandidate": "Street Fighter",
            "imageCount": image_count,
            "readyForMaterialization": True,
        }

    @classmethod
    def plan(cls, position: int, source_filename: str) -> dict:
        image_id = str(100 + position)
        return {
            "version": 1,
            "sourceId": cls.SOURCE_ID,
            "sourceProductKey": cls.PRODUCT_KEY,
            "incomingModel": cls.INCOMING_MODEL,
            "position": position,
            "sourceImageId": image_id,
            "sourceImageIdentityKind": "provider_id",
            "sourceUrl": f"https://cdn.vendor.example/uploads/{source_filename}",
            "targetPath": f"vendor-a/product-42/{image_id}.webp",
        }

    def test_multiple_images_share_one_incoming_model_and_keep_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_dir = root / self.SOURCE_ID / "product-42"
            target_dir.mkdir(parents=True)
            Image.new("RGB", (100, 80), (255, 0, 0)).save(target_dir / "101.webp", "WEBP")
            Image.new("RGB", (120, 90), (0, 0, 255)).save(target_dir / "102.webp", "WEBP")

            rows, summary = build_manifest(
                [self.product()],
                [self.plan(1, "001.webp"), self.plan(2, "002.webp")],
                images_root=root,
            )

            self.assertEqual(len(rows), 2)
            self.assertEqual({row["public_model_key"] for row in rows}, {self.INCOMING_MODEL})
            self.assertEqual({row["category"] for row in rows}, {"Games"})
            self.assertEqual({row["franchise"] for row in rows}, {"Street Fighter"})
            self.assertTrue(all(row["status"] == "OK" for row in rows))
            self.assertTrue(all(len(row["sha256"]) == 64 for row in rows))
            self.assertEqual(rows[0]["source_provenance"]["kind"], "AUTHORIZED_EXTERNAL_MEDIA")
            self.assertEqual(rows[0]["source_provenance"]["sourceProductKey"], self.PRODUCT_KEY)
            self.assertEqual(summary["products"], 1)
            self.assertEqual(summary["images"], 2)
            self.assertEqual(summary["networkDownloads"], 0)
            self.assertTrue(summary["completeGalleryCoverage"])
            self.assertEqual(summary["watermarkStatus"], {"clear": 0, "watermarked": 0, "unknown": 2})
            self.assertFalse(summary["watermarkAuditComplete"])
            self.assertTrue(all(row["watermark_status"] == "unknown" for row in rows))

    def test_legacy_or_tampered_product_identity_is_rejected(self) -> None:
        product = self.product(image_count=1)
        product["sourceProductKey"] = "vendor-a:juri-han"
        product["incomingModel"] = "vendor-a / juri-han"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "contrato de identidade do produto"):
                build_manifest([product], [self.plan(1, "001.webp")], images_root=Path(tmp))

    def test_plan_must_belong_to_exact_product_identity(self) -> None:
        bad_plan = self.plan(1, "001.webp")
        bad_plan["sourceId"] = "vendor-b"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "não pertence à identidade externa"):
                build_manifest(
                    [self.product(image_count=1)],
                    [bad_plan],
                    images_root=Path(tmp),
                )

    def test_provider_image_id_must_remain_canonical_in_plan(self) -> None:
        bad_plan = self.plan(1, "001.webp")
        bad_plan["sourceImageId"] = "0101"
        bad_plan["targetPath"] = "vendor-a/product-42/0101.webp"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "provider_id não canônico"):
                build_manifest(
                    [self.product(image_count=1)],
                    [bad_plan],
                    images_root=Path(tmp),
                )

    def test_url_hash_identity_must_match_source_url(self) -> None:
        bad_plan = self.plan(1, "001.webp")
        bad_plan["sourceImageIdentityKind"] = "url_sha256"
        bad_plan["sourceImageId"] = "url-sha256-" + "0" * 64
        bad_plan["targetPath"] = f"vendor-a/product-42/{bad_plan['sourceImageId']}.webp"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "não corresponde à sourceUrl"):
                build_manifest(
                    [self.product(image_count=1)],
                    [bad_plan],
                    images_root=Path(tmp),
                )

    def test_target_path_must_match_deterministic_identity(self) -> None:
        bad_plan = self.plan(1, "001.webp")
        bad_plan["targetPath"] = "vendor-a/product-42/001.webp"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "targetPath diverge"):
                build_manifest(
                    [self.product(image_count=1)],
                    [bad_plan],
                    images_root=Path(tmp),
                )

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
                    [self.product(image_count=1)],
                    [self.plan(1, "001.webp")],
                    images_root=Path(tmp),
                )

    def test_path_traversal_is_rejected(self) -> None:
        bad_plan = self.plan(1, "001.webp")
        bad_plan["targetPath"] = "../escape.webp"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "targetPath"):
                build_manifest(
                    [self.product(image_count=1)],
                    [bad_plan],
                    images_root=Path(tmp),
                )

    def test_partial_gallery_plan_is_rejected_before_file_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "galeria parcial"):
                build_manifest(
                    [self.product(image_count=7)],
                    [self.plan(position, f"{position:03d}.webp") for position in range(1, 6)],
                    images_root=Path(tmp),
                )

    def test_duplicate_or_gapped_positions_are_rejected(self) -> None:
        plan = [
            self.plan(1, "001.webp"),
            self.plan(1, "002.webp"),
            self.plan(3, "003.webp"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "posições incompletas/duplicadas"):
                build_manifest(
                    [self.product(image_count=3)],
                    plan,
                    images_root=Path(tmp),
                )

    def test_ready_product_requires_positive_image_count(self) -> None:
        product = self.product()
        product["imageCount"] = 0
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ExternalManifestError, "imageCount positivo"):
                build_manifest([product], [], images_root=Path(tmp))

    def test_media_audit_binds_watermark_status_to_exact_materialized_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_dir = root / self.SOURCE_ID / "product-42"
            target_dir.mkdir(parents=True)
            image_path = target_dir / "101.webp"
            Image.new("RGB", (160, 120), (20, 40, 60)).save(image_path, "WEBP")
            digest = module.sha256_file(image_path)
            audit = [{
                "version": 1,
                "sourceProductKey": self.PRODUCT_KEY,
                "sourceImageId": "101",
                "sha256": digest,
                "watermarkStatus": "clear",
                "reviewMethod": "manual_visual",
            }]

            rows, summary = build_manifest(
                [self.product(image_count=1)],
                [self.plan(1, "001.webp")],
                images_root=root,
                media_audit=audit,
            )

            self.assertEqual(rows[0]["watermark_status"], "clear")
            self.assertEqual(rows[0]["watermark_audit"]["sha256"], digest)
            self.assertEqual(summary["watermarkStatus"], {"clear": 1, "watermarked": 0, "unknown": 0})
            self.assertTrue(summary["watermarkAuditComplete"])

    def test_media_audit_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_dir = root / self.SOURCE_ID / "product-42"
            target_dir.mkdir(parents=True)
            Image.new("RGB", (160, 120), (20, 40, 60)).save(target_dir / "101.webp", "WEBP")
            audit = [{
                "version": 1,
                "sourceProductKey": self.PRODUCT_KEY,
                "sourceImageId": "101",
                "sha256": "0" * 64,
                "watermarkStatus": "clear",
            }]

            with self.assertRaisesRegex(ExternalManifestError, "não corresponde aos bytes"):
                build_manifest(
                    [self.product(image_count=1)],
                    [self.plan(1, "001.webp")],
                    images_root=root,
                    media_audit=audit,
                )

    def test_unused_media_audit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_dir = root / self.SOURCE_ID / "product-42"
            target_dir.mkdir(parents=True)
            Image.new("RGB", (160, 120), (20, 40, 60)).save(target_dir / "101.webp", "WEBP")
            audit = [{
                "version": 1,
                "sourceProductKey": self.PRODUCT_KEY,
                "sourceImageId": "999",
                "sha256": "1" * 64,
                "watermarkStatus": "watermarked",
            }]

            with self.assertRaisesRegex(ExternalManifestError, "sem mídia correspondente"):
                build_manifest(
                    [self.product(image_count=1)],
                    [self.plan(1, "001.webp")],
                    images_root=root,
                    media_audit=audit,
                )


if __name__ == "__main__":
    unittest.main()
