from __future__ import annotations

import unittest

from propose_external_identity_matches import propose_identity_matches


def model(
    identity: str,
    *,
    model_id: str,
    name: str,
    category: str = "Games",
    franchise: str = "Street Fighter",
    variant: str = "",
    source_identity: str | None = None,
) -> dict:
    return {
        "identityKey": identity,
        "sourceIdentityKey": source_identity or identity,
        "id": model_id,
        "categoryName": category,
        "franchiseName": franchise,
        "displayName": name,
        "variantName": variant,
    }


def existing_image(
    identity: str,
    sha: str,
    dhash: str,
    *,
    path: str = "catalogo/modelo.webp",
) -> dict:
    return {
        "path": path,
        "status": "OK",
        "canonical": True,
        "sha256": sha,
        "dhash": dhash,
        "quality_score": 80,
        "width": 1000,
        "height": 1400,
        "size": 100_000,
        "model_key": identity,
        "public_model_key": identity,
    }


def external_image(
    incoming: str,
    sha: str,
    dhash: str,
    *,
    name: str = "Ryu",
    category: str = "Games",
    franchise: str = "Street Fighter",
    product_key: str = "vendor-a:product:42",
    path: str = "vendor-a/product-42/1.webp",
) -> dict:
    return {
        "path": path,
        "status": "OK",
        "canonical": True,
        "sha256": sha,
        "dhash": dhash,
        "quality_score": 90,
        "width": 1600,
        "height": 2200,
        "size": 180_000,
        "category": category,
        "franchise": franchise,
        "model_key": incoming,
        "public_model_key": incoming,
        "identification": name,
        "source_provenance": {
            "kind": "AUTHORIZED_EXTERNAL_MEDIA",
            "sourceProductKey": product_key,
        },
    }


class ExternalIdentityMatchTests(unittest.TestCase):
    def test_exact_name_plus_visual_evidence_orders_one_review_candidate(self) -> None:
        target = "Games / Street Fighter / Ryu / ryu"
        incoming = "vendor-a / product:42"
        result = propose_identity_matches(
            [model(target, model_id="mdl-ryu", name="Ryu")],
            [existing_image(target, "a" * 64, "0000000000000000")],
            [external_image(incoming, "b" * 64, "0000000000000001")],
        )

        self.assertEqual(result["summary"]["candidate"], 1)
        self.assertEqual(result["summary"]["automaticMappings"], 0)
        product = result["products"][0]
        self.assertEqual(product["reviewState"], "candidate")
        self.assertEqual(product["topTierCount"], 1)
        candidate = product["candidates"][0]
        self.assertEqual(candidate["targetModel"], target)
        self.assertEqual(candidate["reviewPriority"], "strong_name_plus_visual_review")
        self.assertTrue(candidate["evidence"]["exactName"])
        self.assertEqual(candidate["evidence"]["minimumDhashDistance"], 1)
        self.assertTrue(result["policy"]["reviewOnly"])

    def test_same_display_name_across_variants_is_ambiguous_without_visual_evidence(self) -> None:
        incoming = "vendor-a / product:42"
        principal = "Games / Street Fighter / Ryu / ryu"
        variant = "Games / Street Fighter / Ryu / ryu-modelo-02"
        result = propose_identity_matches(
            [
                model(principal, model_id="mdl-a", name="Ryu", variant="Principal"),
                model(variant, model_id="mdl-b", name="Ryu", variant="Modelo 02"),
            ],
            [],
            [external_image(incoming, "f" * 64, "aaaaaaaaaaaaaaaa")],
        )

        product = result["products"][0]
        self.assertEqual(product["reviewState"], "ambiguous_candidate")
        self.assertEqual(product["topTierCount"], 2)
        self.assertEqual(len(product["candidates"]), 2)
        self.assertTrue(all(item["reviewPriority"] == "name_review" for item in product["candidates"]))

    def test_variant_label_can_disambiguate_same_display_name_for_review(self) -> None:
        incoming = "vendor-a / product:42"
        principal = "Games / Street Fighter / Ryu / ryu"
        variant = "Games / Street Fighter / Ryu / ryu-modelo-02"
        result = propose_identity_matches(
            [
                model(principal, model_id="mdl-a", name="Ryu", variant="Principal"),
                model(variant, model_id="mdl-b", name="Ryu", variant="Modelo 02"),
            ],
            [],
            [
                external_image(
                    incoming,
                    "0" * 64,
                    "abababababababab",
                    name="Ryu Modelo 02",
                )
            ],
        )

        product = result["products"][0]
        self.assertEqual(product["reviewState"], "candidate")
        self.assertEqual(product["topTierCount"], 1)
        self.assertEqual(len(product["candidates"]), 1)
        self.assertEqual(product["candidates"][0]["targetModel"], variant)
        self.assertEqual(product["candidates"][0]["reviewPriority"], "name_review")

    def test_exact_sha_shared_by_two_models_remains_ambiguous_review_only(self) -> None:
        incoming = "vendor-a / product:42"
        first = "Games / Street Fighter / Diorama / modelo-a"
        second = "Games / Street Fighter / Diorama / modelo-b"
        digest = "c" * 64
        result = propose_identity_matches(
            [
                model(first, model_id="mdl-a", name="Cena A"),
                model(second, model_id="mdl-b", name="Cena B"),
            ],
            [
                existing_image(first, digest, "0000000000000000", path="catalogo/a.webp"),
                existing_image(second, digest, "0000000000000000", path="catalogo/b.webp"),
            ],
            [external_image(incoming, digest, "0000000000000000", name="Cena Externa")],
        )

        product = result["products"][0]
        self.assertEqual(product["reviewState"], "ambiguous_candidate")
        self.assertEqual(product["topTierCount"], 2)
        self.assertTrue(
            all(item["reviewPriority"] == "strong_exact_sha_review" for item in product["candidates"])
        )
        self.assertEqual(result["policy"]["automaticMappings"], 0)

    def test_same_name_in_different_franchise_is_not_a_candidate(self) -> None:
        target = "Games / Mortal Kombat / Ryu / ryu"
        result = propose_identity_matches(
            [model(target, model_id="mdl-x", name="Ryu", franchise="Mortal Kombat")],
            [],
            [external_image("vendor-a / product:42", "d" * 64, "1111111111111111")],
        )

        product = result["products"][0]
        self.assertEqual(product["reviewState"], "no_candidate")
        self.assertEqual(product["candidates"], [])

    def test_exact_sha_outside_scope_is_reported_as_conflict_not_mapping_candidate(self) -> None:
        target = "Filmes & Séries / Outra / Ryu / ryu"
        digest = "e" * 64
        result = propose_identity_matches(
            [
                model(
                    target,
                    model_id="mdl-x",
                    name="Ryu",
                    category="Filmes & Séries",
                    franchise="Outra",
                )
            ],
            [existing_image(target, digest, "2222222222222222")],
            [external_image("vendor-a / product:42", digest, "2222222222222222")],
        )

        product = result["products"][0]
        self.assertEqual(product["reviewState"], "no_candidate")
        self.assertEqual(product["candidates"], [])
        self.assertEqual(product["scopeConflicts"], [{"sha256": digest, "targetModel": target}])
        self.assertEqual(result["summary"]["scopeConflict"], 1)

    def test_visual_only_candidate_is_reported_but_never_mapped(self) -> None:
        target = "Games / Street Fighter / Ken / ken"
        result = propose_identity_matches(
            [model(target, model_id="mdl-ken", name="Ken")],
            [existing_image(target, "1" * 64, "0000000000000000")],
            [external_image("vendor-a / product:42", "2" * 64, "0000000000000003", name="Unknown Hero")],
        )

        candidate = result["products"][0]["candidates"][0]
        self.assertEqual(candidate["reviewPriority"], "visual_review")
        self.assertFalse(candidate["evidence"]["exactName"])
        self.assertEqual(result["summary"]["automaticMappings"], 0)

    def test_source_identity_key_links_existing_manifest_to_canonical_model(self) -> None:
        canonical = "Games / Street Fighter / Ryu / ryu"
        source = "Games / Street Fighter / Ryu / ryu-old"
        result = propose_identity_matches(
            [
                model(
                    canonical,
                    model_id="mdl-ryu",
                    name="Ryu",
                    source_identity=source,
                )
            ],
            [existing_image(source, "3" * 64, "3333333333333333")],
            [external_image("vendor-a / product:42", "4" * 64, "3333333333333332")],
        )

        self.assertEqual(result["products"][0]["candidates"][0]["targetModel"], canonical)

    def test_unresolved_existing_image_identity_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "identidades ausentes"):
            propose_identity_matches(
                [model("Games / Street Fighter / Ryu / ryu", model_id="mdl-ryu", name="Ryu")],
                [existing_image("Games / Street Fighter / Ausente / ausente", "5" * 64, "0" * 16)],
                [external_image("vendor-a / product:42", "6" * 64, "1" * 16)],
            )

    def test_external_manifest_without_authorized_provenance_fails_closed(self) -> None:
        row = external_image("vendor-a / product:42", "7" * 64, "2" * 16)
        row["source_provenance"] = {"kind": "OTHER"}
        with self.assertRaisesRegex(RuntimeError, "provenance externa autorizada"):
            propose_identity_matches(
                [model("Games / Street Fighter / Ryu / ryu", model_id="mdl-ryu", name="Ryu")],
                [],
                [row],
            )

    def test_no_text_or_visual_evidence_stays_no_candidate(self) -> None:
        target = "Games / Street Fighter / Ken / ken"
        result = propose_identity_matches(
            [model(target, model_id="mdl-ken", name="Ken")],
            [existing_image(target, "8" * 64, "0000000000000000")],
            [external_image("vendor-a / product:42", "9" * 64, "ffffffffffffffff", name="Chun Li")],
        )

        self.assertEqual(result["products"][0]["reviewState"], "no_candidate")
        self.assertEqual(result["summary"]["noCandidate"], 1)


if __name__ == "__main__":
    unittest.main()
