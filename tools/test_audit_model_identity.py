from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from audit_model_identity import (
    cross_model_sha_candidates,
    load_numbered_sibling_review_registry,
    sibling_suffix_candidates,
    split_view_candidates,
    sibling_review_priority,
    validate_numbered_sibling_review,
)
from model_identity import (
    canonical_identity_key,
    load_identity_aliases,
    load_identity_comparison_collision_groups,
    validate_identity_comparison_collisions,
)


def model(model_id: str, slug: str, *, folder: str = "androides/androide-18", name: str = "Androide 18") -> dict:
    return {
        "id": model_id,
        "slug": slug,
        "code": f"TS-{model_id}",
        "displayName": name,
        "categorySlug": "animes-desenhos",
        "franchiseSlug": "dragon-ball",
        "folderPathKey": folder,
        "imageCount": 1,
        "galleryManifestKey": f"gallery/{model_id}/manifest.json",
    }


class AuditModelIdentityTests(unittest.TestCase):
    def test_identity_alias_registry_is_explicit_and_cycle_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.json"
            path.write_text(json.dumps({
                "version": 1,
                "aliases": [{
                    "canonicalIdentityKey": "produto / antigo",
                    "aliases": ["produto / novo"],
                    "reason": "renome aprovado",
                }],
            }), encoding="utf-8")

            aliases = load_identity_aliases(path)

            self.assertEqual(
                canonical_identity_key("produto / novo", aliases),
                "produto / antigo",
            )
            self.assertEqual(
                canonical_identity_key("produto / outro", aliases),
                "produto / outro",
            )

            path.write_text(json.dumps({
                "version": 1,
                "aliases": [
                    {
                        "canonicalIdentityKey": "produto / a",
                        "aliases": ["produto / b"],
                        "reason": "primeiro",
                    },
                    {
                        "canonicalIdentityKey": "produto / b",
                        "aliases": ["produto / c"],
                        "reason": "cadeia inválida",
                    },
                ],
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "também aparece como alias"):
                load_identity_aliases(path)

    def test_textual_collision_group_is_explicit_exact_and_fail_closed(self) -> None:
        first = "Games / Street Fighter / Ryu / Ryu"
        second = "Games / Street Fighter / Ryu / ryu"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.json"
            path.write_text(json.dumps({
                "version": 1,
                "aliases": [
                    {"canonicalIdentityKey": first, "aliases": ["source-a"], "reason": "historico a"},
                    {"canonicalIdentityKey": second, "aliases": ["source-b"], "reason": "historico b"},
                ],
                "comparisonCollisionGroups": [{
                    "canonicalIdentityKeys": [first, second],
                    "reason": "dois produtos historicos distintos",
                }],
            }), encoding="utf-8")
            groups = load_identity_comparison_collision_groups(path)
            self.assertEqual(groups, (frozenset({first, second}),))
            validate_identity_comparison_collisions([first, second], groups)
            with self.assertRaisesRegex(RuntimeError, "textualmente equivalentes"):
                validate_identity_comparison_collisions([first, second], ())

    def test_textual_collision_group_rejects_invalid_membership(self) -> None:
        first = "Games / Street Fighter / Ryu / Ryu"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.json"
            payload = {
                "version": 1,
                "aliases": [{"canonicalIdentityKey": first, "aliases": ["source-a"], "reason": "historico"}],
                "comparisonCollisionGroups": [{
                    "canonicalIdentityKeys": [first, "Games / Street Fighter / Ryu / ryu"],
                    "reason": "invalido",
                }],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "registrado"):
                load_identity_comparison_collision_groups(path)

            second = "Games / Street Fighter / Ryu / Modelo 02"
            payload["aliases"].append({"canonicalIdentityKey": second, "aliases": ["source-b"], "reason": "outro"})
            payload["comparisonCollisionGroups"][0]["canonicalIdentityKeys"][1] = second
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "apenas identidades textualmente equivalentes"):
                load_identity_comparison_collision_groups(path)

    def test_identity_alias_registry_rejects_ambiguous_alias_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.json"
            path.write_text(json.dumps({
                "version": 1,
                "aliases": [
                    {
                        "canonicalIdentityKey": "produto / a",
                        "aliases": ["produto / renomeado"],
                        "reason": "primeiro",
                    },
                    {
                        "canonicalIdentityKey": "produto / b",
                        "aliases": ["produto / renomeado"],
                        "reason": "segundo",
                    },
                ],
            }), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "mais de um canônico"):
                load_identity_aliases(path)

    def test_directional_views_form_high_confidence_candidate(self) -> None:
        rows = [
            model("a", "dragon-ball-androide-18-traje-casual-frente"),
            model("b", "dragon-ball-androide-18-traje-casual-lateral"),
            model("c", "dragon-ball-androide-18-traje-casual-costas"),
        ]

        candidates = split_view_candidates(rows)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["confidence"], "high")
        self.assertEqual(candidates[0]["canonicalSlug"], rows[0]["slug"])
        self.assertEqual(len(candidates[0]["members"]), 3)

    def test_framing_only_pair_requires_review(self) -> None:
        rows = [
            model("a", "dragon-ball-androide-18-traje-azul-corpo-inteiro"),
            model("b", "dragon-ball-androide-18-traje-azul-em-pe"),
        ]

        candidates = split_view_candidates(rows)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["confidence"], "review")

    def test_same_family_in_different_folders_never_merges(self) -> None:
        rows = [
            model("a", "dragon-ball-heroi-frente", folder="grupo-a"),
            model("b", "dragon-ball-heroi-costas", folder="grupo-b"),
        ]

        self.assertEqual(split_view_candidates(rows), [])

    def test_multi_image_models_are_not_legacy_split_candidates(self) -> None:
        row = model("a", "dragon-ball-heroi-frente")
        row["imageCount"] = 2
        other = model("b", "dragon-ball-heroi-costas")

        self.assertEqual(split_view_candidates([row, other]), [])

    def test_explicit_copy_marker_requires_base_in_same_scope(self) -> None:
        rows = [
            model("a", "dragon-ball-androide-18-estatua"),
            model("b", "dragon-ball-androide-18-estatua-copy"),
            model("c", "dragon-ball-androide-18-outra-copy"),
        ]

        candidates = sibling_suffix_candidates(rows)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["kind"], "explicit-copy-marker")
        self.assertEqual(candidates[0]["baseSlug"], "dragon-ball-androide-18-estatua")
        self.assertEqual(
            [item["slug"] for item in candidates[0]["siblings"]],
            ["dragon-ball-androide-18-estatua-copy"],
        )

    def test_numeric_siblings_are_review_only_and_grouped_by_base(self) -> None:
        rows = [
            model("a", "dragon-ball-androide-18-estatua"),
            model("b", "dragon-ball-androide-18-estatua-01"),
            model("c", "dragon-ball-androide-18-estatua-02"),
        ]

        candidates = sibling_suffix_candidates(rows)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["kind"], "numbered-review")
        self.assertEqual(
            [item["slug"] for item in candidates[0]["siblings"]],
            [
                "dragon-ball-androide-18-estatua-01",
                "dragon-ball-androide-18-estatua-02",
            ],
        )

    def test_numeric_sibling_never_crosses_folder_scope(self) -> None:
        rows = [
            model("a", "dragon-ball-estatua", folder="grupo-a"),
            model("b", "dragon-ball-estatua-01", folder="grupo-b"),
        ]

        self.assertEqual(sibling_suffix_candidates(rows), [])

    def test_sibling_review_priority_focuses_large_groups_without_merging(self) -> None:
        self.assertEqual(
            sibling_review_priority({"kind": "explicit-copy-marker", "siblings": [{"slug": "a-copy"}]}),
            "P0",
        )
        self.assertEqual(
            sibling_review_priority({"kind": "numbered-review", "siblings": [{}] * 12}),
            "P1",
        )
        self.assertEqual(
            sibling_review_priority({"kind": "numbered-review", "siblings": [{}] * 5}),
            "P2",
        )
        self.assertEqual(
            sibling_review_priority({"kind": "numbered-review", "siblings": [{}] * 2}),
            "P3",
        )

    def test_numbered_review_registry_cannot_grow_above_audited_baseline(self) -> None:
        registry = load_numbered_sibling_review_registry()

        self.assertLessEqual(len(registry), 54)
        self.assertLessEqual(
            sum(len(item["siblingSlugs"]) for item in registry.values()),
            146,
        )
        self.assertTrue(
            all(item["status"] in {"pending", "distinct", "mixed", "gallery"} for item in registry.values())
        )

    def test_numbered_review_detects_new_or_changed_sibling_groups(self) -> None:
        rows = [
            model("base", "dragon-ball-androide-18-estatua"),
            model("one", "dragon-ball-androide-18-estatua-01"),
        ]
        key = (
            "animes-desenhos",
            "dragon-ball",
            "androides/androide-18",
            "androide 18",
            "dragon-ball-androide-18-estatua",
        )
        registry = {
            key: {
                "categorySlug": key[0],
                "franchiseSlug": key[1],
                "folderPathKey": key[2],
                "displayName": "Androide 18",
                "baseId": "base",
                "baseSlug": key[4],
                "siblingSlugs": ["dragon-ball-androide-18-estatua-01"],
                "status": "pending",
                "reason": "revisão visual pendente",
            }
        }

        summary = validate_numbered_sibling_review(rows, registry)
        self.assertEqual(summary["candidateGroups"], 1)
        self.assertEqual(summary["candidateSiblingRows"], 1)
        self.assertEqual(summary["pendingGroups"], 1)
        self.assertEqual(summary["mixedGroups"], 0)

        changed = [*rows, model("two", "dragon-ball-androide-18-estatua-02")]
        with self.assertRaisesRegex(RuntimeError, "mudaram desde a revisão"):
            validate_numbered_sibling_review(changed, registry)

        other = [
            model("other-base", "dragon-ball-outro"),
            model("other-num", "dragon-ball-outro-02"),
        ]
        with self.assertRaisesRegex(RuntimeError, "sem revisão registrada"):
            validate_numbered_sibling_review(other, registry)

    def test_numbered_review_mixed_status_is_counted_as_resolved_review(self) -> None:
        rows = [
            model("base", "dragon-ball-androide-18-estatua"),
            model("one", "dragon-ball-androide-18-estatua-01"),
        ]
        key = (
            "animes-desenhos",
            "dragon-ball",
            "androides/androide-18",
            "androide 18",
            "dragon-ball-androide-18-estatua",
        )
        registry = {
            key: {
                "categorySlug": key[0],
                "franchiseSlug": key[1],
                "folderPathKey": key[2],
                "displayName": "Androide 18",
                "baseId": "base",
                "baseSlug": key[4],
                "siblingSlugs": ["dragon-ball-androide-18-estatua-01"],
                "status": "mixed",
                "reason": "grupo revisado com consolidação explícita parcial",
            }
        }

        summary = validate_numbered_sibling_review(rows, registry)
        self.assertEqual(summary["pendingGroups"], 0)
        self.assertEqual(summary["mixedGroups"], 1)

    def test_numbered_review_gallery_status_is_counted_as_resolved_review(self) -> None:
        rows = [
            model("base", "dragon-ball-androide-18-estatua"),
            model("one", "dragon-ball-androide-18-estatua-01"),
        ]
        key = (
            "animes-desenhos",
            "dragon-ball",
            "androides/androide-18",
            "androide 18",
            "dragon-ball-androide-18-estatua",
        )
        registry = {
            key: {
                "categorySlug": key[0],
                "franchiseSlug": key[1],
                "folderPathKey": key[2],
                "displayName": "Androide 18",
                "baseId": "base",
                "baseSlug": key[4],
                "siblingSlugs": ["dragon-ball-androide-18-estatua-01"],
                "status": "gallery",
                "reason": "grupo revisado e consolidado integralmente em galeria",
            }
        }

        summary = validate_numbered_sibling_review(rows, registry)
        self.assertEqual(summary["pendingGroups"], 0)
        self.assertEqual(summary["galleryGroups"], 1)

    def test_numbered_review_rejects_stale_registry_debt(self) -> None:
        rows = [
            model("base", "dragon-ball-androide-18-estatua"),
            model("one", "dragon-ball-androide-18-estatua-01"),
        ]
        live_key = (
            "animes-desenhos",
            "dragon-ball",
            "androides/androide-18",
            "androide 18",
            "dragon-ball-androide-18-estatua",
        )
        stale_key = (
            "animes-desenhos",
            "dragon-ball",
            "androides/androide-18",
            "androide 18",
            "dragon-ball-antigo",
        )
        registry = {
            live_key: {
                "categorySlug": live_key[0],
                "franchiseSlug": live_key[1],
                "folderPathKey": live_key[2],
                "displayName": "Androide 18",
                "baseId": "base",
                "baseSlug": live_key[4],
                "siblingSlugs": ["dragon-ball-androide-18-estatua-01"],
                "status": "pending",
                "reason": "revisão pendente",
            },
            stale_key: {
                "categorySlug": stale_key[0],
                "franchiseSlug": stale_key[1],
                "folderPathKey": stale_key[2],
                "displayName": "Androide 18",
                "baseId": "old",
                "baseSlug": stale_key[4],
                "siblingSlugs": ["dragon-ball-antigo-01"],
                "status": "pending",
                "reason": "dívida antiga",
            },
        }

        with self.assertRaisesRegex(RuntimeError, "sem candidato atual"):
            validate_numbered_sibling_review(rows, registry, require_complete_registry=True)

    def test_cross_model_exact_sha_is_reported_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sha = "a" * 64
            rows = [
                model("mdl-a", "dragon-ball-a"),
                model("mdl-b", "dragon-ball-b"),
            ]
            for row in rows:
                path = root / row["galleryManifestKey"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({
                        "images": [{
                            "id": f"img-{row['id']}",
                            "sourceSha256": sha,
                        }]
                    }),
                    encoding="utf-8",
                )

            candidates = cross_model_sha_candidates(rows, root)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["sha256"], sha)
            self.assertEqual(candidates[0]["modelCount"], 2)


if __name__ == "__main__":
    unittest.main()
