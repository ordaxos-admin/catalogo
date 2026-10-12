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

MODULE_PATH = TOOLS_DIR / "materialize_r2_recovery.py"
SPEC = importlib.util.spec_from_file_location("materialize_r2_recovery", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

RecoveryMaterializationError = module.RecoveryMaterializationError
materialize = module.materialize


class MaterializeR2RecoveryTests(unittest.TestCase):
    @staticmethod
    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def manifest(self, files: list[tuple[str, bytes]]) -> dict:
        return {
            "version": 1,
            "bucket": "catalog-media",
            "provenance": "RECOVERED_FROM_R2_DETAIL",
            "entries": [
                {
                    "modelId": model_id,
                    "r2Key": f"media/{model_id}/img/detail.webp",
                    "expectedSize": len(payload),
                    "sha256": self.digest(payload),
                }
                for model_id, payload in files
            ],
        }

    def mapping(self, files: list[tuple[str, bytes]]) -> dict:
        return {
            "version": 1,
            "entries": [
                {
                    "modelId": model_id,
                    "relativePath": f"Games/Franchise/{model_id}/recovered.webp",
                }
                for model_id, _ in files
            ],
        }

    def write_incoming(self, incoming: Path, files: list[tuple[str, bytes]]) -> None:
        incoming.mkdir(parents=True, exist_ok=True)
        for model_id, payload in files:
            (incoming / f"{model_id}.webp").write_bytes(payload)

    def target_for(self, root: Path, model_id: str) -> Path:
        return root / "Games" / "Franchise" / model_id / "recovered.webp"

    def test_dry_run_verifies_everything_without_writing_target(self) -> None:
        files = [("mdl_a", b"ada"), ("mdl_b", b"cammy")]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            incoming = base / "incoming"
            root = base / "catalog"
            root.mkdir()
            self.write_incoming(incoming, files)
            summary = materialize(
                self.manifest(files),
                self.mapping(files),
                incoming_root=incoming,
                target_root=root,
                apply=False,
            )
            self.assertFalse(summary["applied"])
            self.assertEqual(summary["pendingCopies"], 2)
            self.assertEqual(summary["writesToTargetRoot"], 0)
            self.assertFalse(self.target_for(root, "mdl_a").exists())
            self.assertFalse(self.target_for(root, "mdl_b").exists())

    def test_apply_materializes_all_and_is_idempotent(self) -> None:
        files = [("mdl_a", b"ada"), ("mdl_b", b"cammy")]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            incoming = base / "incoming"
            root = base / "catalog"
            root.mkdir()
            provenance = base / "recovery-provenance.jsonl"
            self.write_incoming(incoming, files)

            first = materialize(
                self.manifest(files),
                self.mapping(files),
                incoming_root=incoming,
                target_root=root,
                apply=True,
                provenance_output=provenance,
            )
            self.assertTrue(first["completeCoverage"])
            self.assertEqual(first["copied"], 2)
            self.assertEqual(self.target_for(root, "mdl_a").read_bytes(), b"ada")
            self.assertEqual(self.target_for(root, "mdl_b").read_bytes(), b"cammy")
            rows = [json.loads(line) for line in provenance.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                {row["provenance"] for row in rows},
                {"RECOVERED_FROM_R2_DETAIL"},
            )

            second = materialize(
                self.manifest(files),
                self.mapping(files),
                incoming_root=incoming,
                target_root=root,
                apply=True,
                provenance_output=provenance,
            )
            self.assertEqual(second["copied"], 0)
            self.assertEqual(second["alreadyVerified"], 2)
            self.assertTrue(second["completeCoverage"])

    def test_conflicting_existing_target_fails_closed(self) -> None:
        files = [("mdl_a", b"expected")]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            incoming = base / "incoming"
            root = base / "catalog"
            root.mkdir()
            self.write_incoming(incoming, files)
            target = self.target_for(root, "mdl_a")
            target.parent.mkdir(parents=True)
            target.write_bytes(b"different")

            with self.assertRaisesRegex(RecoveryMaterializationError, "destino conflitante"):
                materialize(
                    self.manifest(files),
                    self.mapping(files),
                    incoming_root=incoming,
                    target_root=root,
                    apply=True,
                )
            self.assertEqual(target.read_bytes(), b"different")

    def test_bad_incoming_bytes_block_before_any_target_write(self) -> None:
        files = [("mdl_a", b"expected-a"), ("mdl_b", b"expected-b")]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            incoming = base / "incoming"
            root = base / "catalog"
            root.mkdir()
            self.write_incoming(incoming, files)
            (incoming / "mdl_b.webp").write_bytes(b"tampered")

            with self.assertRaisesRegex(RecoveryMaterializationError, "entrada divergente"):
                materialize(
                    self.manifest(files),
                    self.mapping(files),
                    incoming_root=incoming,
                    target_root=root,
                    apply=True,
                )
            self.assertFalse(self.target_for(root, "mdl_a").exists())
            self.assertFalse(self.target_for(root, "mdl_b").exists())

    def test_missing_incoming_file_blocks_before_any_target_write(self) -> None:
        files = [("mdl_a", b"expected-a"), ("mdl_b", b"expected-b")]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            incoming = base / "incoming"
            root = base / "catalog"
            root.mkdir()
            self.write_incoming(incoming, [files[0]])

            with self.assertRaisesRegex(RecoveryMaterializationError, "arquivo de entrada ausente"):
                materialize(
                    self.manifest(files),
                    self.mapping(files),
                    incoming_root=incoming,
                    target_root=root,
                    apply=True,
                )
            self.assertFalse(self.target_for(root, "mdl_a").exists())
            self.assertFalse(self.target_for(root, "mdl_b").exists())


if __name__ == "__main__":
    unittest.main()
