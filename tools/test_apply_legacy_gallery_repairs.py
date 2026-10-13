from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apply_legacy_gallery_repairs import (
    apply_statement_batches,
    count_integrity_statements,
    load_config,
    plan_repairs,
    published_model_count,
    verify_applied,
    verify_materialized_counts,
    verify_repair_image_sources,
)


def group() -> dict:
    return {
        "categorySlug": "animes-desenhos",
        "franchiseSlug": "dragon-ball",
        "folderPathKey": "androides/androide-18",
        "family": "dragon-ball-androide-18-traje-casual",
        "canonicalSlug": "dragon-ball-androide-18-traje-casual-frente",
        "memberSlugs": [
            "dragon-ball-androide-18-traje-casual-frente",
            "dragon-ball-androide-18-traje-casual-lateral",
            "dragon-ball-androide-18-traje-casual-costas",
        ],
        "reason": "teste",
    }


def row(slug: str, *, published: int = 1) -> dict:
    return {
        "id": f"mdl-{slug}",
        "slug": slug,
        "code": f"TS-{slug}",
        "name": "Androide 18",
        "published": published,
        "image_count": 1,
        "gallery_version": 1,
        "gallery_manifest_key": f"gallery/mdl-{slug}/manifest.json",
        "cover_storage_key": f"media/mdl-{slug}/card.webp",
        "category_slug": "animes-desenhos",
        "franchise_slug": "dragon-ball",
        "folder_path": "androides/androide-18",
    }


class LegacyGalleryRepairTests(unittest.TestCase):
    def test_config_contract_validates_audited_summary_and_match_mode(self) -> None:
        payload = {
            "version": 2,
            "auditedAt": "2026-10-07",
            "auditedGroups": 1,
            "auditedMemberCards": 3,
            "auditedExtraCards": 2,
            "groups": [group()],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repairs.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_config(path)
            self.assertEqual(len(loaded), 1)

            bad_mode = json.loads(json.dumps(payload))
            bad_mode["groups"][0]["matchMode"] = "numeric-heuristic"
            path.write_text(json.dumps(bad_mode), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "matchMode"):
                load_config(path)

            bad_summary = json.loads(json.dumps(payload))
            bad_summary["auditedExtraCards"] = 1
            path.write_text(json.dumps(bad_summary), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "resumo auditado"):
                load_config(path)

    def test_explicit_member_group_is_valid_without_numeric_inference(self) -> None:
        explicit = group()
        explicit["family"] = "mortal-kombat-mileena-modelo-base"
        explicit["canonicalSlug"] = "mortal-kombat-mileena"
        explicit["memberSlugs"] = ["mortal-kombat-mileena", "mortal-kombat-mileena-04"]
        explicit["matchMode"] = "explicit-members"
        payload = {
            "version": 2,
            "auditedAt": "2026-10-07",
            "auditedGroups": 1,
            "auditedMemberCards": 2,
            "auditedExtraCards": 1,
            "groups": [explicit],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repairs.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_config(path)[0]["matchMode"], "explicit-members")

    def test_clean_state_plans_only_source_relations(self) -> None:
        config = [group()]
        rows = [row(slug) for slug in config[0]["memberSlugs"]]

        plan = plan_repairs(config, rows, [])

        self.assertTrue(plan["ready"])
        self.assertEqual(plan["groups"], 1)
        self.assertEqual(plan["memberCards"], 3)
        self.assertEqual(plan["expectedRelations"], 2)
        self.assertEqual(plan["pendingRelations"], 2)
        self.assertEqual(plan["sourcesToRetire"], 2)
        self.assertEqual(plan["destructiveDeletes"], 0)
        self.assertEqual(
            [statement["params"][0] for statement in plan["statements"]],
            [1, 2],
        )

    def test_cross_name_group_plans_explicit_approval_before_relation(self) -> None:
        config = [group()]
        canonical, side, back = config[0]["memberSlugs"]
        rows = [row(canonical), row(side), row(back)]
        rows[1]["name"] = "Androide 18 vista lateral"

        plan = plan_repairs(config, rows, [], [])

        self.assertEqual(plan["pendingApprovals"], 1)
        self.assertEqual(plan["pendingRelations"], 2)
        self.assertEqual(len(plan["statements"]), 3)
        self.assertIn("model_gallery_member_approvals", plan["statements"][0]["sql"])
        self.assertEqual(
            plan["statements"][0]["params"],
            [config[0]["family"], config[0]["reason"], side, canonical],
        )
        self.assertEqual(plan["statements"][1]["params"], [1, side, canonical])

    def test_cross_name_group_reuses_exact_existing_approval(self) -> None:
        config = [group()]
        canonical, side, back = config[0]["memberSlugs"]
        rows = [row(canonical), row(side), row(back)]
        rows[1]["name"] = "Androide 18 vista lateral"
        approvals = [{
            "canonical_slug": canonical,
            "source_slug": side,
            "registry_family": config[0]["family"],
            "reason": config[0]["reason"],
        }]

        plan = plan_repairs(config, rows, [], approvals)

        self.assertEqual(plan["existingApprovals"], 1)
        self.assertEqual(plan["pendingApprovals"], 0)
        self.assertEqual(len(plan["statements"]), 2)
        self.assertEqual(plan["statements"][0]["params"], [1, side, canonical])

    def test_cross_name_existing_relation_requires_matching_approval(self) -> None:
        config = [group()]
        canonical, side, back = config[0]["memberSlugs"]
        rows = [row(canonical), row(side, published=0), row(back)]
        rows[1]["name"] = "Androide 18 vista lateral"
        relations = [{
            "canonical_slug": canonical,
            "source_slug": side,
            "position": 1,
        }]

        with self.assertRaisesRegex(RuntimeError, "possui"):
            plan_repairs(config, rows, relations, [])

        bad = [{
            "canonical_slug": canonical,
            "source_slug": side,
            "registry_family": "outra-family",
            "reason": config[0]["reason"],
        }]
        with self.assertRaisesRegex(RuntimeError, "auditada"):
            plan_repairs(config, rows, relations, bad)

    def test_partial_progress_is_idempotent_and_only_plans_missing_relation(self) -> None:
        config = [group()]
        canonical, side, back = config[0]["memberSlugs"]
        rows = [row(canonical), row(side, published=0), row(back)]
        relations = [{
            "canonical_slug": canonical,
            "source_slug": side,
            "position": 1,
        }]

        plan = plan_repairs(config, rows, relations)

        self.assertEqual(plan["existingRelations"], 1)
        self.assertEqual(plan["pendingRelations"], 1)
        self.assertEqual(plan["sourcesToRetire"], 1)
        self.assertEqual(plan["statements"][0]["params"], [2, back, canonical])

    def test_retired_source_without_relation_fails_closed(self) -> None:
        config = [group()]
        canonical, side, back = config[0]["memberSlugs"]
        rows = [row(canonical), row(side, published=0), row(back)]

        with self.assertRaisesRegex(RuntimeError, "publicada sem"):
            plan_repairs(config, rows, [])

    def test_source_attached_to_wrong_canonical_fails_closed(self) -> None:
        config = [group()]
        canonical, side, back = config[0]["memberSlugs"]
        rows = [row(canonical), row(side, published=0), row(back)]
        relations = [{
            "canonical_slug": "outro-modelo",
            "source_slug": side,
            "position": 1,
        }]

        with self.assertRaisesRegex(RuntimeError, "outro"):
            plan_repairs(config, rows, relations)

    def test_scope_mismatch_fails_closed(self) -> None:
        config = [group()]
        rows = [row(slug) for slug in config[0]["memberSlugs"]]
        rows[-1]["folder_path"] = "androides/outra-pasta"

        with self.assertRaisesRegex(RuntimeError, "divergem de escopo"):
            plan_repairs(config, rows, [])

    def test_repair_sha_preflight_requires_full_unique_coverage(self) -> None:
        config = [group()]
        rows = [row(slug) for slug in config[0]["memberSlugs"]]
        source_rows = [
            {
                "slug": slug,
                "image_count": 1,
                "gallery_version": 1,
                "image_id": f"img-{index}",
                "source_sha256": str(index + 1) * 64,
            }
            for index, slug in enumerate(config[0]["memberSlugs"])
        ]

        result = verify_repair_image_sources(config, rows, source_rows)

        self.assertTrue(result["ready"])
        self.assertEqual(result["models"], 3)
        self.assertEqual(result["images"], 3)
        self.assertEqual(result["duplicateShaWithinRepairGroups"], 0)

    def test_repair_sha_preflight_rejects_missing_index_and_cross_source_duplicate(self) -> None:
        config = [group()]
        rows = [row(slug) for slug in config[0]["memberSlugs"]]
        complete = [
            {
                "slug": slug,
                "image_count": 1,
                "gallery_version": 1,
                "image_id": f"img-{index}",
                "source_sha256": str(index + 1) * 64,
            }
            for index, slug in enumerate(config[0]["memberSlugs"])
        ]

        with self.assertRaisesRegex(RuntimeError, "índice SHA incompleto"):
            verify_repair_image_sources(config, rows, complete[:-1])

        duplicate = [dict(item) for item in complete]
        duplicate[1]["source_sha256"] = duplicate[0]["source_sha256"]
        with self.assertRaisesRegex(RuntimeError, "imagem exata repetida entre fontes"):
            verify_repair_image_sources(config, rows, duplicate)

    def test_apply_batches_are_bounded_and_retriable(self) -> None:
        statements = [{"sql": "SELECT 1", "params": []} for _ in range(205)]

        with patch("apply_legacy_gallery_repairs.d1_request") as request:
            batches = apply_statement_batches(statements, batch_size=100)

        self.assertEqual(batches, 3)
        self.assertEqual([len(call.args[0]) for call in request.call_args_list], [100, 100, 5])

        with self.assertRaises(ValueError):
            apply_statement_batches([], batch_size=251)

    def test_public_model_count_reads_only_published_cards(self) -> None:
        response = [{"success": True, "results": [{"total": 2567}]}]
        with patch("apply_legacy_gallery_repairs.d1_request", return_value=response) as request:
            total = published_model_count()

        self.assertEqual(total, 2567)
        sql = request.call_args.args[0][0]["sql"]
        self.assertIn("WHERE published=1", sql)

    def test_materialized_count_verification_covers_all_public_levels(self) -> None:
        specs = count_integrity_statements()

        self.assertEqual(
            [item["name"] for item in specs],
            ["categories", "franchises", "folders_direct", "folders_subtree"],
        )
        self.assertIn("direct_model_count", specs[2]["sql"])
        self.assertIn("subtree_model_count", specs[3]["sql"])
        self.assertIn("WITH RECURSIVE tree", specs[3]["sql"])

        zero = [
            {"success": True, "results": [{"mismatches": 0}]}
            for _ in specs
        ]
        with patch("apply_legacy_gallery_repairs.d1_request", return_value=zero):
            result = verify_materialized_counts()
        self.assertTrue(result["ready"])
        self.assertEqual(result["mismatches"]["categories"], 0)

        bad = list(zero)
        bad[1] = {"success": True, "results": [{"mismatches": 2}]}
        with patch("apply_legacy_gallery_repairs.d1_request", return_value=bad):
            with self.assertRaisesRegex(RuntimeError, "franchises=2"):
                verify_materialized_counts()

    def test_completed_state_verifies_with_zero_pending_relations(self) -> None:
        config = [group()]
        canonical, side, back = config[0]["memberSlugs"]
        rows = [row(canonical), row(side, published=0), row(back, published=0)]
        relations = [
            {"canonical_slug": canonical, "source_slug": side, "position": 1},
            {"canonical_slug": canonical, "source_slug": back, "position": 2},
        ]

        result = verify_applied(config, rows, relations)

        self.assertTrue(result["ready"])
        self.assertEqual(result["relations"], 2)
        self.assertEqual(result["publishedCanonicals"], 1)
        self.assertEqual(result["retiredSources"], 2)
        self.assertEqual(result["destructiveDeletes"], 0)


if __name__ == "__main__":
    unittest.main()
