#!/usr/bin/env python3
"""Safely enrich an existing gallery anchored by an exact published source SHA.

The current cover object is reused byte-for-byte from the active production
manifest. Only reviewed add_view sources are rendered into new media objects.
The canonical exact-source preflight is regenerated at execution time and must
match a versioned authorization. R2 objects are written/read back first; D1 is
updated last with optimistic predicates. Dry-run is the default and no DELETE is
issued anywhere.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from apply_existing_gallery_promotion import (
    build_d1_statements,
    fetch_current_sources,
    fetch_model_state,
    fetch_other_sha_owners,
    is_candidate_current,
    publish_bundle_objects,
    read_json_object,
    require_env,
    validate_current_manifest,
    validate_current_state,
    d1_request,
)
from build_media_bundle import gallery_identity, render_variants, stable_id
from preflight_exact_source_gallery_evidence import (
    run_preflight,
    validate_evidence,
)
from preflight_existing_gallery_evidence import (
    decode_b64_text,
    extension,
    get_r2_object,
    image_metrics,
    load_json,
    staging_key,
    validate_sha,
)


def gallery_payload_with_reused_cover(
    target_model: str,
    hierarchy: list[str],
    current_cover: dict[str, Any],
    anchor_sha: str,
    new_images: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(current_cover, dict) or current_cover.get("role") != "cover":
        raise RuntimeError("manifesto atual não possui capa reutilizável")
    if validate_sha(current_cover.get("sourceSha256"), "current cover sourceSha256") != anchor_sha:
        raise RuntimeError("capa atual não corresponde à âncora exact-source")
    variants = current_cover.get("variantKeys")
    if not isinstance(variants, dict) or not str(variants.get("card") or "").strip():
        raise RuntimeError("capa atual sem card reutilizável")
    if not hierarchy or any(not isinstance(item, str) or not item for item in hierarchy):
        raise RuntimeError("sourceHierarchy inválida")

    cover = copy.deepcopy(current_cover)
    images = [cover]
    seen_shas = {anchor_sha}
    seen_ids = {str(cover.get("id") or "")}
    if not next(iter(seen_ids)):
        raise RuntimeError("capa atual sem image id")
    for image in new_images:
        if not isinstance(image, dict):
            raise RuntimeError("nova imagem inválida")
        item = copy.deepcopy(image)
        item["role"] = "gallery"
        sha = validate_sha(item.get("sourceSha256"), "new sourceSha256")
        image_id = str(item.get("id") or "").strip()
        if sha in seen_shas or not image_id or image_id in seen_ids:
            raise RuntimeError("nova vista colide com a capa ou outra vista")
        seen_shas.add(sha)
        seen_ids.add(image_id)
        images.append(item)
    return {"modelId": target_model, "sourceHierarchy": list(hierarchy), "images": images}


def validate_current_exact_anchor(
    evidence: dict[str, Any],
    model: dict[str, Any],
    current_sources: list[dict[str, Any]],
    current_manifest: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    validate_current_state(evidence, model)
    identity = evidence["identityReview"]
    expected_version = int(identity.get("targetGalleryVersionBeforePromotion") or 0)
    if int(model.get("gallery_version") or 0) != expected_version:
        raise RuntimeError("gallery_version atual mudou desde a revisão")
    anchor_sha = validate_sha(
        evidence["metricReview"]["existingCover"].get("sourceSha256"),
        "existing source sha256",
    )
    if len(current_sources) != int(model.get("image_count") or 0):
        raise RuntimeError("índice SHA atual está incompleto")
    anchors = [
        row for row in current_sources
        if row.get("position") is not None
        and int(row["position"]) == 0
        and row.get("role") == "cover"
    ]
    if len(anchors) != 1 or validate_sha(anchors[0].get("source_sha256"), "indexed cover sha256") != anchor_sha:
        raise RuntimeError("índice SHA atual não preserva a âncora exact-source")
    images = current_manifest.get("images")
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        raise RuntimeError("manifesto atual sem capa")
    current_cover = images[0]
    if validate_sha(current_cover.get("sourceSha256"), "manifest cover sha256") != anchor_sha:
        raise RuntimeError("manifesto atual não preserva sourceSha256 da âncora")
    if str(current_cover.get("id") or "") != str(anchors[0].get("image_id") or ""):
        raise RuntimeError("image id da capa diverge entre manifesto e índice D1")
    return anchor_sha, current_cover


def build_candidate_bundle(
    evidence: dict[str, Any],
    contract: dict[str, Any],
    hierarchy: list[str],
    current_cover: dict[str, Any],
    anchor_sha: str,
    account_id: str,
    token: str,
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    target_model, _, images, _ = validate_evidence(evidence)
    staging_bucket = str(contract.get("stagingBucket") or "").strip()
    staging_prefix = decode_b64_text(contract.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    if not staging_bucket or not staging_prefix:
        raise RuntimeError("contrato staging incompleto")

    additions = sorted(
        [row for row in images if row.get("proposedAction") == "add_view"],
        key=lambda row: int(row["position"]),
    )
    if len(additions) != len(images) - 1:
        raise RuntimeError("exact-source apply exige uma âncora skip_exact e apenas add_view restantes")

    source_root = root / "source"
    r2_root = root / "bundle" / "r2"
    rendered_images: list[dict[str, Any]] = []
    for row in additions:
        sha = validate_sha(row.get("sha256"), "source sha256")
        ext = extension(row.get("extension"))
        source = source_root / f"{sha}.{ext}"
        get_r2_object(account_id, token, staging_bucket, staging_key(staging_prefix, sha, ext), source)
        metrics = image_metrics(source)
        if metrics["sha256"] != sha or int(metrics["size"]) != int(row.get("bytes") or 0):
            raise RuntimeError(f"bytes staging mudaram após autorização: {sha}")
        if int(metrics["width"]) != int(row.get("width") or 0) or int(metrics["height"]) != int(row.get("height") or 0):
            raise RuntimeError(f"dimensões staging mudaram após autorização: {sha}")
        image_id = stable_id("img", sha)
        base_key = f"media/{target_model}/{image_id}"
        variant_keys, variant_meta = render_variants(source, r2_root, base_key)
        rendered_images.append({
            "id": image_id,
            "role": "gallery",
            "width": metrics["width"],
            "height": metrics["height"],
            "bytes": metrics["size"],
            "mime": f"image/{'jpeg' if ext in {'jpg', 'jpeg'} else ext}",
            "qualityScore": metrics["quality_score"],
            "sourceSha256": sha,
            "variantKeys": variant_keys,
            "variants": variant_meta,
        })

    identity_payload = gallery_payload_with_reused_cover(
        target_model, hierarchy, current_cover, anchor_sha, rendered_images
    )
    gallery_digest, gallery_version = gallery_identity(identity_payload)
    gallery_key = f"gallery/{target_model}/{gallery_digest[:24]}.json"
    gallery = {
        "version": gallery_version,
        **identity_payload,
        "generatedBy": "tools/apply_exact_source_gallery_promotion.py",
    }
    gallery_path = r2_root / gallery_key
    gallery_path.parent.mkdir(parents=True, exist_ok=True)
    gallery_path.write_text(json.dumps(gallery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    source_index = []
    for position, image in enumerate(identity_payload["images"]):
        source_index.append({
            "model_id": target_model,
            "image_id": str(image["id"]),
            "position": position,
            "role": "cover" if position == 0 else "gallery",
            "source_sha256": validate_sha(image.get("sourceSha256"), "gallery sourceSha256"),
        })
    return {
        "modelId": target_model,
        "imageCount": len(identity_payload["images"]),
        "coverStorageKey": str(current_cover["variantKeys"]["card"]),
        "galleryManifestKey": gallery_key,
        "galleryVersion": gallery_version,
        "galleryDigest": gallery_digest,
        "sourceHierarchy": hierarchy,
        "reusedCoverImageId": str(current_cover["id"]),
        "reusedCoverSourceSha256": anchor_sha,
    }, source_index, r2_root


def apply(args: argparse.Namespace) -> dict[str, Any]:
    evidence_path = Path(args.evidence)
    authorization_path = Path(args.authorization)
    evidence = load_json(evidence_path)
    authorization = load_json(authorization_path)
    contract = load_json(Path(args.source_contract))
    target_model, _, images, _ = validate_evidence(evidence)
    if authorization.get("decision", {}).get("canonicalPromotionAuthorizationReady") is not True:
        raise RuntimeError("autorização canônica não está pronta")
    if str(authorization.get("targetModelId") or "") != target_model:
        raise RuntimeError("autorização aponta para outro targetModelId")
    expected_resolution = validate_sha(
        authorization.get("resolution", {}).get("resolutionSha256"),
        "resolutionSha256",
    )
    if int(authorization.get("resolution", {}).get("addView") or 0) != len(images) - 1:
        raise RuntimeError("autorização não cobre todas as novas vistas")
    if int(authorization.get("resolution", {}).get("replaceExisting") or 0) != 0:
        raise RuntimeError("exact-source authorization não aceita replace_existing")

    account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
    token = require_env("CLOUDFLARE_API_TOKEN")
    require_env("CLOUDFLARE_D1_DATABASE_ID")
    staging_bucket = str(contract.get("stagingBucket") or "").strip()
    production_bucket = str(args.production_bucket or "").strip()
    if not staging_bucket or not production_bucket or staging_bucket == production_bucket:
        raise RuntimeError("buckets staging/produção inválidos")

    with tempfile.TemporaryDirectory(prefix="exact-source-gallery-apply-") as temp_dir:
        root = Path(temp_dir)
        preflight_summary = run_preflight(SimpleNamespace(
            evidence=str(evidence_path),
            source_contract=str(args.source_contract),
            production_bucket=production_bucket,
            output=str(root / "preflight"),
        ))
        actual_resolution = str(preflight_summary["promotionSummary"]["resolutionSha256"])
        if actual_resolution != expected_resolution:
            raise RuntimeError(
                f"preflight canônico mudou desde a autorização: {actual_resolution} != {expected_resolution}"
            )

        before = fetch_model_state(target_model)
        current_manifest = read_json_object(
            account_id, token, production_bucket, str(before["gallery_manifest_key"]), root / "current"
        )
        hierarchy = validate_current_manifest(current_manifest, before)
        current_sources = fetch_current_sources(target_model, int(before["gallery_version"]))
        anchor_sha, current_cover = validate_current_exact_anchor(
            evidence, before, current_sources, current_manifest
        )

        reviewed_shas = [validate_sha(row.get("sha256"), "reviewed source sha256") for row in images]
        foreign_owners = fetch_other_sha_owners(target_model, reviewed_shas)
        if foreign_owners:
            raise RuntimeError(f"SHA já pertence a outro modelo publicado/histórico: {foreign_owners[:3]}")

        candidate, source_index, r2_root = build_candidate_bundle(
            evidence, contract, hierarchy, current_cover, anchor_sha,
            account_id, token, root,
        )
        candidate_model = fetch_model_state(target_model)
        if is_candidate_current(candidate_model, candidate):
            current = fetch_current_sources(target_model, int(candidate["galleryVersion"]))
            if len(current) != int(candidate["imageCount"]):
                raise RuntimeError("galeria já aplicada mas índice SHA está incompleto")
            result = {
                "version": 1,
                "state": "already_applied",
                "apply": bool(args.apply),
                "targetModelId": target_model,
                "resolutionSha256": expected_resolution,
                "candidate": candidate,
                "r2": {"planned": 0, "written": 0, "reused": 0},
                "d1Writes": 0,
                "productionDeletes": 0,
            }
        else:
            r2_counts = publish_bundle_objects(
                account_id, token, production_bucket, r2_root, root / "remote-check",
                apply=bool(args.apply),
            )
            d1_writes = 0
            after = before
            if args.apply:
                results = d1_request(build_d1_statements(before, candidate, source_index))
                update_meta = results[-1].get("meta") or {}
                if int(update_meta.get("changes") or 0) != 1:
                    raise RuntimeError("UPDATE condicional não alterou exatamente uma linha")
                d1_writes = sum(int((item.get("meta") or {}).get("changes") or 0) for item in results)
                after = fetch_model_state(target_model)
                if not is_candidate_current(after, candidate):
                    raise RuntimeError("estado D1 pós-commit divergiu do candidato")
                indexed = fetch_current_sources(target_model, int(candidate["galleryVersion"]))
                expected_index = {
                    (row["source_sha256"], int(row["position"]), row["role"])
                    for row in source_index
                }
                actual_index = {
                    (str(row["source_sha256"]), int(row["position"]), str(row["role"]))
                    for row in indexed
                }
                if actual_index != expected_index:
                    raise RuntimeError("índice SHA pós-commit divergiu da galeria candidata")
                check_manifest = read_json_object(
                    account_id, token, production_bucket, candidate["galleryManifestKey"], root / "post-check"
                )
                validate_current_manifest(check_manifest, after)

            result = {
                "version": 1,
                "state": "applied_verified" if args.apply else "dry_run_ready",
                "apply": bool(args.apply),
                "targetModelId": target_model,
                "resolutionSha256": expected_resolution,
                "before": {
                    "imageCount": int(before["image_count"]),
                    "coverStorageKey": before["cover_storage_key"],
                    "galleryManifestKey": before["gallery_manifest_key"],
                    "galleryVersion": int(before["gallery_version"]),
                    "publicGalleryVersion": int(before["public_gallery_version"]),
                },
                "candidate": candidate,
                "after": {
                    "imageCount": int(after["image_count"]),
                    "coverStorageKey": after["cover_storage_key"],
                    "galleryManifestKey": after["gallery_manifest_key"],
                    "galleryVersion": int(after["gallery_version"]),
                    "publicGalleryVersion": int(after["public_gallery_version"]),
                } if args.apply else None,
                "r2": r2_counts,
                "d1Writes": d1_writes,
                "productionDeletes": 0,
            }

        summary_path = Path(args.summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence")
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--source-contract", required=True)
    parser.add_argument("--production-bucket", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    apply(parse_args())
