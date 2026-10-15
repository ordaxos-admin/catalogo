from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from materialize_external_media_batch import validate_descriptor, validate_evidence


ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "config" / "external-batches" / "external-source-001-batch-005.json"
EVIDENCE = ROOT / "docs" / "evidence" / "external-source-001-batch-005-media.jsonl"


class SoraBatch005ContractTests(unittest.TestCase):
    def load(self):
        descriptor = json.loads(BATCH.read_text(encoding="utf-8"))
        evidence = [json.loads(line) for line in EVIDENCE.read_text(encoding="utf-8").splitlines() if line.strip()]
        return descriptor, evidence

    def test_identity_contract_is_deterministic(self):
        descriptor, _ = self.load()
        product = descriptor["products"][0]
        identity = product["expectedIdentityKey"]
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.assertEqual(identity, "Games / Kingdom Hearts / Sora / sora")
        self.assertEqual(product["expectedModelId"], "mdl_" + digest[:20])
        self.assertEqual(product["expectedCode"], "TS-" + digest[:12].upper())
        self.assertEqual(product["expectedSlug"], "kingdom-hearts-sora")
        self.assertEqual(product["sourceFolders"], ["Games", "Kingdom Hearts", "Sora"])

    def test_batch_remains_fail_closed_until_missing_image_is_staged(self):
        descriptor, evidence = self.load()
        products = validate_descriptor(descriptor, BATCH)
        missing = [row for row in evidence if row.get("stagingVerified") is not True]
        self.assertEqual([row["imageId"] for row in missing], ["15604"])
        self.assertEqual(missing[0]["sha256"], "c42d6a378c794a4ff6752a4211900b879c4bb79d6f2d0ecd5cf3eec26d9276bc")
        with self.assertRaisesRegex(RuntimeError, "sem stagingVerified"):
            validate_evidence(evidence, descriptor, products)

    def test_all_five_images_are_creator_attribution_reviewed(self):
        descriptor, evidence = self.load()
        self.assertEqual(len(evidence), descriptor["expectedImages"])
        self.assertEqual({row["review"] for row in evidence}, {"creator_attribution"})
        self.assertEqual([row["position"] for row in evidence], [1, 2, 3, 4, 5])
        self.assertEqual(len({row["sha256"] for row in evidence}), 5)


if __name__ == "__main__":
    unittest.main()
