from __future__ import annotations

import csv
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

MODULE_PATH = TOOLS_DIR / "apply_r2_recovery_registry.py"
SPEC = importlib.util.spec_from_file_location("apply_r2_recovery_registry", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

RecoveryRegistryError = module.RecoveryRegistryError
apply_registry_recovery = module.apply_registry_recovery


class RecoveryRegistryInsertTests(unittest.TestCase):
    @staticmethod
    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def test_missing_historical_row_requires_explicit_audit_row_and_inserts_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"recovered-detail"
            recovered = self.digest(payload)
            historical = self.digest(b"historical-original")
            relative = "Games/Saga/Hero/recovered.webp"
            target = root / Path(relative)
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)

            manifest = {
                "version": 1,
                "bucket": "catalog-media",
                "provenance": "RECOVERED_FROM_R2_DETAIL",
                "entries": [{
                    "modelId": "mdl_a",
                    "r2Key": "media/mdl_a/img/detail.webp",
                    "expectedSize": len(payload),
                    "sha256": recovered,
                }],
            }
            provenance = root / "provenance.jsonl"
            provenance.write_text(json.dumps({
                "version": 1,
                "modelId": "mdl_a",
                "relativePath": relative,
                "size": len(payload),
                "sha256": recovered,
                "sourceBucket": "catalog-media",
                "sourceR2Key": "media/mdl_a/img/detail.webp",
                "provenance": "RECOVERED_FROM_R2_DETAIL",
            }) + "\n", encoding="utf-8")

            registry = root / "audit.csv"
            with registry.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["codigo", "sha256", "caminho", "status", "modelo_publico", "identificacao"])
                writer.writeheader()
                writer.writerow({
                    "codigo": "AUD-OTHER",
                    "sha256": self.digest(b"other"),
                    "caminho": "Games/Saga/Other/other.webp",
                    "status": "OK_VISUAL|RV1",
                    "modelo_publico": "other-model",
                    "identificacao": "Other",
                })

            base_mapping = {
                "version": 1,
                "entries": [{"modelId": "mdl_a", "relativePath": relative}],
            }
            with self.assertRaisesRegex(RecoveryRegistryError, "não fornece auditRow explícito"):
                apply_registry_recovery(
                    registry=registry,
                    manifest_payload=manifest,
                    provenance_path=provenance,
                    root=root,
                    apply=False,
                    mapping_payload=base_mapping,
                )

            mapping = {
                "version": 1,
                "entries": [{
                    "modelId": "mdl_a",
                    "relativePath": relative,
                    "auditRow": {
                        "historicalSha256": historical,
                        "codigo": "AUD-RECOVERED",
                        "identificacao": "Hero NSFW",
                        "modelo_publico": "hero-nsfw",
                        "status": "OK_VISUAL|RECOVERED_R2",
                    },
                }],
            }
            dry = apply_registry_recovery(
                registry=registry,
                manifest_payload=manifest,
                provenance_path=provenance,
                root=root,
                apply=False,
                mapping_payload=mapping,
            )
            self.assertEqual(dry["pendingInserts"], 1)
            self.assertEqual(dry["pendingExistingRowUpdates"], 0)

            applied = apply_registry_recovery(
                registry=registry,
                manifest_payload=manifest,
                provenance_path=provenance,
                root=root,
                apply=True,
                mapping_payload=mapping,
            )
            self.assertTrue(applied["registryWritten"])
            self.assertEqual(applied["pendingUpdates"], 0)
            self.assertEqual(applied["alreadyApplied"], 1)

            with registry.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            recovered_rows = [row for row in rows if row.get("recovery_model_id") == "mdl_a"]
            self.assertEqual(len(recovered_rows), 1)
            row = recovered_rows[0]
            self.assertEqual(row["sha256"], recovered)
            self.assertEqual(row["historical_sha256"], historical)
            self.assertEqual(row["codigo"], "AUD-RECOVERED")
            self.assertEqual(row["modelo_publico"], "hero-nsfw")
            self.assertEqual(row["provenance"], "RECOVERED_FROM_R2_DETAIL")

    def test_mapping_audit_row_must_not_infer_publishable_identity(self) -> None:
        payload = {
            "version": 1,
            "entries": [{
                "modelId": "mdl_a",
                "relativePath": "Games/Saga/Hero/recovered.webp",
                "auditRow": {
                    "historicalSha256": "a" * 64,
                    "codigo": "",
                    "identificacao": "Hero",
                    "modelo_publico": "",
                    "status": "OK_VISUAL|RECOVERED_R2",
                },
            }],
        }
        with self.assertRaises(RecoveryRegistryError):
            module.load_recovery_mapping(payload, expected_models={"mdl_a"})


if __name__ == "__main__":
    unittest.main()
