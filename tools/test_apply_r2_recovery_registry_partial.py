from __future__ import annotations

import hashlib
import importlib.util
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
plan_registry_patch = module.plan_registry_patch


class ApplyR2RecoveryRegistryPartialStateTests(unittest.TestCase):
    def test_recovered_sha_with_incomplete_provenance_is_rejected(self) -> None:
        recovered = hashlib.sha256(b"recovered").hexdigest()
        historical = hashlib.sha256(b"historical").hexdigest()
        rows = [
            {
                "caminho": "Games/Saga/Hero/model.webp",
                "sha256": recovered,
                "status": "OK_VISUAL|RV1",
                "historical_sha256": historical,
                "provenance": "RECOVERED_FROM_R2_DETAIL",
                "source_bucket": "catalog-media",
                "source_r2_key": "media/mdl_a/img/detail.webp",
                "recovery_model_id": "mdl_a",
                "recovery_size": "",
            }
        ]
        provenance_rows = [
            {
                "modelId": "mdl_a",
                "relativePath": "Games/Saga/Hero/model.webp",
                "sha256": recovered,
                "size": 123,
                "sourceBucket": "catalog-media",
                "sourceR2Key": "media/mdl_a/img/detail.webp",
                "provenance": "RECOVERED_FROM_R2_DETAIL",
            }
        ]
        with self.assertRaisesRegex(RecoveryRegistryError, "provenance incompleta"):
            plan_registry_patch(list(rows[0]), rows, provenance_rows)


if __name__ == "__main__":
    unittest.main()
