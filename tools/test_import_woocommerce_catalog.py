from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

MODULE_PATH = TOOLS_DIR / "import_woocommerce_catalog.py"
SPEC = importlib.util.spec_from_file_location("import_woocommerce_catalog", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

ImportContractError = module.ImportContractError
build_inventory = module.build_inventory
load_source_config = module.load_source_config
normalize_product = module.normalize_product


class ImportWooCommerceCatalogTests(unittest.TestCase):
    SOURCE_ID = "vendor-a"

    def setUp(self) -> None:
        self.source = {
            "origin": "https://vendor.example",
            "productPathPrefixes": ["/product/"],
            "allowedImageHosts": ["vendor.example", "cdn.vendor.example"],
            "taxonomySignals": {
                "Anime": "Animes & Desenhos",
                "Videogames": "Games",
                "Movies": "Filmes & Séries",
                "Series": "Filmes & Séries",
                "Marvel": "Marvel & DC",
                "DC": "Marvel & DC",
            },
            "nonFranchiseTerms": [
                "Anime", "Videogames", "Comics", "Fantasy", "Movies", "Series",
                "Horror", "Premium", "Diamond Tier", "NSFW", "Packs",
                "Catalog Vendor", "Template Test",
            ],
            "featureTerms": {
                "nsfw": "NSFW",
                "bust": "Bust",
                "busto": "Bust",
                "extras": "Extras",
                "extra content": "Extras",
                "pre-supported": "Pre-Supported",
                "uncut/merged": "Uncut/Merged",
                "split pieces": "Split Pieces",
                "premium": "Premium",
                "diamond tier": "Diamond Tier",
            },
        }

    @staticmethod
    def images(count: int, stem: str = "juri") -> list[dict]:
        return [
            {
                "id": 1000 + index,
                "src": f"https://cdn.vendor.example/uploads/{stem}-{index}.webp",
                "alt": f"{stem} view {index}",
            }
            for index in range(1, count + 1)
        ]

    @staticmethod
    def product_url(slug: str) -> str:
        return f"https://vendor.example/product/{slug}/"

    def product(self, *, product_id: int = 42, slug: str = "juri-han-stl-street-fighter") -> dict:
        return {
            "id": product_id,
            "name": "Juri Han STL – Street Fighter",
            "slug": slug,
            "permalink": self.product_url(slug),
            "categories": [
                {"name": "Street Fighter"},
                {"name": "NSFW"},
                {"name": "Videogames"},
            ],
            "tags": [{"name": "1/6 Scale"}, {"name": "Bust"}, {"name": "NSFW"}],
            "images": self.images(7),
        }

    def test_one_product_with_seven_images_stays_one_incoming_model(self) -> None:
        row, images = normalize_product(
            self.product(), source_id=self.SOURCE_ID, source=self.source
        )

        self.assertEqual(row["displayName"], "Juri Han")
        self.assertEqual(row["canonicalCategoryCandidate"], "Games")
        self.assertEqual(row["franchiseCandidate"], "Street Fighter")
        self.assertEqual(row["features"], ["Bust", "NSFW"])
        self.assertEqual(row["sourceProductId"], "42")
        self.assertEqual(row["sourceProductKey"], "vendor-a:product:42")
        self.assertEqual(row["incomingModel"], "vendor-a / product:42")
        self.assertEqual(row["sourceSlug"], "juri-han-stl-street-fighter")
        self.assertEqual(row["imageCount"], 7)
        self.assertTrue(row["readyForMaterialization"])
        self.assertEqual(len(images), 7)
        self.assertEqual(
            {image["incomingModel"] for image in images},
            {"vendor-a / product:42"},
        )
        self.assertEqual(
            images[0]["targetPath"],
            "vendor-a/product-42/1001.webp",
        )
        self.assertEqual(images[0]["sourceImageIdentityKind"], "provider_id")

    def test_product_identity_survives_slug_and_permalink_rename(self) -> None:
        before = self.product(product_id=42, slug="juri-old-slug")
        after = self.product(product_id=42, slug="juri-new-slug")

        before_row, before_images = normalize_product(
            before, source_id=self.SOURCE_ID, source=self.source
        )
        after_row, after_images = normalize_product(
            after, source_id=self.SOURCE_ID, source=self.source
        )

        self.assertEqual(before_row["sourceProductKey"], after_row["sourceProductKey"])
        self.assertEqual(before_row["incomingModel"], after_row["incomingModel"])
        self.assertNotEqual(before_row["sourceSlug"], after_row["sourceSlug"])
        self.assertEqual(
            {row["targetPath"] for row in before_images},
            {row["targetPath"] for row in after_images},
        )

    def test_missing_or_invalid_product_id_fails_closed(self) -> None:
        for bad_id in (None, "", 0, -1, True, "abc"):
            product = self.product()
            product["id"] = bad_id
            with self.subTest(bad_id=bad_id):
                with self.assertRaisesRegex(ImportContractError, "id estável positivo"):
                    normalize_product(product, source_id=self.SOURCE_ID, source=self.source)

    def test_url_hash_image_fallback_is_order_independent(self) -> None:
        product = self.product()
        product["images"] = [
            {"src": "https://cdn.vendor.example/uploads/no-id-a.webp", "alt": "A"},
            {"src": "https://cdn.vendor.example/uploads/no-id-b.webp", "alt": "B"},
        ]
        first_row, first = normalize_product(product, source_id=self.SOURCE_ID, source=self.source)
        product["images"] = list(reversed(product["images"]))
        second_row, second = normalize_product(product, source_id=self.SOURCE_ID, source=self.source)

        self.assertEqual(first_row["sourceProductKey"], second_row["sourceProductKey"])
        first_by_url = {
            row["sourceUrl"]: (row["sourceImageId"], row["targetPath"])
            for row in first
        }
        second_by_url = {
            row["sourceUrl"]: (row["sourceImageId"], row["targetPath"])
            for row in second
        }
        self.assertEqual(first_by_url, second_by_url)
        self.assertTrue(all(row["sourceImageIdentityKind"] == "url_sha256" for row in first))

    def test_duplicate_provider_image_id_is_rejected(self) -> None:
        product = self.product()
        product["images"] = [
            {"id": 77, "src": "https://cdn.vendor.example/uploads/a.webp"},
            {"id": 77, "src": "https://cdn.vendor.example/uploads/b.webp"},
        ]
        with self.assertRaisesRegex(ImportContractError, "identidade de imagem duplicada"):
            normalize_product(product, source_id=self.SOURCE_ID, source=self.source)

    def test_title_features_are_removed_from_display_name_but_preserved_as_features(self) -> None:
        product = {
            "id": 50,
            "name": "Cynthia STL + Bust + NSFW – Pokemon",
            "slug": "cynthia-stl-bust-nsfw-pokemon",
            "permalink": self.product_url("cynthia-stl-bust-nsfw-pokemon"),
            "categories": [
                {"name": "Pokemon"},
                {"name": "Anime"},
                {"name": "NSFW"},
                {"name": "Premium"},
            ],
            "tags": [{"name": "Bust"}, {"name": "NSFW"}, {"name": "Pre-Supported"}],
            "images": self.images(3, "cynthia"),
        }
        row, images = normalize_product(product, source_id=self.SOURCE_ID, source=self.source)
        self.assertEqual(row["displayName"], "Cynthia")
        self.assertEqual(row["canonicalCategoryCandidate"], "Animes & Desenhos")
        self.assertEqual(row["franchiseCandidate"], "Pokemon")
        self.assertEqual(row["features"], ["Bust", "NSFW", "Pre-Supported", "Premium"])
        self.assertEqual(len(images), 3)

    def test_conflicting_source_taxonomy_is_kept_for_review_not_guessed(self) -> None:
        product = {
            "id": 60,
            "name": "Lucy and David Diorama – Cyberpunk",
            "slug": "lucy-and-david-diorama-cyberpunk",
            "permalink": self.product_url("lucy-and-david-diorama-cyberpunk"),
            "categories": [
                {"name": "Cyberpunk"}, {"name": "Series"}, {"name": "Videogames"}
            ],
            "tags": [],
            "images": self.images(2, "cyberpunk"),
        }
        row, _ = normalize_product(product, source_id=self.SOURCE_ID, source=self.source)
        self.assertIsNone(row["canonicalCategoryCandidate"])
        self.assertEqual(row["categoryResolution"], "review_conflict")
        self.assertFalse(row["readyForMaterialization"])
        self.assertIn("review_conflict", row["reviewReasons"])

    def test_marvel_signal_can_also_be_the_franchise(self) -> None:
        product = {
            "id": 70,
            "name": "Hela STL Queen in Black + Bust + NSFW – Marvel",
            "slug": "hela-stl-queen-in-black-marvel",
            "permalink": self.product_url("hela-stl-queen-in-black-marvel"),
            "categories": [
                {"name": "Marvel"}, {"name": "Comics"}, {"name": "Premium"}
            ],
            "tags": [{"name": "Bust"}, {"name": "NSFW"}],
            "images": self.images(4, "hela"),
        }
        row, _ = normalize_product(product, source_id=self.SOURCE_ID, source=self.source)
        self.assertEqual(row["canonicalCategoryCandidate"], "Marvel & DC")
        self.assertEqual(row["franchiseCandidate"], "Marvel")
        self.assertTrue(row["readyForMaterialization"])

    def test_off_origin_image_is_blocked_fail_closed(self) -> None:
        product = {
            "id": 80,
            "name": "Ryu STL – Street Fighter",
            "slug": "ryu-stl-street-fighter",
            "permalink": self.product_url("ryu-stl-street-fighter"),
            "categories": [{"name": "Street Fighter"}, {"name": "Videogames"}],
            "tags": [],
            "images": [{"id": 1, "src": "https://untrusted.example/ryu.webp", "alt": "Ryu"}],
        }
        with self.assertRaisesRegex(ImportContractError, "host de imagem não autorizado"):
            normalize_product(product, source_id=self.SOURCE_ID, source=self.source)

    def test_duplicate_product_id_is_rejected_even_when_slug_changes(self) -> None:
        base = {
            "id": 90,
            "name": "Ryu STL – Street Fighter",
            "slug": "ryu-stl-street-fighter",
            "permalink": self.product_url("ryu-stl-street-fighter"),
            "categories": [{"name": "Street Fighter"}, {"name": "Videogames"}],
            "tags": [],
            "images": self.images(1, "ryu"),
        }
        duplicate = dict(base)
        duplicate["slug"] = "ryu-renamed"
        duplicate["permalink"] = self.product_url("ryu-renamed")
        duplicate["images"] = self.images(1, "ryu-renamed")

        with self.assertRaisesRegex(ImportContractError, "produto duplicado"):
            build_inventory([base, duplicate], source_id=self.SOURCE_ID, source=self.source)

    def test_summary_counts_nsfw_products_and_images(self) -> None:
        juri = self.product(product_id=100, slug="juri-han-stl-street-fighter")
        ryu = {
            "id": 101,
            "name": "Ryu STL – Street Fighter",
            "slug": "ryu-stl-street-fighter",
            "permalink": self.product_url("ryu-stl-street-fighter"),
            "categories": [{"name": "Street Fighter"}, {"name": "Videogames"}],
            "tags": [],
            "images": self.images(2, "ryu"),
        }
        products, images, summary = build_inventory(
            [juri, ryu], source_id=self.SOURCE_ID, source=self.source
        )
        self.assertEqual(len(products), 2)
        self.assertEqual(len(images), 9)
        self.assertEqual(summary["products"], 2)
        self.assertEqual(summary["images"], 9)
        self.assertEqual(summary["nsfwProducts"], 1)
        self.assertEqual(summary["readyForMaterialization"], 2)
        self.assertEqual(summary["networkDownloads"], 0)

    def test_config_rejects_taxonomy_target_outside_public_ssot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            bad = {
                "version": 1,
                "sources": {
                    "bad": {
                        "origin": "https://example.com",
                        "allowedImageHosts": ["example.com"],
                        "taxonomySignals": {"Anime": "Inventada"},
                    }
                },
            }
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ImportContractError, "fora do SSOT público"):
                load_source_config(path, "bad")


if __name__ == "__main__":
    unittest.main()
