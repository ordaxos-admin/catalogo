from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from build_media_bundle import (
    apply_legacy_gallery_overrides,
    build_bundle,
    disambiguate_public_variants,
    franchise_index,
    load_taxonomy_config,
    media_build_output_lock,
    model_display_name,
    model_variant_name,
    save_media_build_state,
    stable_id,
    validate_built_model_identifiers,
    validate_built_taxonomy_identifiers,
    validate_variant_disambiguation,
)


class MediaBundleTests(unittest.TestCase):
    def test_variant_name_removes_nested_franchise_and_character_prefixes(self) -> None:
        self.assertEqual(
            model_variant_name("Star-Wars-Darth-Vader-04", "Darth Vader", "Star Wars", False),
            "Modelo 04",
        )
        self.assertEqual(
            model_variant_name("Cammy-Classic-Pose", "Cammy", "Street Fighter", False),
            "Classic Pose",
        )
        self.assertEqual(
            model_variant_name("Cammy", "Cammy", "Street Fighter", False),
            "",
        )

    def test_variant_name_marks_nsfw_without_changing_taxonomy(self) -> None:
        self.assertEqual(
            model_variant_name(
                "Street-Fighter-Cammy-STL-5-Street-Wear-Bust-NSFW",
                "Cammy",
                "Street Fighter",
                False,
            ),
            "NSFW",
        )
        self.assertEqual(
            model_variant_name(
                "Resident-Evil-Ada-Wong-STL-4-NSFW",
                "Ada Wong",
                "Resident Evil",
                False,
            ),
            "NSFW",
        )

    def test_disambiguate_public_variants_labels_base_model_as_principal(self) -> None:
        entries = [
            {
                "id": "mdl-a",
                "identityKey": "Animes / Dragon Ball / Goku e Piccolo / goku-e-piccolo",
                "sourceIdentityKey": "Animes / Dragon Ball / Goku e Piccolo / goku-e-piccolo",
                "categorySlug": "animes",
                "franchiseSlug": "dragon-ball",
                "folderPathKey": "dioramas/goku-e-piccolo",
                "displayName": "Goku e Piccolo",
                "variantName": "",
                "searchText": "goku e piccolo",
            },
            {
                "id": "mdl-b",
                "identityKey": "Animes / Dragon Ball / Goku e Piccolo / goku-e-piccolo-2",
                "sourceIdentityKey": "Animes / Dragon Ball / Goku e Piccolo / goku-e-piccolo-2",
                "categorySlug": "animes",
                "franchiseSlug": "dragon-ball",
                "folderPathKey": "dioramas/goku-e-piccolo",
                "displayName": "Goku e Piccolo",
                "variantName": "Modelo 2",
                "searchText": "goku e piccolo modelo 2",
            },
        ]

        adjusted = disambiguate_public_variants(entries)

        self.assertEqual(adjusted, 1)
        self.assertEqual(entries[0]["variantName"], "Principal")
        self.assertEqual(entries[1]["variantName"], "Modelo 2")
        self.assertIn("principal", entries[0]["searchText"])
        validate_variant_disambiguation(entries)

    def test_disambiguate_public_variants_uses_source_label_for_duplicate_variant(self) -> None:
        entries = [
            {
                "id": "mdl-a",
                "identityKey": "Games / Street Fighter / Ryu / Ryu 02",
                "sourceIdentityKey": "Games / Street Fighter / Ryu / Ryu 02",
                "categorySlug": "games",
                "franchiseSlug": "street-fighter",
                "folderPathKey": "ryu",
                "displayName": "Ryu",
                "variantName": "Modelo 02",
                "searchText": "ryu modelo 02",
            },
            {
                "id": "mdl-b",
                "identityKey": "Games / Street Fighter / Ryu / ryu",
                "sourceIdentityKey": "Games / Street Fighter / Ryu / ryu-modelo-02",
                "categorySlug": "games",
                "franchiseSlug": "street-fighter",
                "folderPathKey": "ryu",
                "displayName": "Ryu",
                "variantName": "Modelo 02",
                "searchText": "ryu modelo 02",
            },
        ]

        adjusted = disambiguate_public_variants(entries)

        self.assertEqual(adjusted, 1)
        self.assertEqual(entries[0]["variantName"], "Modelo 02")
        self.assertEqual(entries[1]["variantName"], "Ryu modelo 02")
        validate_variant_disambiguation(entries)

    def test_disambiguate_public_variants_has_deterministic_last_resort_suffix(self) -> None:
        entries = [
            {
                "id": "mdl-a",
                "identityKey": "Games / Resident Evil / Ada Wong / Ada-Wong-2",
                "sourceIdentityKey": "Games / Resident Evil / Ada Wong / Ada-Wong-2",
                "categorySlug": "games",
                "franchiseSlug": "resident-evil",
                "folderPathKey": "ada-wong",
                "displayName": "Ada Wong",
                "variantName": "Ada wong 2",
                "searchText": "ada wong 2",
            },
            {
                "id": "mdl-b",
                "identityKey": "Games / Resident Evil / Ada Wong / ada_wong_2",
                "sourceIdentityKey": "Games / Resident Evil / Ada Wong / ada_wong_2",
                "categorySlug": "games",
                "franchiseSlug": "resident-evil",
                "folderPathKey": "ada-wong",
                "displayName": "Ada Wong",
                "variantName": "Ada wong 2",
                "searchText": "ada wong 2",
            },
        ]

        adjusted = disambiguate_public_variants(entries)

        self.assertEqual(adjusted, 1)
        self.assertEqual(entries[0]["variantName"], "Ada wong 2")
        self.assertEqual(entries[1]["variantName"], "Ada wong 2 · Variante 2")
        validate_variant_disambiguation(entries)

    def test_public_display_name_uses_character_leaf_but_not_generic_group(self) -> None:
        self.assertEqual(
            model_display_name(
                ["Marvel & DC", "Heróis Individuais", "Homem-Aranha", "Abutre"],
                ["Homem-Aranha", "Abutre"],
                "Abutre traje verde com asas em estatua",
                True,
            ),
            "Abutre",
        )
        self.assertEqual(
            model_display_name(
                ["Marvel & DC", "Adão Negro", "Confrontos"],
                ["Confrontos"],
                "Adao negro e doutor destino",
                True,
            ),
            "Adao negro e doutor destino",
        )

    def test_variant_name_separates_character_from_product_variant(self) -> None:
        self.assertEqual(
            model_variant_name(
                "androide-18-traje-casual-frente",
                "Androide 18",
                "Dragon Ball",
                True,
            ),
            "Traje casual",
        )
        self.assertEqual(
            model_variant_name(
                "cammy-02",
                "Cammy",
                "Street Fighter",
                False,
            ),
            "Modelo 02",
        )
        self.assertEqual(
            model_variant_name(
                "cammy",
                "Cammy",
                "Street Fighter",
                False,
            ),
            "",
        )
        self.assertEqual(
            model_variant_name(
                "caitlyn-kiramman-league-of-legends-2",
                "Caitlyn Kiramman",
                "League of Legends",
                False,
            ),
            "Modelo 2",
        )

    def test_bundle_generates_variants_without_upscaling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            source.mkdir()
            model_dir = source / "Games" / "Saga" / "Heroi"
            model_dir.mkdir(parents=True)

            high = model_dir / "high.png"
            low = model_dir / "low.jpg"
            ignored = model_dir / "duplicate.jpg"
            Image.new("RGB", (1800, 1200), "#45484c").save(high)
            Image.new("RGB", (300, 200), "#77716b").save(low)
            Image.new("RGB", (900, 600), "#45484c").save(ignored)

            manifest = root / "manifest.jsonl"
            rows = [
                {
                    "path": str(high.relative_to(source)), "size": high.stat().st_size, "status": "OK", "canonical": True,
                    "sha256": "a" * 64, "width": 1800, "height": 1200, "quality_score": 95.0,
                    "model_key": "Games / Saga / Heroi",
                },
                {
                    "path": str(low.relative_to(source)), "size": low.stat().st_size, "status": "OK", "canonical": True,
                    "sha256": "b" * 64, "width": 300, "height": 200, "quality_score": 40.0,
                    "model_key": "Games / Saga / Heroi",
                },
                {
                    "path": str(ignored.relative_to(source)), "size": ignored.stat().st_size, "status": "OK", "canonical": False,
                    "sha256": "c" * 64, "width": 900, "height": 600, "quality_score": 80.0,
                    "model_key": "Games / Saga / Heroi",
                },
            ]
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            output = root / "bundle"

            summary = build_bundle(source, manifest, output, include_original=False)

            self.assertEqual(summary["models"], 1)
            self.assertEqual(summary["canonicalImages"], 2)
            self.assertEqual(summary["variantFiles"], 6)
            self.assertEqual(summary["galleryManifests"], 1)

            model_index = [json.loads(line) for line in (output / "models.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(model_index), 1)
            model = model_index[0]
            self.assertEqual(model["displayName"], "Heroi")
            self.assertEqual(model["categoryName"], "Games")
            self.assertEqual(model["categorySlug"], "games")
            self.assertEqual(model["franchiseName"], "Saga")
            self.assertEqual(model["franchiseSlug"], "saga")
            self.assertEqual(model["slug"], "saga-heroi")
            self.assertTrue(model["code"].startswith("TS-"))
            self.assertEqual(model["imageCount"], 2)
            self.assertRegex(model["galleryManifestKey"], rf"^gallery/{model['id']}/[0-9a-f]{{24}}\.json$")
            self.assertIsInstance(model["galleryVersion"], int)

            gallery_path = output / "r2" / model["galleryManifestKey"]
            gallery = json.loads(gallery_path.read_text(encoding="utf-8"))
            self.assertEqual(gallery["version"], model["galleryVersion"])
            self.assertEqual(len(gallery["images"]), 2)
            self.assertEqual(gallery["images"][0]["sourceSha256"], "a" * 64)
            self.assertEqual(gallery["images"][0]["role"], "cover")

            low_item = next(item for item in gallery["images"] if item["sourceSha256"] == "b" * 64)
            for variant in low_item["variants"].values():
                self.assertLessEqual(variant["width"], 300)
                self.assertLessEqual(variant["height"], 200)

    def test_gallery_manifest_key_is_deterministic_and_changes_with_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Games" / "Saga" / "Heroi"
            model_dir.mkdir(parents=True)
            first = model_dir / "first.png"
            second = model_dir / "second.png"
            Image.new("RGB", (640, 960), "#202428").save(first)
            Image.new("RGB", (640, 960), "#303438").save(second)
            manifest = root / "manifest.jsonl"

            def row(path: Path, sha: str, score: float) -> dict:
                return {
                    "path": str(path.relative_to(source)), "size": path.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": sha,
                    "width": 640, "height": 960, "quality_score": score,
                    "model_key": "Games / Saga / Heroi",
                }

            manifest.write_text(json.dumps(row(first, "1" * 64, 80.0)) + "\n", encoding="utf-8")
            output_a = root / "bundle-a"
            output_b = root / "bundle-b"
            build_bundle(source, manifest, output_a, include_original=False)
            build_bundle(source, manifest, output_b, include_original=False)
            model_a = json.loads((output_a / "models.jsonl").read_text(encoding="utf-8"))
            model_b = json.loads((output_b / "models.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(model_a["galleryManifestKey"], model_b["galleryManifestKey"])
            self.assertEqual(model_a["galleryVersion"], model_b["galleryVersion"])

            manifest.write_text(
                json.dumps(row(first, "1" * 64, 80.0)) + "\n" + json.dumps(row(second, "2" * 64, 70.0)) + "\n",
                encoding="utf-8",
            )
            output_c = root / "bundle-c"
            build_bundle(source, manifest, output_c, include_original=False)
            model_c = json.loads((output_c / "models.jsonl").read_text(encoding="utf-8"))
            self.assertNotEqual(model_a["galleryManifestKey"], model_c["galleryManifestKey"])
            self.assertNotEqual(model_a["galleryVersion"], model_c["galleryVersion"])

    def test_slug_collision_gets_stable_model_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            paths = [
                source / "Games" / "Saga A" / "Heroi",
                source / "Games" / "Saga-A" / "Heroi",
            ]
            rows = []
            for index, model_dir in enumerate(paths):
                model_dir.mkdir(parents=True)
                image_path = model_dir / ("vista.png" if index == 0 else "vista-alternativa.png")
                Image.new("RGB", (320, 480), f"#{index + 2}{index + 2}{index + 2}333").save(image_path)
                model_key = " / ".join(image_path.relative_to(source).parts[:-1])
                rows.append({
                    "path": str(image_path.relative_to(source)), "size": image_path.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": str(index + 3) * 64,
                    "width": 320, "height": 480, "quality_score": 50.0,
                    "model_key": model_key,
                })
            manifest = root / "manifest.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            output = root / "bundle"
            build_bundle(source, manifest, output, include_original=False)
            models = [json.loads(line) for line in (output / "models.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len({model["slug"] for model in models}), 2)
            self.assertTrue(all(model["slug"].startswith("saga-a-heroi-") for model in models))

    def test_audited_public_entries_remain_distinct_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "OK - Animes & Desenhos [2]" / "OK - Animes [2]" / "OK - Dragon Ball [2]" / "OK - Androide 18 [2]"
            model_dir.mkdir(parents=True)
            first = model_dir / "androide-18-busto-display.webp"
            second = model_dir / "androide-18-traje-azul.webp"
            Image.new("RGB", (640, 960), "#223344").save(first, "WEBP")
            Image.new("RGB", (640, 960), "#334455").save(second, "WEBP")
            rows = []
            hierarchy = "Animes & Desenhos / Animes / Dragon Ball / Androide 18"
            for index, path in enumerate((first, second), 1):
                rows.append({
                    "path": str(path.relative_to(source)), "size": path.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": str(index) * 64,
                    "width": 640, "height": 960, "quality_score": 70.0,
                    "model_key": hierarchy,
                    "public_model_key": f"{hierarchy} / {path.stem}",
                })
            manifest = root / "manifest.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            output = root / "bundle"
            summary = build_bundle(source, manifest, output, include_original=False)
            models = [json.loads(line) for line in (output / "models.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(summary["models"], 2)
            self.assertEqual(len(models), 2)
            self.assertTrue(all(model["franchiseName"] == "Dragon Ball" for model in models))
            self.assertEqual({model["imageCount"] for model in models}, {1})
            self.assertEqual({model["displayName"] for model in models}, {"Androide 18"})
            self.assertEqual(len({model["id"] for model in models}), 2)
            self.assertEqual({model["slug"] for model in models}, {"dragon-ball-androide-18-busto-display", "dragon-ball-androide-18-traje-azul"})
            self.assertTrue(any("busto display" in model["searchText"] for model in models))
            self.assertTrue(any("traje azul" in model["searchText"] for model in models))

    def test_audited_shared_public_model_builds_one_multi_image_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "OK - Animes & Desenhos [3]" / "OK - Animes [3]" / "OK - Dragon Ball [3]" / "OK - Goku [3]"
            model_dir.mkdir(parents=True)
            paths = [
                model_dir / "goku-frente.webp",
                model_dir / "goku-costas.webp",
                model_dir / "goku-lateral.webp",
            ]
            for index, path in enumerate(paths):
                Image.new("RGB", (800 + index * 80, 1200 + index * 120), f"#{2 + index}{3 + index}{4 + index}455").save(path, "WEBP")

            hierarchy = "Animes & Desenhos / Animes / Dragon Ball / Goku"
            public_key = f"{hierarchy} / goku-modelo-01"
            rows = [
                {
                    "path": str(path.relative_to(source)),
                    "size": path.stat().st_size,
                    "status": "OK",
                    "canonical": True,
                    "sha256": str(index + 4) * 64,
                    "width": 800 + index * 80,
                    "height": 1200 + index * 120,
                    "quality_score": 70.0 + index,
                    "model_key": hierarchy,
                    "public_model_key": public_key,
                    "audit_model_group": "goku-modelo-01",
                }
                for index, path in enumerate(paths)
            ]
            manifest = root / "manifest.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            output = root / "bundle"

            summary = build_bundle(source, manifest, output, include_original=False)
            models = [json.loads(line) for line in (output / "models.jsonl").read_text(encoding="utf-8").splitlines()]

            self.assertEqual(summary["models"], 1)
            self.assertEqual(summary["canonicalImages"], 3)
            self.assertEqual(len(models), 1)
            self.assertEqual(models[0]["imageCount"], 3)
            self.assertEqual(models[0]["displayName"], "Goku")
            self.assertEqual(models[0]["slug"], "dragon-ball-goku-modelo-01")

            gallery = json.loads((output / "r2" / models[0]["galleryManifestKey"]).read_text(encoding="utf-8"))
            self.assertEqual(len(gallery["images"]), 3)
            self.assertEqual(gallery["images"][0]["role"], "cover")
            self.assertEqual({image["sourceSha256"] for image in gallery["images"]}, {"4" * 64, "5" * 64, "6" * 64})

    def test_grouped_identity_cannot_cross_model_hierarchies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            first_dir = source / "Games" / "Saga A" / "Heroi"
            second_dir = source / "Games" / "Saga B" / "Heroi"
            first_dir.mkdir(parents=True)
            second_dir.mkdir(parents=True)
            first = first_dir / "frente.webp"
            second = second_dir / "costas.webp"
            Image.new("RGB", (640, 960), "#223344").save(first, "WEBP")
            Image.new("RGB", (640, 960), "#334455").save(second, "WEBP")
            public_key = "produto-compartilhado"
            rows = [
                {
                    "path": str(first.relative_to(source)), "size": first.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": "9" * 64,
                    "width": 640, "height": 960, "quality_score": 80.0,
                    "model_key": "Games / Saga A / Heroi",
                    "public_model_key": public_key,
                    "audit_model_group": "produto-01",
                },
                {
                    "path": str(second.relative_to(source)), "size": second.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": "a" * 64,
                    "width": 640, "height": 960, "quality_score": 79.0,
                    "model_key": "Games / Saga B / Heroi",
                    "public_model_key": public_key,
                    "audit_model_group": "produto-01",
                },
            ]
            manifest = root / "manifest.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "hierarquias diferentes"):
                build_bundle(source, manifest, root / "bundle", include_original=False)

    def test_grouped_identity_cannot_mix_different_product_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Games" / "Saga" / "Heroi"
            model_dir.mkdir(parents=True)
            first = model_dir / "frente.webp"
            second = model_dir / "costas.webp"
            Image.new("RGB", (640, 960), "#223344").save(first, "WEBP")
            Image.new("RGB", (640, 960), "#334455").save(second, "WEBP")
            hierarchy = "Games / Saga / Heroi"
            public_key = f"{hierarchy} / produto-publico"
            rows = [
                {
                    "path": str(first.relative_to(source)), "size": first.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": "b" * 64,
                    "width": 640, "height": 960, "quality_score": 80.0,
                    "model_key": hierarchy, "public_model_key": public_key,
                    "audit_model_group": "produto-a",
                },
                {
                    "path": str(second.relative_to(source)), "size": second.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": "c" * 64,
                    "width": 640, "height": 960, "quality_score": 79.0,
                    "model_key": hierarchy, "public_model_key": public_key,
                    "audit_model_group": "produto-b",
                },
            ]
            manifest = root / "manifest.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "produtos auditados diferentes"):
                build_bundle(source, manifest, root / "bundle", include_original=False)

    def test_two_grouped_products_for_same_character_remain_distinct_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "OK - Animes & Desenhos [4]" / "OK - Animes [4]" / "OK - Dragon Ball [4]" / "OK - Goku [4]"
            model_dir.mkdir(parents=True)
            hierarchy = "Animes & Desenhos / Animes / Dragon Ball / Goku"
            rows = []
            for product_index, product in enumerate(("goku-modelo-a", "goku-modelo-b"), 1):
                for view_index, view in enumerate(("frente", "costas"), 1):
                    path = model_dir / f"{product}-{view}.webp"
                    Image.new("RGB", (800 + view_index * 20, 1200 + view_index * 30), f"#{product_index + 2}{view_index + 2}5566").save(path, "WEBP")
                    rows.append({
                        "path": str(path.relative_to(source)),
                        "size": path.stat().st_size,
                        "status": "OK",
                        "canonical": True,
                        "sha256": f"{product_index}{view_index}" * 32,
                        "width": 800 + view_index * 20,
                        "height": 1200 + view_index * 30,
                        "quality_score": 80.0 + view_index,
                        "model_key": hierarchy,
                        "public_model_key": f"{hierarchy} / {product}",
                        "audit_model_group": product,
                    })

            manifest = root / "manifest.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            output = root / "bundle"
            summary = build_bundle(source, manifest, output, include_original=False)
            models = [json.loads(line) for line in (output / "models.jsonl").read_text(encoding="utf-8").splitlines()]

            self.assertEqual(summary["models"], 2)
            self.assertEqual({model["imageCount"] for model in models}, {2})
            self.assertEqual({model["displayName"] for model in models}, {"Goku"})
            self.assertEqual(len({model["id"] for model in models}), 2)
            self.assertEqual(len({model["slug"] for model in models}), 2)
            self.assertEqual({model["slug"] for model in models}, {"dragon-ball-goku-modelo-a", "dragon-ball-goku-modelo-b"})

    def test_grouped_gallery_is_detected_even_when_historical_cover_has_no_group_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "OK - Games [2]" / "OK - God of War [2]" / "OK - Kratos [2]"
            model_dir.mkdir(parents=True)
            historical = model_dir / "kratos-estatua-classica.webp"
            incoming = model_dir / "kratos-costas.webp"
            Image.new("RGB", (1600, 2400), "#445566").save(historical, "WEBP")
            Image.new("RGB", (900, 1350), "#556677").save(incoming, "WEBP")
            hierarchy = "Games / God of War / Kratos"
            public_key = f"{hierarchy} / kratos-estatua-classica"
            rows = [
                {
                    "path": str(historical.relative_to(source)), "size": historical.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": "d" * 64,
                    "width": 1600, "height": 2400, "quality_score": 99.0,
                    "model_key": hierarchy, "public_model_key": public_key,
                },
                {
                    "path": str(incoming.relative_to(source)), "size": incoming.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": "e" * 64,
                    "width": 900, "height": 1350, "quality_score": 70.0,
                    "model_key": hierarchy, "public_model_key": public_key,
                    "audit_model_group": "kratos-estatua-classica",
                },
            ]
            manifest = root / "manifest.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            output = root / "bundle"

            build_bundle(source, manifest, output, include_original=False)
            model = json.loads((output / "models.jsonl").read_text(encoding="utf-8"))

            self.assertEqual(model["imageCount"], 2)
            self.assertEqual(model["displayName"], "Kratos")
            self.assertEqual(model["slug"], "god-of-war-kratos-estatua-classica")

    def test_grouped_product_slug_does_not_change_when_sibling_product_is_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "OK - Animes & Desenhos [4]" / "OK - Animes [4]" / "OK - Dragon Ball [4]" / "OK - Goku [4]"
            model_dir.mkdir(parents=True)
            hierarchy = "Animes & Desenhos / Animes / Dragon Ball / Goku"

            def make_row(product: str, index: int) -> dict:
                path = model_dir / f"{product}-frente.webp"
                Image.new("RGB", (800 + index * 10, 1200 + index * 20), f"#{index + 2}45566").save(path, "WEBP")
                return {
                    "path": str(path.relative_to(source)),
                    "size": path.stat().st_size,
                    "status": "OK",
                    "canonical": True,
                    "sha256": str(index + 6) * 64,
                    "width": 800 + index * 10,
                    "height": 1200 + index * 20,
                    "quality_score": 80.0 + index,
                    "model_key": hierarchy,
                    "public_model_key": f"{hierarchy} / {product}",
                    "audit_model_group": product,
                }

            first_row = make_row("goku-modelo-a", 1)
            manifest_one = root / "one.jsonl"
            manifest_one.write_text(json.dumps(first_row) + "\n", encoding="utf-8")
            output_one = root / "bundle-one"
            build_bundle(source, manifest_one, output_one, include_original=False)
            first_model = json.loads((output_one / "models.jsonl").read_text(encoding="utf-8"))

            second_row = make_row("goku-modelo-b", 2)
            manifest_two = root / "two.jsonl"
            manifest_two.write_text(
                json.dumps(first_row) + "\n" + json.dumps(second_row) + "\n",
                encoding="utf-8",
            )
            output_two = root / "bundle-two"
            build_bundle(source, manifest_two, output_two, include_original=False)
            models = [json.loads(line) for line in (output_two / "models.jsonl").read_text(encoding="utf-8").splitlines()]
            same_model = next(model for model in models if model["id"] == first_model["id"])

            self.assertEqual(first_model["slug"], "dragon-ball-goku-modelo-a")
            self.assertEqual(same_model["slug"], first_model["slug"])

    def test_grouped_gallery_slug_does_not_change_when_cover_quality_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "OK - Games [2]" / "OK - God of War [2]" / "OK - Kratos [2]"
            model_dir.mkdir(parents=True)
            front = model_dir / "kratos-frente.webp"
            back = model_dir / "kratos-costas.webp"
            Image.new("RGB", (800, 1200), "#445566").save(front, "WEBP")
            Image.new("RGB", (900, 1350), "#556677").save(back, "WEBP")
            hierarchy = "Games / God of War / Kratos"
            public_key = f"{hierarchy} / kratos-modelo-01"

            def build(quality_front: float, quality_back: float, output_name: str):
                rows = [
                    {
                        "path": str(front.relative_to(source)), "size": front.stat().st_size,
                        "status": "OK", "canonical": True, "sha256": "7" * 64,
                        "width": 800, "height": 1200, "quality_score": quality_front,
                        "model_key": hierarchy, "public_model_key": public_key,
                        "audit_model_group": "kratos-modelo-01",
                    },
                    {
                        "path": str(back.relative_to(source)), "size": back.stat().st_size,
                        "status": "OK", "canonical": True, "sha256": "8" * 64,
                        "width": 900, "height": 1350, "quality_score": quality_back,
                        "model_key": hierarchy, "public_model_key": public_key,
                        "audit_model_group": "kratos-modelo-01",
                    },
                ]
                manifest = root / f"{output_name}.jsonl"
                manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                output = root / output_name
                build_bundle(source, manifest, output, include_original=False)
                return json.loads((output / "models.jsonl").read_text(encoding="utf-8"))

            front_cover = build(95.0, 70.0, "front-cover")
            back_cover = build(70.0, 95.0, "back-cover")

            self.assertEqual(front_cover["id"], back_cover["id"])
            self.assertEqual(front_cover["identityKey"], back_cover["identityKey"])
            self.assertEqual(front_cover["slug"], "god-of-war-kratos-modelo-01")
            self.assertEqual(back_cover["slug"], "god-of-war-kratos-modelo-01")
            self.assertEqual(front_cover["displayName"], "Kratos")
            self.assertEqual(back_cover["displayName"], "Kratos")
            self.assertNotEqual(front_cover["coverStorageKey"], back_cover["coverStorageKey"])

    def test_identity_drift_from_same_historical_sha_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Games" / "Saga" / "Heroi"
            model_dir.mkdir(parents=True)
            image_path = model_dir / "modelo.webp"
            Image.new("RGB", (640, 960), "#334455").save(image_path, "WEBP")
            hierarchy = "Games / Saga / Heroi"
            old_identity = f"{hierarchy} / modelo-antigo"
            new_identity = f"{hierarchy} / modelo-novo"
            manifest = root / "manifest.jsonl"
            output = root / "bundle"

            def write(identity: str) -> None:
                manifest.write_text(json.dumps({
                    "path": str(image_path.relative_to(source)),
                    "size": image_path.stat().st_size,
                    "status": "OK",
                    "canonical": True,
                    "sha256": "1" * 64,
                    "width": 640,
                    "height": 960,
                    "quality_score": 90.0,
                    "model_key": hierarchy,
                    "public_model_key": identity,
                    "audit_model_group": identity.rsplit(" / ", 1)[-1],
                }) + "\n", encoding="utf-8")

            write(old_identity)
            build_bundle(source, manifest, output, include_original=False)

            write(new_identity)
            with self.assertRaisesRegex(RuntimeError, "identity drift detectado"):
                build_bundle(source, manifest, output, include_original=False)

    def test_approved_identity_rename_preserves_technical_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Games" / "Saga" / "Heroi"
            model_dir.mkdir(parents=True)
            image_path = model_dir / "modelo.webp"
            Image.new("RGB", (640, 960), "#445566").save(image_path, "WEBP")
            hierarchy = "Games / Saga / Heroi"
            old_identity = f"{hierarchy} / modelo-antigo"
            new_identity = f"{hierarchy} / modelo-novo"
            manifest = root / "manifest.jsonl"
            output = root / "bundle"

            def row(identity: str) -> dict:
                return {
                    "path": str(image_path.relative_to(source)),
                    "size": image_path.stat().st_size,
                    "status": "OK",
                    "canonical": True,
                    "sha256": "2" * 64,
                    "width": 640,
                    "height": 960,
                    "quality_score": 90.0,
                    "model_key": hierarchy,
                    "public_model_key": identity,
                    "audit_model_group": identity.rsplit(" / ", 1)[-1],
                }

            manifest.write_text(json.dumps(row(old_identity)) + "\n", encoding="utf-8")
            build_bundle(source, manifest, output, include_original=False)
            original = json.loads((output / "models.jsonl").read_text(encoding="utf-8"))

            aliases = root / "identity-aliases.json"
            aliases.write_text(json.dumps({
                "version": 1,
                "aliases": [{
                    "canonicalIdentityKey": old_identity,
                    "aliases": [new_identity],
                    "reason": "renome auditado do mesmo produto",
                }],
            }), encoding="utf-8")
            manifest.write_text(json.dumps(row(new_identity)) + "\n", encoding="utf-8")

            summary = build_bundle(
                source,
                manifest,
                output,
                include_original=False,
                identity_alias_config=aliases,
            )
            renamed = json.loads((output / "models.jsonl").read_text(encoding="utf-8"))

            self.assertEqual(summary["identityAliasesApplied"], 1)
            self.assertEqual(renamed["id"], original["id"])
            self.assertEqual(renamed["code"], original["code"])
            self.assertEqual(renamed["identityKey"], old_identity)
            self.assertEqual(renamed["sourceIdentityKey"], new_identity)
            state = json.loads((output / "media-build-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["identityHistory"]["2" * 64], old_identity)


    def test_aliases_preserve_case_distinct_historical_ids_codes_and_slugs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Games" / "00 - Fliperama" / "Street Fighter" / "Ryu"
            model_dir.mkdir(parents=True)
            first = model_dir / "Ryu.png"
            second = model_dir / "ryu.webp"
            Image.new("RGB", (640, 960), "#334455").save(first)
            Image.new("RGB", (720, 1080), "#556677").save(second, "WEBP")

            hierarchy = "Games / 00 - Fliperama / Street Fighter / Ryu"
            historical = [
                f"{hierarchy} / Ryu",
                f"{hierarchy} / ryu",
            ]
            current = [
                f"{hierarchy} / ryu-modelo-01",
                f"{hierarchy} / ryu-modelo-02",
            ]
            rows = []
            for index, (path, identity) in enumerate(zip((first, second), current), 1):
                rows.append({
                    "path": str(path.relative_to(source)),
                    "size": path.stat().st_size,
                    "status": "OK",
                    "canonical": True,
                    "sha256": str(index) * 64,
                    "width": 640 if index == 1 else 720,
                    "height": 960 if index == 1 else 1080,
                    "quality_score": 80.0 + index,
                    "model_key": hierarchy,
                    "public_model_key": identity,
                    "audit_model_group": identity.rsplit(" / ", 1)[-1],
                })

            manifest = root / "manifest.jsonl"
            manifest.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            aliases = root / "identity-aliases.json"
            aliases.write_text(json.dumps({
                "version": 1,
                "aliases": [
                    {
                        "canonicalIdentityKey": historical_key,
                        "aliases": [current_key],
                        "reason": "continuidade de produto historicamente publicado",
                    }
                    for historical_key, current_key in zip(historical, current)
                ],
            }), encoding="utf-8")

            output = root / "bundle"
            summary = build_bundle(
                source,
                manifest,
                output,
                include_original=False,
                identity_alias_config=aliases,
            )
            models = [
                json.loads(line)
                for line in (output / "models.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            by_source = {model["sourceIdentityKey"]: model for model in models}

            self.assertEqual(summary["identityAliasesApplied"], 2)
            self.assertEqual(len(models), 2)
            self.assertEqual(len({model["slug"] for model in models}), 2)

            for historical_key, current_key in zip(historical, current):
                model = by_source[current_key]
                expected_id = stable_id("mdl", historical_key)
                expected_code = "TS-" + hashlib.sha256(
                    historical_key.encode("utf-8")
                ).hexdigest()[:12].upper()
                expected_slug = (
                    "street-fighter-ryu-"
                    + expected_id.split("_", 1)[1][:10]
                )
                expected_variant = f"Modelo {current_key.rsplit('-', 1)[-1]}"
                self.assertEqual(model["identityKey"], historical_key)
                self.assertEqual(model["id"], expected_id)
                self.assertEqual(model["code"], expected_code)
                self.assertEqual(model["slug"], expected_slug)
                self.assertEqual(model["variantName"], expected_variant)

    def test_two_current_identities_cannot_share_one_canonical_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Games" / "Saga" / "Heroi"
            model_dir.mkdir(parents=True)
            hierarchy = "Games / Saga / Heroi"
            canonical = f"{hierarchy} / modelo-antigo"
            aliases = [f"{hierarchy} / modelo-novo-a", f"{hierarchy} / modelo-novo-b"]
            rows = []
            for index, identity in enumerate(aliases, 1):
                image_path = model_dir / f"modelo-{index}.webp"
                Image.new("RGB", (640, 960), f"#{index + 2}45566").save(image_path, "WEBP")
                rows.append({
                    "path": str(image_path.relative_to(source)),
                    "size": image_path.stat().st_size,
                    "status": "OK",
                    "canonical": True,
                    "sha256": str(index + 2) * 64,
                    "width": 640,
                    "height": 960,
                    "quality_score": 80.0,
                    "model_key": hierarchy,
                    "public_model_key": identity,
                    "audit_model_group": identity.rsplit(" / ", 1)[-1],
                })
            manifest = root / "manifest.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            config = root / "identity-aliases.json"
            config.write_text(json.dumps({
                "version": 1,
                "aliases": [{
                    "canonicalIdentityKey": canonical,
                    "aliases": aliases,
                    "reason": "configuração propositalmente inválida para o snapshot atual",
                }],
            }), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "duas identidades atuais"):
                build_bundle(
                    source,
                    manifest,
                    root / "bundle",
                    include_original=False,
                    identity_alias_config=config,
                )

    def test_explicit_taxonomy_override_creates_nested_villain_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "OK - Animes & Desenhos [1]" / "OK - Clássicos [1]" / "OK - As Tartarugas Ninja [1]" / "OK - Destruidor [1]"
            model_dir.mkdir(parents=True)
            image_path = model_dir / "destruidor-01.jpg"
            Image.new("RGB", (640, 960), "#223344").save(image_path)
            hierarchy = "Animes & Desenhos / Clássicos / As Tartarugas Ninja / Destruidor"
            row = {
                "path": str(image_path.relative_to(source)), "size": image_path.stat().st_size,
                "status": "OK", "canonical": True, "sha256": "e" * 64,
                "width": 640, "height": 960, "quality_score": 70.0,
                "model_key": hierarchy, "public_model_key": f"{hierarchy} / {image_path.stem}",
            }
            manifest = root / "manifest.jsonl"
            manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
            output = root / "bundle"
            build_bundle(source, manifest, output, include_original=False)
            model = json.loads((output / "models.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(model["franchiseName"], "As Tartarugas Ninja")
            self.assertEqual(model["folderPath"], ["Vilões", "Destruidor"])
            self.assertEqual(model["folderPathKey"], "viloes/destruidor")
            self.assertEqual(model["collection"], "Vilões / Destruidor")
            self.assertEqual(model["displayName"], "Destruidor")
            self.assertIn("vilões", model["searchText"])
            self.assertIn("destruidor 01", model["searchText"])
            self.assertTrue(model["slug"].startswith("as-tartarugas-ninja-destruidor-01"))

    def test_tmnt_hero_stays_at_franchise_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "OK - Animes & Desenhos [1]" / "OK - Clássicos [1]" / "OK - As Tartarugas Ninja [1]" / "OK - Leonardo [1]"
            model_dir.mkdir(parents=True)
            image_path = model_dir / "leonardo-01.jpg"
            Image.new("RGB", (640, 960), "#335577").save(image_path)
            hierarchy = "Animes & Desenhos / Clássicos / As Tartarugas Ninja / Leonardo"
            row = {
                "path": str(image_path.relative_to(source)), "size": image_path.stat().st_size,
                "status": "OK", "canonical": True, "sha256": "f" * 64,
                "width": 640, "height": 960, "quality_score": 70.0,
                "model_key": hierarchy, "public_model_key": f"{hierarchy} / {image_path.stem}",
            }
            manifest = root / "manifest.jsonl"
            manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
            output = root / "bundle"
            build_bundle(source, manifest, output, include_original=False)
            model = json.loads((output / "models.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(model["folderPath"], ["Leonardo"])
            self.assertEqual(model["folderPathKey"], "leonardo")
            self.assertEqual(model["collection"], "Leonardo")
            self.assertEqual(model["displayName"], "Leonardo")
            self.assertIn("leonardo 01", model["searchText"])

    def test_taxonomy_config_rejects_unsafe_public_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "taxonomy.json"
            config.write_text(json.dumps({
                "version": 1,
                "franchises": {
                    "animes-desenhos/as-tartarugas-ninja": {
                        "pathOverrides": {
                            "Destruidor": ["Vilões", ".."]
                        }
                    }
                }
            }), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "segmento público inválido"):
                load_taxonomy_config(config)

    def test_source_hierarchy_rules_come_from_taxonomy_config(self) -> None:
        taxonomy = load_taxonomy_config()
        self.assertEqual(
            franchise_index(["Animes & Desenhos", "Clássicos", "As Tartarugas Ninja", "Leonardo"], taxonomy),
            2,
        )
        self.assertEqual(
            franchise_index(["Filmes & Séries", "01 - Ficção", "Alien", "Xenomorfo"], taxonomy),
            2,
        )
        self.assertEqual(
            franchise_index(["Games", "00 - Fliperama", "Street Fighter", "Ryu"], taxonomy),
            2,
        )

    def test_source_hierarchy_has_no_implicit_hardcoded_grouping(self) -> None:
        taxonomy = {"version": 1, "franchises": {}}
        self.assertEqual(
            franchise_index(["Animes & Desenhos", "Clássicos", "As Tartarugas Ninja", "Leonardo"], taxonomy),
            1,
        )

    def test_taxonomy_path_group_adds_public_parent_without_moving_other_members(self) -> None:
        taxonomy = {
            "version": 1,
            "franchises": {
                "animes-desenhos/as-tartarugas-ninja": {
                    "pathGroups": {"Vilões": ["Destruidor"]}
                }
            },
        }
        from build_media_bundle import public_folder_path
        self.assertEqual(
            public_folder_path("animes-desenhos", "as-tartarugas-ninja", ["Destruidor"], taxonomy),
            ["Vilões", "Destruidor"],
        )
        self.assertEqual(
            public_folder_path("animes-desenhos", "as-tartarugas-ninja", ["Leonardo"], taxonomy),
            ["Leonardo"],
        )

    def test_taxonomy_config_rejects_source_in_multiple_public_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "taxonomy.json"
            config.write_text(json.dumps({
                "version": 1,
                "franchises": {
                    "animes-desenhos/as-tartarugas-ninja": {
                        "pathGroups": {
                            "Vilões": ["Destruidor"],
                            "Chefes": ["Destruidor"],
                        }
                    }
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "múltiplos grupos"):
                load_taxonomy_config(config)

    def test_taxonomy_config_rejects_invalid_source_hierarchy_regex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "taxonomy.json"
            config.write_text(json.dumps({
                "version": 1,
                "sourceHierarchy": {
                    "categoryIntermediates": {
                        "filmes-series": {"pattern": "["}
                    }
                },
                "franchises": {},
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "pattern regex inválido"):
                load_taxonomy_config(config)

    def test_parallel_workers_keep_deterministic_model_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            rows = []
            for index, hero in enumerate(("Heroi A", "Heroi B"), 1):
                model_dir = source / "Games" / "Saga" / hero
                model_dir.mkdir(parents=True)
                image_path = model_dir / "vista.png"
                Image.new("RGB", (640, 960), f"#{index}{index}3344").save(image_path)
                rows.append({
                    "path": str(image_path.relative_to(source)), "size": image_path.stat().st_size,
                    "status": "OK", "canonical": True, "sha256": str(index) * 64,
                    "width": 640, "height": 960, "quality_score": 70.0,
                    "model_key": f"Games / Saga / {hero}",
                })
            manifest = root / "manifest.jsonl"
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            output_single = root / "single"
            output_parallel = root / "parallel"
            build_bundle(source, manifest, output_single, include_original=False, workers=1)
            build_bundle(source, manifest, output_parallel, include_original=False, workers=3)

            self.assertEqual(
                (output_single / "models.jsonl").read_text(encoding="utf-8"),
                (output_parallel / "models.jsonl").read_text(encoding="utf-8"),
            )

    def test_bundle_rejects_textually_equivalent_identity_keys_without_changing_id_algorithm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.jsonl"
            hierarchy = "Games / Pokémon / Pikachu"
            composed = f"{hierarchy} / Estátua"
            decomposed = "Games / Poke\u0301mon / Pikachu / Esta\u0301tua"
            rows = [
                {
                    "path": "Games/Pokémon/Pikachu/a.webp",
                    "size": 1,
                    "status": "OK",
                    "canonical": True,
                    "sha256": "a" * 64,
                    "width": 640,
                    "height": 960,
                    "quality_score": 80.0,
                    "model_key": hierarchy,
                    "public_model_key": composed,
                },
                {
                    "path": "Games/Pokémon/Pikachu/b.webp",
                    "size": 1,
                    "status": "OK",
                    "canonical": True,
                    "sha256": "b" * 64,
                    "width": 640,
                    "height": 960,
                    "quality_score": 79.0,
                    "model_key": hierarchy,
                    "public_model_key": decomposed,
                },
            ]
            manifest.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "textualmente equivalentes"):
                build_bundle(root / "catalog", manifest, root / "bundle", include_original=False)

    def test_built_model_identifier_collisions_fail_closed(self) -> None:
        base = {
            "identityKey": "Games / Saga / Produto A",
            "id": "mdl-a",
            "slug": "saga-produto-a",
            "code": "TS-AAAAAAAAAAAA",
        }
        distinct = {
            "identityKey": "Games / Saga / Produto B",
            "id": "mdl-b",
            "slug": "saga-produto-b",
            "code": "TS-BBBBBBBBBBBB",
        }

        validate_built_model_identifiers([base, distinct])

        for field in ("id", "slug", "code"):
            collision = dict(distinct)
            collision[field] = base[field]
            with self.subTest(field=field):
                with self.assertRaisesRegex(RuntimeError, f"colisão de {field}"):
                    validate_built_model_identifiers([base, collision])

    def test_taxonomy_slug_collisions_fail_closed(self) -> None:
        first = {
            "identityKey": "Games / Pokémon / Pikachu / A",
            "id": "mdl-a",
            "slug": "pokemon-pikachu-a",
            "code": "TS-AAAAAAAAAAAA",
            "categorySlug": "games",
            "categoryName": "Games",
            "franchiseSlug": "pokemon",
            "franchiseName": "Pokémon",
        }
        second = {
            **first,
            "identityKey": "Games / Pokemon / Pikachu / B",
            "id": "mdl-b",
            "slug": "pokemon-pikachu-b",
            "code": "TS-BBBBBBBBBBBB",
            "franchiseName": "Pokemon",
        }

        with self.assertRaisesRegex(RuntimeError, "slug de franquia"):
            validate_built_taxonomy_identifiers([first, second])

        bad_category = {
            **second,
            "franchiseName": "Pokémon",
            "categoryName": "Gámes",
        }
        with self.assertRaisesRegex(RuntimeError, "slug de categoria"):
            validate_built_taxonomy_identifiers([first, bad_category])

    def test_variant_disambiguation_blocks_visually_identical_cards(self) -> None:
        base = {
            "identityKey": "Games / Street Fighter / Cammy / modelo-a",
            "categorySlug": "games",
            "franchiseSlug": "street-fighter",
            "folderPathKey": "cammy",
            "displayName": "Cammy",
            "variantName": "Modelo 01",
        }
        distinct = {
            **base,
            "identityKey": "Games / Street Fighter / Cammy / modelo-b",
            "variantName": "Modelo 02",
        }

        validate_variant_disambiguation([base, distinct])

        no_variant = {**distinct, "variantName": ""}
        with self.assertRaisesRegex(RuntimeError, "indistinguíveis"):
            validate_variant_disambiguation([base, no_variant])

        same_variant = {**distinct, "variantName": "  modelo   01  "}
        with self.assertRaisesRegex(RuntimeError, "mesma variante pública"):
            validate_variant_disambiguation([base, same_variant])

    def test_include_original_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Filmes" / "Saga" / "Personagem"
            model_dir.mkdir(parents=True)
            image_path = model_dir / "vista.png"
            Image.new("RGB", (640, 960), "#202428").save(image_path)
            manifest = root / "manifest.jsonl"
            manifest.write_text(json.dumps({
                "path": str(image_path.relative_to(source)), "size": image_path.stat().st_size,
                "status": "OK", "canonical": True, "sha256": "d" * 64,
                "width": 640, "height": 960, "quality_score": 70.0,
                "model_key": "Filmes / Saga / Personagem",
            }) + "\n", encoding="utf-8")

            output = root / "bundle"
            build_bundle(source, manifest, output, include_original=True)
            model = json.loads((output / "models.jsonl").read_text(encoding="utf-8").strip())
            gallery = json.loads((output / "r2" / model["galleryManifestKey"]).read_text(encoding="utf-8"))
            original_key = gallery["images"][0]["variantKeys"]["original"]
            self.assertTrue((output / "r2" / original_key).is_file())

    def test_media_build_output_lock_blocks_concurrent_bundle_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "bundle"
            with media_build_output_lock(output):
                with self.assertRaisesRegex(RuntimeError, "já está em execução"):
                    build_bundle(
                        Path(tmp) / "missing-source",
                        Path(tmp) / "missing-manifest.jsonl",
                        output,
                        include_original=False,
                    )
                self.assertFalse((output / "media-build-state.json").exists())

            with media_build_output_lock(output):
                self.assertTrue((output / ".media-build.lock").is_file())

    def test_media_build_state_retries_transient_windows_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "media-build-state.json"
            original_replace = Path.replace
            attempts = {"count": 0}

            def flaky_replace(source: Path, target: Path):
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise PermissionError("arquivo temporariamente bloqueado")
                return original_replace(source, target)

            with patch.object(Path, "replace", flaky_replace), patch("build_media_bundle.time.sleep"):
                save_media_build_state(path, {"version": 1, "models": {"mdl": {}}})

            self.assertEqual(attempts["count"], 3)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 1)
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_media_build_state_fails_closed_when_lock_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "media-build-state.json"
            with patch.object(Path, "replace", side_effect=PermissionError("lock persistente")), patch("build_media_bundle.time.sleep"):
                with self.assertRaises(PermissionError):
                    save_media_build_state(path, {"version": 1, "models": {}})

            self.assertFalse(path.exists())
            self.assertTrue(path.with_suffix(path.suffix + ".tmp").exists())

    def test_media_build_resume_reuses_verified_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Games" / "Saga" / "Heroi"
            model_dir.mkdir(parents=True)
            image_path = model_dir / "vista.png"
            Image.new("RGB", (640, 960), "#445566").save(image_path)
            manifest = root / "manifest.jsonl"
            manifest.write_text(json.dumps({
                "path": str(image_path.relative_to(source)), "size": image_path.stat().st_size,
                "status": "OK", "canonical": True, "sha256": "9" * 64,
                "width": 640, "height": 960, "quality_score": 70.0,
                "model_key": "Games / Saga / Heroi",
            }) + "\n", encoding="utf-8")
            output = root / "bundle"

            first = build_bundle(source, manifest, output, include_original=False, workers=1)
            index_before = (output / "models.jsonl").read_text(encoding="utf-8")
            second = build_bundle(source, manifest, output, include_original=False, workers=1)

            self.assertEqual(first["builtModels"], 1)
            self.assertEqual(first["resumedModels"], 0)
            self.assertEqual(second["builtModels"], 0)
            self.assertEqual(second["resumedModels"], 1)
            self.assertEqual((output / "models.jsonl").read_text(encoding="utf-8"), index_before)
            self.assertTrue((output / "media-build-state.json").is_file())

    def test_media_build_resume_rebuilds_when_cached_output_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "catalog"
            model_dir = source / "Games" / "Saga" / "Heroi"
            model_dir.mkdir(parents=True)
            image_path = model_dir / "vista.png"
            Image.new("RGB", (640, 960), "#556677").save(image_path)
            manifest = root / "manifest.jsonl"
            manifest.write_text(json.dumps({
                "path": str(image_path.relative_to(source)), "size": image_path.stat().st_size,
                "status": "OK", "canonical": True, "sha256": "8" * 64,
                "width": 640, "height": 960, "quality_score": 70.0,
                "model_key": "Games / Saga / Heroi",
            }) + "\n", encoding="utf-8")
            output = root / "bundle"

            build_bundle(source, manifest, output, include_original=False, workers=1)
            model = json.loads((output / "models.jsonl").read_text(encoding="utf-8"))
            cover = output / "r2" / model["coverStorageKey"]
            cover.unlink()

            resumed = build_bundle(source, manifest, output, include_original=False, workers=1)

            self.assertEqual(resumed["builtModels"], 1)
            self.assertEqual(resumed["resumedModels"], 0)
            self.assertTrue(cover.is_file())


    def test_legacy_gallery_override_merges_current_source_into_canonical_group(self) -> None:
        hierarchy = "Games / League of Legends / Jinx"
        canonical = f"{hierarchy} / jinx-02"
        source = f"{hierarchy} / jinx-03"
        by_model = {
            canonical: [{"model_key": hierarchy, "public_model_key": canonical, "path": "Jinx/jinx-02.webp"}],
            source: [{"model_key": hierarchy, "public_model_key": source, "path": "Jinx/jinx-03.webp"}],
        }
        config = {"groups": [{
            "categorySlug": "games",
            "franchiseSlug": "league-of-legends",
            "folderPathKey": "jinx",
            "family": "league-of-legends-jinx-nomnom-modelo",
            "canonicalSlug": "league-of-legends-jinx-02",
            "memberSlugs": ["league-of-legends-jinx-02", "league-of-legends-jinx-03"],
        }]}
        summary = apply_legacy_gallery_overrides(
            by_model,
            {canonical: canonical, source: source},
            load_taxonomy_config(),
            config,
        )
        self.assertEqual(summary, {"groupsApplied": 1, "sourcesMerged": 1})
        self.assertEqual(list(by_model), [canonical])
        self.assertEqual(len(by_model[canonical]), 2)
        self.assertTrue(by_model[canonical][0]["legacy_gallery_canonical"])
        self.assertFalse(by_model[canonical][1]["legacy_gallery_canonical"])
        self.assertEqual(by_model[canonical][1]["model_key"], hierarchy)
        self.assertEqual(by_model[canonical][1]["public_model_key"], canonical)

    def test_legacy_gallery_override_fails_closed_when_source_exists_without_canonical(self) -> None:
        hierarchy = "Games / League of Legends / Jinx"
        source = f"{hierarchy} / jinx-03"
        by_model = {
            source: [{"model_key": hierarchy, "public_model_key": source, "path": "Jinx/jinx-03.webp"}],
        }
        config = {"groups": [{
            "categorySlug": "games",
            "franchiseSlug": "league-of-legends",
            "folderPathKey": "jinx",
            "family": "league-of-legends-jinx-nomnom-modelo",
            "canonicalSlug": "league-of-legends-jinx-02",
            "memberSlugs": ["league-of-legends-jinx-02", "league-of-legends-jinx-03"],
        }]}
        with self.assertRaisesRegex(RuntimeError, "sem canônico presente"):
            apply_legacy_gallery_overrides(
                by_model,
                {source: source},
                load_taxonomy_config(),
                config,
            )


if __name__ == "__main__":
    unittest.main()
