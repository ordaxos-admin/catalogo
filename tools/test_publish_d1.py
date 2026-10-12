from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from publish_d1 import (
    CATEGORY_ORDER,
    build_image_source_statements,
    build_statements,
    candidate_image_sources,
    exact_cross_model_sha_query,
    load_models,
    production_inventory_statement,
    production_lookup_statements,
    production_sha_lookup_statements,
    read_gallery_shrink_approvals,
    read_model_retirement_approvals,
    validate_model_retirements,
    validate_production_compatibility,
    validate_production_image_identity,
)


def model(model_id: str, slug: str, code: str, *, variant: str, variant_name: str = "") -> dict:
    return {
        "identityKey": f"test/{model_id}",
        "id": model_id,
        "slug": slug,
        "code": code,
        "displayName": variant,
        "variantName": variant_name,
        "categoryName": "Animes & Desenhos",
        "categorySlug": "animes-desenhos",
        "franchiseName": "Dragon Ball",
        "franchiseSlug": "dragon-ball",
        "collection": "Androides / Androide 18",
        "folderPath": ["Androides", "Androide 18"],
        "folderPathKey": "androides/androide-18",
        "searchText": f"dragon ball {variant}".casefold(),
        "imageCount": 1,
        "coverStorageKey": f"media/{model_id}/card.webp",
        "galleryManifestKey": f"gallery/{model_id}/manifest.json",
        "galleryVersion": 1,
    }


class PublishD1Tests(unittest.TestCase):
    def test_category_order_is_derived_from_public_scope_ssot(self) -> None:
        self.assertEqual(
            CATEGORY_ORDER,
            {
                "Animes & Desenhos": 10,
                "Games": 20,
                "Filmes & Séries": 30,
                "Marvel & DC": 40,
                "Tokusatsu & Cultura Japonesa": 50,
                "Pessoas": 60,
            },
        )

    def test_plan_deduplicates_taxonomy_but_preserves_models(self) -> None:
        rows = [
            model("mdl-1", "android-18-a", "TS-1", variant="Androide 18 A"),
            model("mdl-2", "android-18-b", "TS-2", variant="Androide 18 B"),
        ]

        statements = build_statements(rows)

        self.assertEqual(len(statements), 6)
        self.assertIn("INSERT INTO categories", statements[0]["sql"])
        self.assertIn("INSERT INTO franchises", statements[1]["sql"])
        self.assertEqual(sum("INSERT INTO catalog_folders" in item["sql"] for item in statements), 2)
        self.assertEqual(sum("INSERT INTO models" in item["sql"] for item in statements), 2)
        self.assertFalse(any("DELETE" in item["sql"].upper() for item in statements))

    def test_load_rejects_duplicate_identity_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            rows = [
                model("mdl-1", "a", "TS-1", variant="A"),
                model("mdl-2", "b", "TS-2", variant="B"),
            ]
            rows[1]["identityKey"] = rows[0]["identityKey"]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "identityKey vazio ou duplicado"):
                load_models(path)

    def test_load_rejects_textually_equivalent_identity_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            rows = [
                model("mdl-1", "pokemon-a", "TS-1", variant="A"),
                model("mdl-2", "pokemon-b", "TS-2", variant="B"),
            ]
            rows[0]["identityKey"] = "Games / Pokémon / Estátua"
            rows[1]["identityKey"] = " games / Poke\u0301mon /  Esta\u0301tua "
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "textualmente equivalentes"):
                load_models(path)

    def test_load_keeps_numbered_sibling_as_release_review_concern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            rows = [
                model("mdl-base", "dragon-ball-teste-unico", "TS-BASE", variant="Teste"),
                model("mdl-numbered", "dragon-ball-teste-unico-02", "TS-NUM", variant="Teste"),
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            self.assertEqual(len(load_models(path)), 2)

    def test_load_rejects_duplicate_public_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            rows = [
                model("mdl-1", "same", "TS-1", variant="A"),
                model("mdl-2", "same", "TS-2", variant="B"),
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "slug vazio ou duplicado"):
                load_models(path)

    def test_load_rejects_semantic_taxonomy_collision_but_allows_typographic_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            first = model("mdl-1", "produto-a", "TS-1", variant="A")
            second = model("mdl-2", "produto-b", "TS-2", variant="B")
            first["franchiseName"] = "Pokémon"
            first["franchiseSlug"] = "pokemon"
            second["franchiseName"] = "Pokemon"
            second["franchiseSlug"] = "pokemon"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in (first, second)),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "slug de franquia"):
                load_models(path)

            first["franchiseName"] = "Saga A"
            first["franchiseSlug"] = "saga-a"
            second["franchiseName"] = "Saga-A"
            second["franchiseSlug"] = "saga-a"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in (first, second)),
                encoding="utf-8",
            )

            self.assertEqual(len(load_models(path)), 2)

    def test_statements_apply_idempotently_against_catalog_schema(self) -> None:
        db = sqlite3.connect(':memory:')
        migrations = Path(__file__).resolve().parents[1] / 'migrations'
        for name in ('0001_catalog.sql', '0002_keyset_pagination.sql', '0003_catalog_counts.sql', '0009_catalog_folders.sql', '0019_model_variant_name.sql'):
            db.executescript((migrations / name).read_text(encoding='utf-8'))
        rows = [
            model('mdl-1', 'android-18-a', 'TS-1', variant='Androide 18 A'),
            model('mdl-2', 'android-18-b', 'TS-2', variant='Androide 18 B'),
        ]
        statements = build_statements(rows)
        for _ in range(2):
            with db:
                for statement in statements:
                    db.execute(statement['sql'], statement['params'])
        self.assertEqual(db.execute('SELECT COUNT(*) FROM models').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM catalog_folders').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM models WHERE folder_id IS NOT NULL').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT model_count FROM franchises').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT model_count FROM categories').fetchone()[0], 2)

    def test_republish_preserves_created_at_for_recent_feed_semantics(self) -> None:
        db = sqlite3.connect(':memory:')
        migrations = Path(__file__).resolve().parents[1] / 'migrations'
        for name in ('0001_catalog.sql', '0002_keyset_pagination.sql', '0003_catalog_counts.sql', '0009_catalog_folders.sql', '0019_model_variant_name.sql'):
            db.executescript((migrations / name).read_text(encoding='utf-8'))

        row = model('mdl-1', 'android-18', 'TS-1', variant='Androide 18')
        for statement in build_statements([row]):
            db.execute(statement['sql'], statement['params'])
        db.execute("UPDATE models SET created_at='2026-01-01 10:00:00' WHERE id='mdl-1'")

        updated = model(
            'mdl-1',
            'android-18',
            'TS-1',
            variant='Androide 18 revisada',
            variant_name='Traje casual',
        )
        for statement in build_statements([updated]):
            db.execute(statement['sql'], statement['params'])

        created_at, name, variant_name, slug, code = db.execute(
            "SELECT created_at,name,variant_name,slug,code FROM models WHERE id='mdl-1'"
        ).fetchone()
        self.assertEqual(created_at, '2026-01-01 10:00:00')
        self.assertEqual(name, 'Androide 18 revisada')
        self.assertEqual(variant_name, 'Traje casual')
        self.assertEqual(slug, 'android-18')
        self.assertEqual(code, 'TS-1')

    def test_recent_models_index_orders_only_published_models_deterministically(self) -> None:
        db = sqlite3.connect(':memory:')
        migrations = Path(__file__).resolve().parents[1] / 'migrations'
        for name in ('0001_catalog.sql', '0002_keyset_pagination.sql', '0003_catalog_counts.sql', '0009_catalog_folders.sql', '0010_recent_models_index.sql', '0019_model_variant_name.sql'):
            db.executescript((migrations / name).read_text(encoding='utf-8'))

        rows = [
            model('mdl-1', 'android-18-a', 'TS-1', variant='A'),
            model('mdl-2', 'android-18-b', 'TS-2', variant='B'),
            model('mdl-3', 'android-18-c', 'TS-3', variant='C'),
        ]
        for statement in build_statements(rows):
            db.execute(statement['sql'], statement['params'])

        db.execute("UPDATE models SET created_at='2026-01-01 10:00:00' WHERE id='mdl-1'")
        db.execute("UPDATE models SET created_at='2026-01-02 10:00:00' WHERE id IN ('mdl-2','mdl-3')")
        db.execute("UPDATE models SET published=0 WHERE id='mdl-3'")

        ids = [
            row[0]
            for row in db.execute(
                'SELECT id FROM models WHERE published=1 ORDER BY created_at DESC,id DESC LIMIT 24'
            ).fetchall()
        ]
        self.assertEqual(ids, ['mdl-2', 'mdl-1'])

        plan = ' '.join(
            str(part)
            for row in db.execute(
                'EXPLAIN QUERY PLAN SELECT id FROM models WHERE published=1 ORDER BY created_at DESC,id DESC LIMIT 24'
            ).fetchall()
            for part in row
        )
        self.assertIn('idx_models_published_created', plan)

    def test_load_rejects_high_confidence_split_view_products(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            rows = [
                model("mdl-front", "dragon-ball-recoome-busto-frente", "TS-FRONT", variant="Recoome"),
                model("mdl-side", "dragon-ball-recoome-busto-lateral", "TS-SIDE", variant="Recoome"),
                model("mdl-back", "dragon-ball-recoome-busto-costas", "TS-BACK", variant="Recoome"),
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "fichas fragmentadas por vista"):
                load_models(path)

    def test_load_rejects_explicit_copy_marker_when_base_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            rows = [
                model("mdl-base", "dragon-ball-recoome-busto", "TS-BASE", variant="Recoome"),
                model("mdl-copy", "dragon-ball-recoome-busto-copy", "TS-COPY", variant="Recoome"),
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "marcadores explícitos de cópia"):
                load_models(path)

    def test_load_allows_numbered_sibling_for_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            rows = [
                model("mdl-base", "street-fighter-cammy", "TS-BASE", variant="Cammy"),
                model("mdl-numbered", "street-fighter-cammy-02", "TS-NUM", variant="Cammy"),
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            loaded = load_models(path)

            self.assertEqual(len(loaded), 2)

    def test_load_allows_framing_pair_for_explicit_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            rows = [
                model("mdl-full", "dragon-ball-cell-primeira-forma-corpo-inteiro", "TS-FULL", variant="Cell"),
                model("mdl-stand", "dragon-ball-cell-primeira-forma-em-pe", "TS-STAND", variant="Cell"),
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            loaded = load_models(path)

            self.assertEqual(len(loaded), 2)

    def test_load_rejects_category_outside_public_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            row = model("mdl-1", "interno", "TS-1", variant="Interno")
            row["categoryName"] = "Referências Internas"
            row["categorySlug"] = "referencias-internas"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "fora do escopo público"):
                load_models(path)

    def test_production_compatibility_allows_existing_model_and_new_model(self) -> None:
        existing = model("mdl-1", "android-18-a", "TS-1", variant="Androide 18 A")
        new_model = model("mdl-2", "android-18-b", "TS-2", variant="Androide 18 B")
        production = [{
            "id": "mdl-1",
            "slug": "android-18-a",
            "code": "TS-1",
            "name": "Androide 18 A",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/manifest.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        result = validate_production_compatibility([existing, new_model], production)

        self.assertTrue(result["ready"])
        self.assertEqual(result["existingModels"], 1)
        self.assertEqual(result["newModels"], 1)

    def test_production_compatibility_rejects_attached_source_reappearing(self) -> None:
        candidate = model("mdl-source", "source-view", "TS-SOURCE", variant="Produto")
        production = [{
            "id": "mdl-source",
            "slug": "source-view",
            "code": "TS-SOURCE",
            "name": "Produto",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-source/card.webp",
            "gallery_manifest_key": "gallery/mdl-source/manifest.json",
            "gallery_version": 1,
            "gallery_source_attached": 1,
            "gallery_canonical_model_id": "mdl-canonical",
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "ficha-fonte consolidada reapareceu"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_rejects_new_id_with_existing_slug(self) -> None:
        candidate = model("mdl-new", "android-18", "TS-NEW", variant="Androide 18")
        production = [{
            "id": "mdl-old",
            "slug": "android-18",
            "code": "TS-OLD",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/manifest.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "colide com slug já publicado"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_rejects_new_id_with_existing_code(self) -> None:
        candidate = model("mdl-new", "android-18-new", "TS-OLD", variant="Androide 18")
        production = [{
            "id": "mdl-old",
            "slug": "android-18-old",
            "code": "TS-OLD",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/manifest.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "colide com código já publicado"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_rejects_slug_change_for_existing_id(self) -> None:
        candidate = model("mdl-1", "slug-novo", "TS-1", variant="Androide 18")
        production = [{
            "id": "mdl-1",
            "slug": "slug-antigo",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/manifest.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "slug de modelo publicado mudaria"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_rejects_code_change_for_existing_id(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-NEW", variant="Androide 18")
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-OLD",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/manifest.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "código de modelo publicado mudaria"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_rejects_taxonomy_move_without_explicit_migration(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "category_slug": "games",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "categoria de modelo publicado mudaria"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_rejects_public_name_change_without_migration(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18 Revisada")
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/manifest.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "nome de modelo publicado mudaria"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_rejects_collection_change_without_migration(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
        candidate["collection"] = "Androides / Outra Coleção"
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/manifest.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "coleção de modelo publicado mudaria"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_rejects_folder_move_without_migration(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
        candidate["folderPath"] = ["Androides", "Outra Pasta"]
        candidate["folderPathKey"] = "androides/outra-pasta"
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/manifest.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "pasta de modelo publicado mudaria"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_allows_gallery_expansion_with_new_version(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
        candidate["imageCount"] = 3
        candidate["galleryManifestKey"] = "gallery/mdl-1/new-gallery.json"
        candidate["galleryVersion"] = 2
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/manifest.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        result = validate_production_compatibility([candidate], production)

        self.assertEqual(result["expandedGalleries"], 1)
        self.assertEqual(result["reducedGalleries"], 0)

    def test_production_compatibility_blocks_gallery_shrink_without_approval(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
        candidate["imageCount"] = 1
        candidate["galleryManifestKey"] = "gallery/mdl-1/reduced.json"
        candidate["galleryVersion"] = 2
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 3,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/full.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "redução de galeria exige aprovação explícita"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_allows_exact_gallery_shrink_approval(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
        candidate["imageCount"] = 1
        candidate["galleryManifestKey"] = "gallery/mdl-1/reduced.json"
        candidate["galleryVersion"] = 2
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 3,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/full.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]
        approvals = {
            "mdl-1": {
                "model_id": "mdl-1",
                "current_image_count": 3,
                "new_image_count": 1,
                "current_gallery_version": 1,
                "new_gallery_version": 2,
                "reason": "vista incorreta removida após revisão",
            }
        }

        result = validate_production_compatibility([candidate], production, approvals)

        self.assertEqual(result["reducedGalleries"], 1)
        self.assertEqual(result["galleryShrinkApprovalsUsed"], 1)

    def test_production_compatibility_rejects_stale_gallery_shrink_approval(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
        candidate["imageCount"] = 1
        candidate["galleryManifestKey"] = "gallery/mdl-1/reduced.json"
        candidate["galleryVersion"] = 3
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 3,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/full.json",
            "gallery_version": 2,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]
        approvals = {
            "mdl-1": {
                "model_id": "mdl-1",
                "current_image_count": 3,
                "new_image_count": 1,
                "current_gallery_version": 1,
                "new_gallery_version": 2,
                "reason": "aprovação antiga",
            }
        }

        with self.assertRaisesRegex(RuntimeError, "não corresponde ao delta atual"):
            validate_production_compatibility([candidate], production, approvals)

    def test_production_compatibility_requires_new_version_when_gallery_changes(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
        candidate["imageCount"] = 2
        candidate["galleryManifestKey"] = "gallery/mdl-1/new.json"
        candidate["galleryVersion"] = 1
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/old.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "galeria mudou sem nova gallery_version"):
            validate_production_compatibility([candidate], production)

    def test_production_compatibility_rejects_same_manifest_with_divergent_metadata(self) -> None:
        candidate = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
        candidate["imageCount"] = 2
        candidate["galleryManifestKey"] = "gallery/mdl-1/shared.json"
        candidate["galleryVersion"] = 2
        production = [{
            "id": "mdl-1",
            "slug": "android-18",
            "code": "TS-1",
            "name": "Androide 18",
            "collection": "Androides / Androide 18",
            "folder_path": "androides/androide-18",
            "image_count": 1,
            "cover_storage_key": "media/mdl-1/card.webp",
            "gallery_manifest_key": "gallery/mdl-1/shared.json",
            "gallery_version": 1,
            "category_slug": "animes-desenhos",
            "franchise_slug": "dragon-ball",
        }]

        with self.assertRaisesRegex(RuntimeError, "content-addressed igual com metadados divergentes"):
            validate_production_compatibility([candidate], production)

    def test_gallery_shrink_approval_csv_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approvals.csv"
            path.write_text(
                "model_id,current_image_count,new_image_count,current_gallery_version,new_gallery_version,reason\n"
                "mdl-1,3,1,10,11,remoção revisada\n",
                encoding="utf-8",
            )
            approvals = read_gallery_shrink_approvals(path)
            self.assertEqual(approvals["mdl-1"]["new_image_count"], 1)

            path.write_text(
                "model_id,current_image_count,new_image_count,current_gallery_version,new_gallery_version,reason\n"
                "mdl-1,3,3,10,11,não é redução\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "não representa redução real"):
                read_gallery_shrink_approvals(path)

    def test_production_lookup_is_bounded_and_uses_all_identity_keys(self) -> None:
        rows = [
            model(f"mdl-{index}", f"slug-{index}", f"TS-{index}", variant=f"Model {index}")
            for index in range(30)
        ]

        statements = production_lookup_statements(rows, chunk_size=25)

        self.assertEqual(len(statements), 2)
        self.assertEqual(len(statements[0]["params"]), 75)
        self.assertEqual(len(statements[1]["params"]), 15)
        self.assertIn("m.id IN", statements[0]["sql"])
        self.assertIn("m.slug IN", statements[0]["sql"])
        self.assertIn("m.code IN", statements[0]["sql"])
        self.assertIn("gallery_source_attached", statements[0]["sql"])
        self.assertIn("gallery_canonical_model_id", statements[0]["sql"])

    def test_model_retirement_approval_csv_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "retirements.csv"
            path.write_text(
                "model_id,slug,code,reason\n"
                "mdl-old,old-slug,TS-OLD,consolidado em galeria canônica\n",
                encoding="utf-8",
            )

            approvals = read_model_retirement_approvals(path)

            self.assertEqual(approvals["mdl-old"]["slug"], "old-slug")
            self.assertEqual(approvals["mdl-old"]["code"], "TS-OLD")

            path.write_text(
                "model_id,slug,code,reason\n"
                "mdl-old,old-slug,TS-OLD,\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "aprovação de aposentadoria inválida"):
                read_model_retirement_approvals(path)

    def test_retirement_plan_requires_exact_approval_for_every_absent_model(self) -> None:
        candidate = model("mdl-live", "live-slug", "TS-LIVE", variant="Ativo")
        inventory = [
            {"id": "mdl-live", "slug": "live-slug", "code": "TS-LIVE", "name": "Ativo", "published": 1},
            {"id": "mdl-old", "slug": "old-slug", "code": "TS-OLD", "name": "Antigo", "published": 1},
        ]

        with self.assertRaisesRegex(RuntimeError, "faltam 1 aprovação"):
            validate_model_retirements([candidate], inventory, {})

        approvals = {
            "mdl-old": {
                "model_id": "mdl-old",
                "slug": "old-slug",
                "code": "TS-OLD",
                "reason": "consolidado",
            }
        }
        summary, statements = validate_model_retirements([candidate], inventory, approvals)

        self.assertEqual(summary["absentPublishedModels"], 1)
        self.assertEqual(summary["approvedRetirements"], 1)
        self.assertEqual(len(statements), 1)
        self.assertIn("published=0", statements[0]["sql"])
        self.assertEqual(statements[0]["params"], ["mdl-old", "old-slug", "TS-OLD"])

    def test_retirement_plan_rejects_canonical_with_attached_sources(self) -> None:
        inventory = [{
            "id": "mdl-canonical",
            "slug": "canonical",
            "code": "TS-CAN",
            "name": "Produto",
            "published": 1,
            "gallery_member_count": 2,
        }]
        approvals = {
            "mdl-canonical": {
                "model_id": "mdl-canonical",
                "slug": "canonical",
                "code": "TS-CAN",
                "reason": "remoção indevida",
            }
        }

        with self.assertRaisesRegex(RuntimeError, "canônico com fontes anexadas não pode ser aposentado"):
            validate_model_retirements([], inventory, approvals)

    def test_retirement_plan_rejects_stale_slug_or_code_approval(self) -> None:
        inventory = [
            {"id": "mdl-old", "slug": "current-slug", "code": "TS-CURRENT", "name": "Antigo", "published": 1},
        ]
        approvals = {
            "mdl-old": {
                "model_id": "mdl-old",
                "slug": "stale-slug",
                "code": "TS-CURRENT",
                "reason": "aprovação antiga",
            }
        }

        with self.assertRaisesRegex(RuntimeError, "estado atual"):
            validate_model_retirements([], inventory, approvals)

    def test_retirement_plan_rejects_approval_for_model_still_in_snapshot(self) -> None:
        candidate = model("mdl-live", "live-slug", "TS-LIVE", variant="Ativo")
        inventory = [
            {"id": "mdl-live", "slug": "live-slug", "code": "TS-LIVE", "name": "Ativo", "published": 1},
        ]
        approvals = {
            "mdl-live": {
                "model_id": "mdl-live",
                "slug": "live-slug",
                "code": "TS-LIVE",
                "reason": "não deveria aposentar",
            }
        }

        with self.assertRaisesRegex(RuntimeError, "sem modelo ausente correspondente"):
            validate_model_retirements([candidate], inventory, approvals)

    def test_production_inventory_statement_is_keyset_bounded(self) -> None:
        statement = production_inventory_statement("mdl-100", 1000)

        self.assertIn("published=1 AND id>?", statement["sql"])
        self.assertIn("ORDER BY id", statement["sql"])
        self.assertIn("gallery_member_count", statement["sql"])
        self.assertEqual(statement["params"], ["mdl-100", 1000])

        with self.assertRaises(ValueError):
            production_inventory_statement("", 5001)

    def _write_gallery_bundle(self, root: Path, row: dict, shas: list[str]) -> None:
        manifest_key = Path(row["galleryManifestKey"])
        manifest_path = root / "r2" / manifest_key
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        images = []
        for index, sha in enumerate(shas):
            image_id = f"img-{index + 1}"
            images.append({
                "id": image_id,
                "role": "cover" if index == 0 else "gallery",
                "width": 900,
                "height": 1400,
                "bytes": 1000 + index,
                "mime": "image/webp",
                "qualityScore": 90 - index,
                "sourceSha256": sha,
                "variantKeys": {
                    "thumb": f"media/{row['id']}/{image_id}/thumb.webp",
                    "card": f"media/{row['id']}/{image_id}/card.webp",
                    "detail": f"media/{row['id']}/{image_id}/detail.webp",
                },
            })
        row["imageCount"] = len(images)
        row["coverStorageKey"] = images[0]["variantKeys"]["card"]
        manifest_path.write_text(
            json.dumps({
                "version": row["galleryVersion"],
                "modelId": row["id"],
                "images": images,
            }),
            encoding="utf-8",
        )

    def test_image_source_statements_materialize_manifest_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models_path = root / "models.jsonl"
            models_path.write_text("", encoding="utf-8")
            row = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
            row["galleryManifestKey"] = "gallery/mdl-1/manifest.json"
            self._write_gallery_bundle(root, row, ["a" * 64, "b" * 64])

            statements = build_image_source_statements(models_path, [row])

            self.assertEqual(len(statements), 3)
            self.assertEqual(sum("INSERT INTO model_image_sources" in item["sql"] for item in statements), 2)
            self.assertIn("gallery_version<>?", statements[-1]["sql"])
            self.assertEqual(statements[0]["params"][4], "a" * 64)

    def test_image_source_statements_reject_exact_duplicate_inside_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models_path = root / "models.jsonl"
            models_path.write_text("", encoding="utf-8")
            row = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
            row["galleryManifestKey"] = "gallery/mdl-1/manifest.json"
            self._write_gallery_bundle(root, row, ["c" * 64, "c" * 64])

            with self.assertRaisesRegex(RuntimeError, "SHA-256 duplicado dentro da mesma galeria"):
                build_image_source_statements(models_path, [row])

    def test_candidate_image_sources_reject_exact_image_across_products(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models_path = root / "models.jsonl"
            models_path.write_text("", encoding="utf-8")
            first = model("mdl-a", "a", "TS-A", variant="A")
            second = model("mdl-b", "b", "TS-B", variant="B")
            first["galleryManifestKey"] = "gallery/mdl-a/manifest.json"
            second["galleryManifestKey"] = "gallery/mdl-b/manifest.json"
            self._write_gallery_bundle(root, first, ["e" * 64])
            self._write_gallery_bundle(root, second, ["e" * 64])

            with self.assertRaisesRegex(RuntimeError, "produtos candidatos diferentes"):
                candidate_image_sources(models_path, [first, second])

    def test_production_sha_lookup_is_effective_model_aware_and_bounded(self) -> None:
        sources = [
            {
                "model_id": "mdl-a",
                "image_id": "img-a",
                "position": 0,
                "role": "cover",
                "source_sha256": "a" * 64,
                "gallery_version": 1,
            },
            {
                "model_id": "mdl-b",
                "image_id": "img-b",
                "position": 0,
                "role": "cover",
                "source_sha256": "b" * 64,
                "gallery_version": 1,
            },
        ]

        statements = production_sha_lookup_statements(sources, chunk_size=1)

        self.assertEqual(len(statements), 2)
        self.assertIn("COALESCE(member.canonical_model_id,s.model_id)", statements[0]["sql"])
        self.assertIn("source.gallery_version=s.gallery_version", statements[0]["sql"])
        self.assertIn("effective.published=1", statements[0]["sql"])
        self.assertEqual(statements[0]["params"], ["a" * 64])
        self.assertEqual(statements[1]["params"], ["b" * 64])
        with self.assertRaises(ValueError):
            production_sha_lookup_statements(sources, chunk_size=251)

    def test_production_image_identity_blocks_historical_owner_drift(self) -> None:
        sources = [{
            "model_id": "mdl-current",
            "image_id": "img-1",
            "position": 0,
            "role": "cover",
            "source_sha256": "f" * 64,
            "gallery_version": 1,
        }]

        ready = validate_production_image_identity(
            sources,
            [{"source_sha256": "f" * 64, "effective_model_id": "mdl-current"}],
        )
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["historicalMatches"], 1)

        with self.assertRaisesRegex(RuntimeError, "identity drift de produção"):
            validate_production_image_identity(
                sources,
                [{"source_sha256": "f" * 64, "effective_model_id": "mdl-historical"}],
            )

    def test_image_source_schema_allows_cross_model_reuse_but_blocks_same_gallery_duplicate(self) -> None:
        db = sqlite3.connect(":memory:")
        migrations = Path(__file__).resolve().parents[1] / "migrations"
        db.executescript((migrations / "0001_catalog.sql").read_text(encoding="utf-8"))
        db.executescript((migrations / "0013_model_image_sources.sql").read_text(encoding="utf-8"))
        db.execute("INSERT INTO categories(slug,name) VALUES('animes-desenhos','Animes')")
        category_id = db.execute("SELECT id FROM categories WHERE slug='animes-desenhos'").fetchone()[0]
        db.execute("INSERT INTO franchises(category_id,slug,name) VALUES(?,?,?)", (category_id, "dragon-ball", "Dragon Ball"))
        franchise_id = db.execute("SELECT id FROM franchises WHERE slug='dragon-ball'").fetchone()[0]
        for model_id, slug, code in (("mdl-a", "a", "TS-A"), ("mdl-b", "b", "TS-B")):
            db.execute(
                "INSERT INTO models(id,franchise_id,slug,code,name,gallery_version,published) VALUES(?,?,?,?,?,1,1)",
                (model_id, franchise_id, slug, code, slug),
            )

        sha = "d" * 64
        db.execute(
            "INSERT INTO model_image_sources(model_id,image_id,position,role,source_sha256,gallery_version) VALUES(?,?,?,?,?,1)",
            ("mdl-a", "img-1", 0, "cover", sha),
        )
        db.execute(
            "INSERT INTO model_image_sources(model_id,image_id,position,role,source_sha256,gallery_version) VALUES(?,?,?,?,?,1)",
            ("mdl-b", "img-1", 0, "cover", sha),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO model_image_sources(model_id,image_id,position,role,source_sha256,gallery_version) VALUES(?,?,?,?,?,1)",
                ("mdl-a", "img-2", 1, "gallery", sha),
            )
        db.close()

    def test_exact_cross_model_sha_query_is_review_only_and_bounded(self) -> None:
        statement = exact_cross_model_sha_query(25)

        self.assertIn(
            "COUNT(DISTINCT COALESCE(member.canonical_model_id,s.model_id))>1",
            statement["sql"],
        )
        self.assertIn("LEFT JOIN model_gallery_members member", statement["sql"])
        self.assertIn("source.gallery_version=s.gallery_version", statement["sql"])
        self.assertIn("effective.published=1", statement["sql"])
        self.assertEqual(statement["params"], [25])
        with self.assertRaises(ValueError):
            exact_cross_model_sha_query(501)

    def test_load_rejects_model_without_publishable_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.jsonl"
            row = model("mdl-1", "android-18", "TS-1", variant="Androide 18")
            row["imageCount"] = 0
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "modelo sem imagem publicável"):
                load_models(path)


    def test_repository_retirement_approvals_are_valid(self) -> None:
        path = Path(__file__).resolve().parents[1] / "config" / "catalog-model-retirement-approvals.csv"
        approvals = read_model_retirement_approvals(path)

        self.assertTrue(approvals)
        for model_id, approval in approvals.items():
            self.assertEqual(approval["model_id"], model_id)
            self.assertTrue(approval["slug"])
            self.assertTrue(approval["code"])
            self.assertTrue(approval["reason"])


if __name__ == "__main__":
    unittest.main()
