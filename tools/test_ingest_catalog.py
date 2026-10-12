from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from ingest_catalog import PUBLIC_TOP_LEVEL_CATEGORIES, analyze, clean_folder, cross_product_visual_candidates, discover_catalog_roots, disambiguate_public_model_keys, hamming, infer_audited_gallery_groups, iter_images, load_audit_registry, load_public_top_level_categories, mark_duplicates, save_progress_manifest


class CatalogIngestTests(unittest.TestCase):
    def test_clean_folder_removes_audit_prefix_and_count(self) -> None:
        self.assertEqual(clean_folder("OK - Games [539]"), "Games")
        self.assertEqual(clean_folder("OK - Resident Evil [32]"), "Resident Evil")

    def test_public_scope_comes_from_versioned_ssot(self) -> None:
        self.assertEqual(
            PUBLIC_TOP_LEVEL_CATEGORIES,
            (
                "Animes & Desenhos",
                "Games",
                "Filmes & Séries",
                "Marvel & DC",
                "Tokusatsu & Cultura Japonesa",
                "Pessoas",
            ),
        )

    def test_public_scope_config_fails_closed_on_duplicates_and_unsafe_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            duplicate = root / "duplicate.json"
            duplicate.write_text(
                json.dumps({"version": 1, "categories": ["Games", "games"]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "duplicada"):
                load_public_top_level_categories(duplicate)

            unsafe = root / "unsafe.json"
            unsafe.write_text(
                json.dumps({"version": 1, "categories": ["Games/Interno"]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "insegura"):
                load_public_top_level_categories(unsafe)

    def test_discovery_only_selects_canonical_top_level_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            games = root / "OK - Games [1]"
            marvel_dc = root / "Marvel & DC [1]"
            stats = root / "00 - ESTATISTICAS - CATALOGO [1]"
            incoming = root / "Novos"
            consolidated = root / "99 - LOTES CONSOLIDADOS"
            unknown_ok = root / "OK - Experimental [1]"
            for folder in (games, marvel_dc, stats, incoming, consolidated, unknown_ok):
                folder.mkdir(parents=True)
                Image.new("RGB", (32, 32), "white").save(folder / "sample.png")

            self.assertEqual(discover_catalog_roots(root), [marvel_dc, games])
            self.assertEqual(
                list(iter_images(root)),
                [marvel_dc / "sample.png", games / "sample.png"],
            )

    def test_discovery_fails_closed_without_audited_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Novos").mkdir()
            with self.assertRaisesRegex(RuntimeError, "nenhuma categoria pública canônica"):
                list(iter_images(root))

    def test_audit_registry_is_explicit_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = root / "Animes & Desenhos [1]" / "OK - Saga [1]"
            active.mkdir(parents=True)
            image_path = active / "hero.png"
            Image.new("RGB", (64, 64), "white").save(image_path)
            import hashlib
            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            registry = root / "audit.csv"
            registry.write_text(
                "codigo,sha256,caminho,status,modelo_publico\n"
                f"AUD-1,{digest},Animes & Desenhos [1]\\OK - Saga [1]\\hero.png,OK_VISUAL|RV1,hero-modelo-01\n",
                encoding="utf-8-sig",
            )

            paths, hashes, metadata = load_audit_registry(root, registry)

            self.assertEqual(paths, [image_path])
            self.assertEqual(hashes[str(image_path.relative_to(root))], digest)
            self.assertEqual(metadata[str(image_path.relative_to(root))]["codigo"], "AUD-1")
            self.assertEqual(metadata[str(image_path.relative_to(root))]["modelo_publico"], "hero-modelo-01")

    def test_audit_registry_rejects_unicode_equivalent_duplicate_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "audit.csv"
            composed = "Games/Sagá/modelo.png"
            decomposed = "Games/Saga\u0301/modelo.png"
            registry.write_text(
                "sha256,caminho,status\n"
                + ("a" * 64) + f",{composed},OK_VISUAL|RV1\n"
                + ("b" * 64) + f",{decomposed},OK_VISUAL|RV1\n",
                encoding="utf-8-sig",
            )

            with self.assertRaisesRegex(RuntimeError, "caminho duplicado"):
                load_audit_registry(root, registry)

    def test_audit_registry_rejects_operational_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            operational = root / "99 - LOTES CONSOLIDADOS"
            operational.mkdir()
            image_path = operational / "duplicate.png"
            Image.new("RGB", (64, 64), "white").save(image_path)
            registry = root / "audit.csv"
            registry.write_text(
                "sha256,caminho,status\n"
                + ("0" * 64)
                + ",99 - LOTES CONSOLIDADOS\\duplicate.png,OK_VISUAL|RV1\n",
                encoding="utf-8-sig",
            )

            with self.assertRaisesRegex(RuntimeError, "fora de categoria auditada"):
                load_audit_registry(root, registry)

    def test_audit_registry_rejects_non_public_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "audit.csv"
            registry.write_text(
                "sha256,caminho,status\n"
                + ("0" * 64)
                + ",OK - Games [1]\\hero.png,PENDENTE\n",
                encoding="utf-8-sig",
            )

            with self.assertRaisesRegex(RuntimeError, "não aprovada para publicação"):
                load_audit_registry(root, registry)

    def test_audit_registry_rejects_lookalike_public_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "audit.csv"
            registry.write_text(
                "sha256,caminho,status\n"
                + ("0" * 64)
                + ",OK - Games [1]\\hero.png,OK_VISUAL_INVALID\n",
                encoding="utf-8-sig",
            )

            with self.assertRaisesRegex(RuntimeError, "não aprovada para publicação"):
                load_audit_registry(root, registry)

    def test_audit_registry_rejects_empty_versioned_public_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "audit.csv"
            registry.write_text(
                "sha256,caminho,status\n"
                + ("0" * 64)
                + ",OK - Games [1]\\hero.png,OK_VISUAL|\n",
                encoding="utf-8-sig",
            )

            with self.assertRaisesRegex(RuntimeError, "aprovada para publica"):
                load_audit_registry(root, registry)

    def test_progress_checkpoint_is_atomic_and_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "OK - Games [1]" / "OK - Saga [1]" / "OK - Heroi [1]"
            target.mkdir(parents=True)
            path = target / "hero.png"
            Image.new("RGB", (64, 64), "white").save(path)
            record = analyze(root, path)
            record.duplicate_group = "visual-000001"
            record.canonical = False
            output = root / "checkpoint"

            save_progress_manifest(output, [record])

            manifest = output / "manifest.jsonl"
            self.assertTrue(manifest.exists())
            self.assertFalse((output / "manifest.jsonl.tmp").exists())
            row = __import__("json").loads(manifest.read_text(encoding="utf-8"))
            self.assertIsNone(row["duplicate_group"])
            self.assertTrue(row["canonical"])

    def test_analysis_preserves_hierarchy_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "OK - Games [2]" / "OK - Saga [2]" / "OK - Heroi [2]"
            target.mkdir(parents=True)
            path = target / "frente.png"
            Image.new("RGB", (1200, 1600), "#6d5542").save(path)

            record = analyze(root, path)

            self.assertEqual(record.status, "OK")
            self.assertEqual(record.category, "Games")
            self.assertEqual(record.franchise, "Saga")
            self.assertEqual(record.model_key, "Games / Saga / Heroi")
            self.assertEqual(record.width, 1200)
            self.assertEqual(record.height, 1600)
            self.assertEqual(len(record.sha256 or ""), 64)
            self.assertEqual(len(record.dhash or ""), 16)
            self.assertIsNotNone(record.quality_score)

    def test_audited_same_stem_models_are_disambiguated_only_on_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "OK - Animes [2]" / "OK - Dragon Ball [2]" / "OK - Goku [2]"
            target.mkdir(parents=True)
            first = target / "legacy-goku-(1).jpg"
            second = target / "legacy-goku-(1).png"
            Image.new("RGB", (320, 480), "#223344").save(first)
            Image.new("RGB", (320, 480), "#334455").save(second)
            records = [analyze(root, first), analyze(root, second)]
            for index, record in enumerate(records, 1):
                record.public_model_key = f"{record.model_key} / legacy-goku-(1)"
                record.audit_code = f"AUD-{index}"

            disambiguate_public_model_keys(records)

            self.assertEqual(len({record.public_model_key for record in records}), 2)
            self.assertTrue(records[0].public_model_key.endswith("/ AUD-1"))
            self.assertTrue(records[1].public_model_key.endswith("/ AUD-2"))

    def test_legacy_view_suffixes_become_one_gallery_without_merging_distinct_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "Animes & Desenhos [7]" / "Dragon Ball [7]" / "Androides [7]" / "Androide 18 [7]"
            target.mkdir(parents=True)
            names = [
                "traje-casual-frente.jpg",
                "traje-casual-frente-alternativo.jpg",
                "traje-casual-lateral.jpg",
                "traje-casual-costas.jpg",
                "traje-azul-corpo-inteiro.jpg",
                "traje-azul-em-pe.jpg",
                "busto-realista.jpg",
            ]
            records = []
            for index, name in enumerate(names, 1):
                path = target / name
                Image.new("RGB", (320, 480), (20 * index, 30, 40)).save(path)
                record = analyze(root, path)
                record.public_model_key = f"{record.model_key} / {path.stem}"
                record.audit_code = f"AUD-{index}"
                records.append(record)

            grouped = infer_audited_gallery_groups(records)
            disambiguate_public_model_keys(records)

            casual = records[:4]
            blue = records[4:6]
            bust = records[6]
            self.assertEqual(grouped, 4)
            self.assertEqual({record.audit_model_group for record in casual}, {"traje-casual-frente"})
            self.assertEqual(len({record.public_model_key for record in casual}), 1)
            self.assertTrue(casual[0].public_model_key.endswith("/ traje-casual-frente"))
            self.assertEqual({record.audit_model_group for record in blue}, {None})
            self.assertEqual(len({record.public_model_key for record in blue}), 2)
            self.assertTrue(blue[0].public_model_key.endswith("/ traje-azul-corpo-inteiro"))
            self.assertTrue(blue[1].public_model_key.endswith("/ traje-azul-em-pe"))
            self.assertIsNone(bust.audit_model_group)
            self.assertTrue(bust.public_model_key.endswith("/ busto-realista"))

    def test_inferred_gallery_anchor_is_stable_independent_of_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "Animes & Desenhos [4]" / "Dragon Ball [4]" / "Androide 18 [4]"
            target.mkdir(parents=True)
            records = []
            for index, name in enumerate((
                "traje-casual-costas.jpg",
                "traje-casual-lateral.jpg",
                "traje-casual-frente-alternativo.jpg",
                "traje-casual-frente.jpg",
            ), 1):
                path = target / name
                Image.new("RGB", (320, 480), (30 * index, 20, 10)).save(path)
                record = analyze(root, path)
                record.public_model_key = f"{record.model_key} / {path.stem}"
                records.append(record)

            grouped = infer_audited_gallery_groups(list(reversed(records)))

            self.assertEqual(grouped, 4)
            self.assertEqual(
                {record.public_model_key for record in records},
                {f"{records[0].model_key} / traje-casual-frente"},
            )

    def test_explicit_public_model_group_is_never_overridden_by_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "Games [2]" / "Saga [2]" / "Heroi [2]"
            target.mkdir(parents=True)
            records = []
            for index, name in enumerate(("produto-frente.jpg", "produto-costas.jpg"), 1):
                path = target / name
                Image.new("RGB", (320, 480), (40 * index, 20, 10)).save(path)
                record = analyze(root, path)
                record.audit_model_group = "produto-auditado-07"
                record.public_model_key = f"{record.model_key} / produto-auditado-07"
                records.append(record)

            grouped = infer_audited_gallery_groups(records)

            self.assertEqual(grouped, 0)
            self.assertEqual({record.audit_model_group for record in records}, {"produto-auditado-07"})
            self.assertEqual(len({record.public_model_key for record in records}), 1)
    def test_intentional_public_model_group_keeps_one_shared_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "OK - Games [2]" / "OK - Saga [2]" / "OK - Heroi [2]"
            target.mkdir(parents=True)
            first = target / "frente.jpg"
            second = target / "costas.jpg"
            Image.new("RGB", (640, 960), "#223344").save(first)
            Image.new("RGB", (640, 960), "#334455").save(second)
            records = [analyze(root, first), analyze(root, second)]
            for index, record in enumerate(records, 1):
                record.public_model_key = f"{record.model_key} / heroi-modelo-01"
                record.audit_code = f"AUD-{index}"
                record.audit_model_group = "heroi-modelo-01"

            disambiguate_public_model_keys(records)

            self.assertEqual({record.public_model_key for record in records}, {f"{records[0].model_key} / heroi-modelo-01"})

    def test_intentional_gallery_is_not_collapsed_by_visual_similarity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "OK - Games [2]" / "OK - Saga [2]" / "OK - Heroi [2]"
            target.mkdir(parents=True)
            first = target / "frente.jpg"
            second = target / "angulo.jpg"
            for path, size in ((first, (800, 1000)), (second, (1000, 1250))):
                image = Image.new("RGB", size, "white")
                draw = ImageDraw.Draw(image)
                draw.rectangle((size[0] // 4, size[1] // 5, size[0] * 3 // 4, size[1] * 4 // 5), fill="#34393e")
                image.save(path)
            records = [analyze(root, first), analyze(root, second)]
            for record in records:
                record.public_model_key = f"{record.model_key} / heroi-modelo-01"
                record.audit_model_group = "heroi-modelo-01"

            groups = mark_duplicates(records, visual_threshold=6)

            self.assertEqual(groups, [])
            self.assertTrue(all(record.canonical for record in records))

    def test_exact_duplicates_are_grouped_without_deleting_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "OK - Games [2]" / "OK - Saga [2]" / "OK - Heroi [2]"
            target.mkdir(parents=True)
            first = target / "a.png"
            second = target / "b.png"
            image = Image.new("RGB", (900, 1200), "#2f3438")
            image.save(first)
            second.write_bytes(first.read_bytes())

            records = [analyze(root, first), analyze(root, second)]
            groups = mark_duplicates(records, visual_threshold=6)

            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["kind"], "exact")
            self.assertEqual(sum(record.canonical for record in records), 1)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_exact_same_file_in_different_models_is_preserved_for_both(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_dir = root / "OK - Games [2]" / "OK - Saga [2]" / "OK - Heroi A [1]"
            second_dir = root / "OK - Games [2]" / "OK - Saga [2]" / "OK - Heroi B [1]"
            first_dir.mkdir(parents=True)
            second_dir.mkdir(parents=True)
            first = first_dir / "diorama.png"
            second = second_dir / "diorama.png"
            Image.new("RGB", (1000, 1400), "#4c5560").save(first)
            second.write_bytes(first.read_bytes())

            records = [analyze(root, first), analyze(root, second)]
            self.assertEqual(records[0].sha256, records[1].sha256)
            self.assertNotEqual(records[0].model_key, records[1].model_key)

            groups = mark_duplicates(records, visual_threshold=6)

            self.assertEqual(groups, [])
            self.assertTrue(all(record.canonical for record in records))

    def test_cross_product_visual_candidates_are_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "OK - Games [2]" / "OK - Saga [2]" / "OK - Heroi [2]"
            target.mkdir(parents=True)
            first = target / "modelo-a.png"
            second = target / "modelo-b.png"
            for path, size in ((first, (800, 1000)), (second, (1000, 1250))):
                image = Image.new("RGB", size, "white")
                draw = ImageDraw.Draw(image)
                draw.rectangle((size[0] // 4, size[1] // 5, size[0] * 3 // 4, size[1] * 4 // 5), fill="#34393e")
                image.save(path)

            left, right = analyze(root, first), analyze(root, second)
            left.public_model_key = f"{left.model_key} / produto-a"
            right.public_model_key = f"{right.model_key} / produto-b"

            candidates = cross_product_visual_candidates([left, right], 6)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["kind"], "cross_product_visual")
            self.assertLessEqual(candidates[0]["distance"], 6)
            self.assertTrue(left.canonical)
            self.assertTrue(right.canonical)

    def test_cross_product_visual_candidates_never_cross_hierarchies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_dir = root / "OK - Games [2]" / "OK - Saga [2]" / "OK - Heroi A [1]"
            second_dir = root / "OK - Games [2]" / "OK - Saga [2]" / "OK - Heroi B [1]"
            first_dir.mkdir(parents=True)
            second_dir.mkdir(parents=True)
            first = first_dir / "modelo-a.png"
            second = second_dir / "modelo-b.png"
            Image.new("RGB", (800, 1000), "#334455").save(first)
            second.write_bytes(first.read_bytes())
            left, right = analyze(root, first), analyze(root, second)
            left.public_model_key = f"{left.model_key} / produto"
            right.public_model_key = f"{right.model_key} / produto"

            self.assertEqual(cross_product_visual_candidates([left, right], 6), [])

    def test_visual_candidates_prefer_higher_quality_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "OK - Animes [2]" / "OK - Serie [2]" / "OK - Heroina [2]"
            target.mkdir(parents=True)

            small = target / "vista-small.jpg"
            large = target / "vista-large.png"
            for path, size in ((small, (640, 800)), (large, (1600, 2000))):
                image = Image.new("RGB", size, "white")
                draw = ImageDraw.Draw(image)
                draw.rectangle((size[0] // 4, size[1] // 5, size[0] * 3 // 4, size[1] * 4 // 5), fill="#34393e")
                draw.ellipse((size[0] // 3, size[1] // 8, size[0] * 2 // 3, size[1] // 3), fill="#c19d79")
                image.save(path)

            records = [analyze(root, small), analyze(root, large)]
            self.assertLessEqual(hamming(records[0].dhash or "0", records[1].dhash or "0"), 6)
            groups = mark_duplicates(records, visual_threshold=6)

            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["kind"], "visual_candidate")
            self.assertEqual(groups[0]["canonical"], str(large.relative_to(root)))
            self.assertTrue(small.exists())
            self.assertTrue(large.exists())


if __name__ == "__main__":
    unittest.main()
