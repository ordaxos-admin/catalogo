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


class ApplyR2RecoveryRegistryTests(unittest.TestCase):
    @staticmethod
    def digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def fixture(self, root: Path):
        model_id = "mdl_a"
        payload = b"recovered-detail"
        historical = self.digest(b"historical-original")
        relative = "Games/Saga/Hero/model.webp"
        target = root / "Games" / "Saga" / "Hero" / "model.webp"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

        manifest = {
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
            ],
        }
        provenance = root / "provenance.jsonl"
        provenance.write_text(
            json.dumps(
                {
                    "version": 1,
                    "modelId": model_id,
                    "relativePath": relative,
                    "size": len(payload),
                    "sha256": self.digest(payload),
                    "sourceBucket": "catalog-media",
                    "sourceR2Key": f"media/{model_id}/img/detail.webp",
                    "provenance": "RECOVERED_FROM_R2_DETAIL",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        registry = root / "audit.csv"
        with registry.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["codigo", "sha256", "caminho", "status", "modelo_publico", "identificacao"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "codigo": "AUD-1",
                    "sha256": historical,
                    "caminho": relative.replace("/", "\\"),
                    "status": "OK_VISUAL|RV1",
                    "modelo_publico": "hero-model",
                    "identificacao": "Hero",
                }
            )
        return manifest, provenance, registry, payload, historical, relative

    def read_registry_row(self, registry: Path) -> dict[str, str]:
        with registry.open("r", encoding="utf-8-sig", newline="") as handle:
            return next(csv.DictReader(handle))

    def test_dry_run_preserves_registry_and_reports_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, provenance, registry, _payload, _historical, _ = self.fixture(root)
            before = registry.read_bytes()
            result = apply_registry_recovery(
                registry=registry,
                manifest_payload=manifest,
                provenance_path=provenance,
                root=root,
                apply=False,
            )
            self.assertEqual(before, registry.read_bytes())
            self.assertFalse(result["applied"])
            self.assertFalse(result["registryWritten"])
            self.assertEqual(result["pendingUpdates"], 1)
            self.assertEqual(result["pendingExistingRowUpdates"], 1)
            self.assertEqual(result["pendingInserts"], 0)
            self.assertEqual(result["alreadyApplied"], 0)

    def test_apply_preserves_historical_sha_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, provenance, registry, payload, historical, _ = self.fixture(root)
            first = apply_registry_recovery(
                registry=registry,
                manifest_payload=manifest,
                provenance_path=provenance,
                root=root,
                apply=True,
            )
            self.assertTrue(first["applied"])
            self.assertTrue(first["registryWritten"])
            self.assertEqual(first["pendingUpdates"], 0)
            self.assertEqual(first["alreadyApplied"], 1)
            row = self.read_registry_row(registry)
            self.assertEqual(row["sha256"], self.digest(payload))
            self.assertEqual(row["historical_sha256"], historical)
            self.assertEqual(row["provenance"], "RECOVERED_FROM_R2_DETAIL")
            self.assertEqual(row["source_bucket"], "catalog-media")
            self.assertEqual(row["source_r2_key"], "media/mdl_a/img/detail.webp")
            self.assertEqual(row["recovery_model_id"], "mdl_a")
            self.assertEqual(row["recovery_size"], str(len(payload)))

            before_second = registry.read_bytes()
            second = apply_registry_recovery(
                registry=registry,
                manifest_payload=manifest,
                provenance_path=provenance,
                root=root,
                apply=True,
            )
            self.assertEqual(before_second, registry.read_bytes())
            self.assertTrue(second["applied"])
            self.assertFalse(second["registryWritten"])
            self.assertEqual(second["pendingUpdates"], 0)
            self.assertEqual(second["alreadyApplied"], 1)

    def test_physical_or_provenance_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, provenance, registry, _payload, _historical, relative = self.fixture(root)
            (root / Path(relative)).write_bytes(b"tampered")
            with self.assertRaisesRegex(RecoveryRegistryError, "arquivo físico divergiu"):
                apply_registry_recovery(
                    registry=registry,
                    manifest_payload=manifest,
                    provenance_path=provenance,
                    root=root,
                    apply=False,
                )

    def test_missing_registry_path_without_explicit_audit_row_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, provenance, registry, _payload, _historical, relative = self.fixture(root)
            rows = []
            with registry.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fields = list(reader.fieldnames or [])
                rows = list(reader)
            rows[0]["caminho"] = "Games/Saga/Hero/other.webp"
            with registry.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            mapping = {
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
                    mapping_payload=mapping,
                )

    def test_missing_registry_path_can_be_inserted_only_from_explicit_audited_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, provenance, registry, payload, historical, relative = self.fixture(root)
            with registry.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["codigo", "sha256", "caminho", "status", "modelo_publico", "identificacao"],
                )
                writer.writeheader()
                writer.writerow({
                    "codigo": "AUD-OTHER",
                    "sha256": self.digest(b"other"),
                    "caminho": "Games/Saga/Other/other.webp",
                    "status": "OK_VISUAL|RV1",
                    "modelo_publico": "other-model",
                    "identificacao": "Other",
                })
            mapping = {
                "version": 1,
                "entries": [{
                    "modelId": "mdl_a",
                    "relativePath": relative,
                    "auditRow": {
                        "historicalSha256": historical,
                        "codigo": "AUD-RECOVERED",
                        "identificacao": "Hero",
                        "modelo_publico": "hero-model",
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
            self.assertEqual(dry["pendingUpdates"], 1)
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
            self.assertEqual(row["sha256"], self.digest(payload))
            self.assertEqual(row["historical_sha256"], historical)
            self.assertEqual(row["codigo"], "AUD-RECOVERED")
            self.assertEqual(row["modelo_publico"], "hero-model")
            self.assertEqual(row["provenance"], "RECOVERED_FROM_R2_DETAIL")

    def test_recovered_sha_without_historical_lineage_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, provenance, registry, payload, _historical, _relative = self.fixture(root)
            row = self.read_registry_row(registry)
            fields = list(row)
            row["sha256"] = self.digest(payload)
            with registry.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaisesRegex(RecoveryRegistryError, "lineage histórica válida"):
                apply_registry_recovery(
                    registry=registry,
                    manifest_payload=manifest,
                    provenance_path=provenance,
                    root=root,
                    apply=False,
                )


if __name__ == "__main__":
    unittest.main()
