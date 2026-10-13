from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

MODULE_PATH = TOOLS_DIR / "verify_r2_recovery.py"
SPEC = importlib.util.spec_from_file_location("verify_r2_recovery", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

RecoveryVerificationError = module.RecoveryVerificationError
validate_manifest = module.validate_manifest
verify_materialization = module.verify_materialization


class VerifyR2RecoveryTests(unittest.TestCase):
    @staticmethod
    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def manifest(self, files: list[tuple[str, str, bytes]]) -> dict:
        return {
            "version": 1,
            "bucket": "catalog-media",
            "provenance": "RECOVERED_FROM_R2_DETAIL",
            "entries": [{
                "modelId": model_id,
                "r2Key": f"media/{model_id}/{name}/detail.webp",
                "expectedSize": len(payload),
                "sha256": self.digest(payload),
            } for model_id, name, payload in files],
        }

    @staticmethod
    def mapping(files: list[tuple[str, str, bytes]]) -> dict:
        return {
            "version": 1,
            "entries": [{
                "modelId": model_id,
                "relativePath": f"Games/Franchise/{model_id}/{name}.webp",
            } for model_id, name, _ in files],
        }

    def materialize(self, root: Path, files: list[tuple[str, str, bytes]]) -> None:
        for model_id, name, payload in files:
            path = root / "Games" / "Franchise" / model_id / f"{name}.webp"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    def test_exact_coverage_size_hash_and_provenance_are_accepted(self) -> None:
        files = [
            ("mdl_a", "ada", b"first-recovered-detail"),
            ("mdl_b", "cammy", b"second-recovered-detail"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.materialize(root, files)
            rows, summary = verify_materialization(
                self.manifest(files), self.mapping(files), root=root
            )
        self.assertEqual(len(rows), 2)
        self.assertTrue(summary["completeCoverage"])
        self.assertEqual(summary["verified"], 2)
        self.assertEqual(summary["networkReads"], 0)
        self.assertEqual(summary["writesToSourceRoot"], 0)
        self.assertEqual({row["provenance"] for row in rows}, {"RECOVERED_FROM_R2_DETAIL"})
        self.assertEqual({row["sourceBucket"] for row in rows}, {"catalog-media"})

    def test_missing_or_extra_mapping_is_rejected(self) -> None:
        files = [("mdl_a", "ada", b"first"), ("mdl_b", "cammy", b"second")]
        mapping = self.mapping(files)
        mapping["entries"] = [mapping["entries"][0], {"modelId": "mdl_extra", "relativePath": "x.webp"}]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RecoveryVerificationError, "cobertura do mapping divergente"):
                verify_materialization(self.manifest(files), mapping, root=Path(tmp))

    def test_path_traversal_and_absolute_windows_path_are_rejected(self) -> None:
        files = [("mdl_a", "ada", b"first")]
        for unsafe in ("../escape.webp", "C:\\Catalogo\\escape.webp"):
            mapping = self.mapping(files)
            mapping["entries"][0]["relativePath"] = unsafe
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(RecoveryVerificationError):
                    verify_materialization(self.manifest(files), mapping, root=Path(tmp))

    def test_recovered_detail_must_keep_truthful_webp_extension(self) -> None:
        files = [("mdl_a", "ada", b"first")]
        for wrong in ("Games/Saga/ada.jpg", "Games/Saga/ada.png", "Games/Saga/ada.jpeg"):
            mapping = self.mapping(files)
            mapping["entries"][0]["relativePath"] = wrong
            with self.subTest(wrong=wrong), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(RecoveryVerificationError, "formato WebP real"):
                    verify_materialization(self.manifest(files), mapping, root=Path(tmp))

    def test_size_or_sha_mismatch_is_rejected(self) -> None:
        files = [("mdl_a", "ada", b"expected")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.materialize(root, [("mdl_a", "ada", b"tampered")])
            with self.assertRaisesRegex(RecoveryVerificationError, "tamanho divergente|SHA-256 divergente"):
                verify_materialization(self.manifest(files), self.mapping(files), root=root)

    def test_manifest_requires_detail_webp_and_exact_provenance(self) -> None:
        files = [("mdl_a", "ada", b"expected")]
        payload = self.manifest(files)
        payload["entries"][0]["r2Key"] = "media/mdl_a/img/card.webp"
        with self.assertRaisesRegex(RecoveryVerificationError, "detail.webp"):
            validate_manifest(payload)
        payload = self.manifest(files)
        payload["provenance"] = "RECOVERED"
        with self.assertRaisesRegex(RecoveryVerificationError, "provenance inválida"):
            validate_manifest(payload)

    def test_repository_manifest_is_machine_valid(self) -> None:
        path = TOOLS_DIR.parent / "config" / "catalog-r2-recovery-manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        bucket, entries = validate_manifest(payload)
        self.assertEqual(bucket, "tonecos-catalogo-media")
        self.assertEqual(len(entries), 3)
        self.assertEqual(
            {entry["modelId"] for entry in entries},
            {"mdl_4178950210474bea1aa9", "mdl_dc75a8c0af5b0d852743", "mdl_ebb783318941d6abc94e"},
        )


if __name__ == "__main__":
    unittest.main()
