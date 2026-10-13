from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

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


def run_match(
    models: list[dict],
    existing: list[dict],
    incoming: list[dict],
    *,
    ownership: dict[str, list[str]] | None = None,
) -> dict:
    models = [dict(item) for item in models]
    existing = [dict(item) for item in existing]
    resolved_ownership = {key: list(value) for key, value in (ownership or {}).items()}
    if ownership is None:
        for row in existing:
            identity = str(row.get("public_model_key") or row.get("model_key") or "")
            if identity:
                resolved_ownership.setdefault(identity, []).append(str(row["sha256"]))

    with tempfile.TemporaryDirectory() as tmp:
        r2_root = Path(tmp) / "r2"
        for index, item in enumerate(models, 1):
            identity = item["identityKey"]
            shas = resolved_ownership.get(identity)
            if shas is None:
                digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                shas = [digest]
                existing.append(
                    existing_image(
                        identity,
                        digest,
                        "f0f0f0f0f0f0f0f0",
                        path=f"catalogo/synthetic-{index}.webp",
                    )
                )
            key = f"gallery/{item['id']}/manifest.json"
            item["galleryManifestKey"] = key
            item["imageCount"] = len(shas)
            path = r2_root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "modelId": item["id"],
                        "images": [{"sourceSha256": digest} for digest in shas],
                    }
                ),
                encoding="utf-8",
            )

        return propose_identity_matches(
            models,
            existing,
            incoming,
            existing_r2_root=r2_root,
        )


class ExternalIdentityMatchTests(unittest.TestCase):
    def test_exact_name_plus_visual_evidence_orders_one_review_candidate(self) -> None:
        target = "Games / Street Fighter / Ryu / ryu"
        incoming = "vendor-a / product:42"
        result = run_match(
            [model(target, model_id="mdl-ryu", name="Ryu")],
            [existing_image(target, "a" * 64, "0000000000000000")],
            [external_image(incoming, "b" * 64, "0000000000000001")],
        )

        self.assertEqual(result["summary"]["candidate"], 1)
        self.assertEqual(result["summary"]["automaticMappings"], 0)
        self.assertEqual(
            result["policy"]["existingImageOwnership"],
            "canonical_bundle_gallery_manifest",
        )
        product = result["products"][0]
        self.assertEqual(product["reviewState"], "candidate")
        candidate = product["candidates"][0]
        self.assertEqual(candidate["targetModel"], target)
        self.assertEqual(candidate["reviewPriority"], "strong_name_plus_visual_review")
        self.assertEqual(candidate["evidence"]["minimumDhashDistance"], 1)

    def test_same_display_name_across_variants_is_ambiguous_without_visual_evidence(self) -> None:
        incoming = "vendor-a / product:42"
        principal = "Games / Street Fighter / Ryu / ryu"
        variant = "Games / Street Fighter / Ryu / ryu-modelo-02"
        result = run_match(
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

    def test_variant_label_can_disambiguate_same_display_name_for_review(self) -> None:
        incoming = "vendor-a / product:42"
        principal = "Games / Street Fighter / Ryu / ryu"
        variant = "Games / Street Fighter / Ryu / ryu-modelo-02"
        result = run_match(
            [
                model(principal, model_id="mdl-a", name="Ryu", variant="Principal"),
                model(variant, model_id="mdl-b", name="Ryu", variant="Modelo 02"),
            ],
            [],
            [external_image(incoming, "0" * 64, "abababababababab", name="Ryu Modelo 02")],
        )

        product = result["products"][0]
        self.assertEqual(product["reviewState"], "candidate")
        self.assertEqual(len(product["candidates"]), 1)
        self.assertEqual(product["candidates"][0]["targetModel"], variant)

    def test_exact_sha_shared_by_two_models_remains_ambiguous_review_only(self) -> None:
        incoming = "vendor-a / product:42"
        first = "Games / Street Fighter / Diorama / modelo-a"
        second = "Games / Street Fighter / Diorama / modelo-b"
        digest = "c" * 64
        raw = existing_image(first, digest, "0000000000000000", path="catalogo/shared.webp")
        result = run_match(
            [
                model(first, model_id="mdl-a", name="Cena A"),
                model(second, model_id="mdl-b", name="Cena B"),
            ],
            [raw],
            [external_image(incoming, digest, "0000000000000000", name="Cena Externa")],
            ownership={first: [digest], second: [digest]},
        )

        product = result["products"][0]
        self.assertEqual(product["reviewState"], "ambiguous_candidate")
        self.assertEqual(product["topTierCount"], 2)
        self.assertTrue(
            all(item["reviewPriority"] == "strong_exact_sha_review" for item in product["candidates"])
        )

    def test_same_name_in_different_franchise_is_not_a_candidate(self) -> None:
        target = "Games / Mortal Kombat / Ryu / ryu"
        result = run_match(
            [model(target, model_id="mdl-x", name="Ryu", franchise="Mortal Kombat")],
            [],
            [external_image("vendor-a / product:42", "d" * 64, "1111111111111111")],
        )
        self.assertEqual(result["products"][0]["reviewState"], "no_candidate")

    def test_exact_sha_outside_scope_is_reported_as_conflict_not_mapping_candidate(self) -> None:
        target = "Filmes & Séries / Outra / Ryu / ryu"
        digest = "e" * 64
        result = run_match(
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
        self.assertEqual(product["scopeConflicts"], [{"sha256": digest, "targetModel": target}])

    def test_visual_only_candidate_is_reported_but_never_mapped(self) -> None:
        target = "Games / Street Fighter / Ken / ken"
        result = run_match(
            [model(target, model_id="mdl-ken", name="Ken")],
            [existing_image(target, "1" * 64, "0000000000000000")],
            [external_image("vendor-a / product:42", "2" * 64, "0000000000000003", name="Unknown Hero")],
        )
        candidate = result["products"][0]["candidates"][0]
        self.assertEqual(candidate["reviewPriority"], "visual_review")
        self.assertEqual(result["summary"]["automaticMappings"], 0)

    def test_legacy_source_identity_uses_canonical_bundle_owner(self) -> None:
        canonical = "Games / Street Fighter / Ryu / ryu"
        legacy_source = "Games / Street Fighter / Ryu / ryu-old"
        digest = "3" * 64
        result = run_match(
            [model(canonical, model_id="mdl-ryu", name="Ryu", source_identity=legacy_source)],
            [existing_image(legacy_source, digest, "3333333333333333")],
            [external_image("vendor-a / product:42", "4" * 64, "3333333333333332")],
            ownership={canonical: [digest]},
        )
        self.assertEqual(result["products"][0]["candidates"][0]["targetModel"], canonical)

    def test_bundle_sha_missing_from_ingest_manifest_fails_closed(self) -> None:
        canonical = "Games / Street Fighter / Ryu / ryu"
        unrelated = "Games / Street Fighter / Ken / ken"
        with self.assertRaisesRegex(RuntimeError, "SHA do bundle ausente"):
            run_match(
                [model(canonical, model_id="mdl-ryu", name="Ryu")],
                [existing_image(unrelated, "a" * 64, "0101010101010101")],
                [external_image("vendor-a / product:42", "6" * 64, "1" * 16)],
                ownership={canonical: ["5" * 64]},
            )

    def test_empty_ingest_manifest_fails_closed_before_bundle_lookup(self) -> None:
        canonical = "Games / Street Fighter / Ryu / ryu"
        with self.assertRaisesRegex(RuntimeError, "manifesto atual"):
            run_match(
                [model(canonical, model_id="mdl-ryu", name="Ryu")],
                [],
                [external_image("vendor-a / product:42", "6" * 64, "1" * 16)],
                ownership={canonical: ["5" * 64]},
            )

    def test_external_manifest_without_authorized_provenance_fails_closed(self) -> None:
        target = "Games / Street Fighter / Ryu / ryu"
        row = external_image("vendor-a / product:42", "7" * 64, "2" * 16)
        row["source_provenance"] = {"kind": "OTHER"}
        with self.assertRaisesRegex(RuntimeError, "provenance externa autorizada"):
            run_match([model(target, model_id="mdl-ryu", name="Ryu")], [], [row])

    def test_no_text_or_visual_evidence_stays_no_candidate(self) -> None:
        target = "Games / Street Fighter / Ken / ken"
        result = run_match(
            [model(target, model_id="mdl-ken", name="Ken")],
            [existing_image(target, "8" * 64, "0000000000000000")],
            [external_image("vendor-a / product:42", "9" * 64, "ffffffffffffffff", name="Chun Li")],
        )
        self.assertEqual(result["products"][0]["reviewState"], "no_candidate")
        self.assertEqual(result["summary"]["noCandidate"], 1)


if __name__ == "__main__":
    unittest.main()
