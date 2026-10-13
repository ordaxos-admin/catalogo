from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "catalog-legacy-gallery-overrides.json"


class LegacyGalleryRegistryIdentityTests(unittest.TestCase):
    def test_kari_tailmon_preserves_published_identity(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        groups = payload["groups"]
        group = next(
            item
            for item in groups
            if item["family"] == "digimon-kari-e-tailmon-modelo"
        )

        self.assertEqual(group["categorySlug"], "animes-desenhos")
        self.assertEqual(group["franchiseSlug"], "digimon")
        self.assertEqual(group["folderPathKey"], "kari-e-tailmon")
        self.assertEqual(group["canonicalSlug"], "digimon-kari-e-tailmon")
        self.assertEqual(
            group["memberSlugs"],
            ["digimon-kari-e-tailmon", "digimon-kari-e-tailmon-02"],
        )
        self.assertEqual(group["matchMode"], "explicit-members")

    def test_registry_audited_totals_still_match_groups(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        groups = payload["groups"]
        member_cards = sum(len(group["memberSlugs"]) for group in groups)

        self.assertEqual(payload["auditedGroups"], len(groups))
        self.assertEqual(payload["auditedMemberCards"], member_cards)
        self.assertEqual(payload["auditedExtraCards"], member_cards - len(groups))


if __name__ == "__main__":
    unittest.main()
