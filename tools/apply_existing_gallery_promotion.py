#!/usr/bin/env python3
"""Safely apply an authorized external-media enrichment to one existing gallery.

The operation is monotonic and fail-closed:
- the canonical preflight is regenerated first and must produce the approved resolution SHA;
- the current D1 model + current R2 gallery/cover must still match the reviewed state;
- new content-addressed media variants + gallery manifest are written and read back first;
- D1 is updated last with optimistic state predicates in one batch;
- old R2 objects are never deleted;
- the current-version model_image_sources index is verified after commit.

Dry-run is the default. --apply is required for production writes.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from build_media_bundle import gallery_identity, render_variants, stable_id
from preflight_existing_gallery_evidence import (
    decode_b64_text,
    extension,
    get_r2_object,
    image_metrics,
    load_json,
    run_preflight,
    staging_key,
    validate_evidence,
    validate_sha,
)

USER_AGENT = "catalog-existing-gallery-apply/1.0"


def require_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"variável obrigatória ausente: {name}")
    return value


def d1_request(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not statements:
        return []
    account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
    database_id = require_env("CLOUDFLARE_D1_DATABASE_ID")
    token = require_env("CLOUDFLARE_API_TOKEN")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
    request = urllib.request.Request(
        url,
        data=json.dumps({"batch": statements}, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"D1 HTTP {exc.code}: {detail}") from exc
    if payload.get("success") is not True:
        raise RuntimeError(f"D1 recusou operação: {payload.get('errors')}")
    result = payload.get("result")
    if not isinstance(result, list):
        raise RuntimeError("resposta D1 sem lista result")
    for item in result:
        if not isinstance(item, dict) or item.get("success") is not True:
            raise RuntimeError(f"statement D1 falhou: {item!r}")
    return result


def fetch_model_state(model_id: str) -> dict[str, Any]:
    result = d1_request([{
        "sql": """SELECT
m.id,m.slug,m.code,m.name,m.image_count,m.cover_storage_key,m.gallery_manifest_key,
m.gallery_version,m.public_gallery_version,m.published
FROM models m WHERE m.id=?""",
        "params": [model_id],
    }])
    rows = result[0].get("results")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError(f"modelo alvo ausente/ambíguo no D1: {model_id}")
    return rows[0]


def fetch_current_sources(model_id: str, gallery_version: int) -> list[dict[str, Any]]:
    result = d1_request([{
        "sql": """SELECT model_id,image_id,position,role,source_sha256,gallery_version
FROM model_image_sources WHERE model_id=? AND gallery_version=? ORDER BY position,image_id""",
        "params": [model_id, gallery_version],
    }])
    rows = result[0].get("results")
    if not isinstance(rows, list):
        raise RuntimeError("consulta model_image_sources inválida")
    return [row for row in rows if isinstance(row, dict)]


def fetch_other_sha_owners(model_id: str, shas: list[str]) -> list[dict[str, Any]]:
    if not shas:
        return []
    placeholders = ",".join("?" for _ in shas)
    result = d1_request([{
        "sql": f"""SELECT s.source_sha256,s.model_id,m.slug,m.published,s.gallery_version
FROM model_image_sources s JOIN models m ON m.id=s.model_id
WHERE s.source_sha256 IN ({placeholders}) AND s.model_id<>?
ORDER BY s.source_sha256,s.model_id""",
        "params": [*shas, model_id],
    }])
    rows = result[0].get("results")
    if not isinstance(rows, list):
        raise RuntimeError("consulta de ownership SHA inválida")
    return [row for row in rows if isinstance(row, dict)]


def r2_url(account_id: str, bucket: str, key: str) -> str:
    encoded = urllib.parse.quote(key, safe="/")
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{encoded}"


def r2_download(account_id: str, token: str, bucket: str, key: str, destination: Path) -> bool:
    request = urllib.request.Request(
        r2_url(account_id, bucket, key),
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise
    destination.parent.mkdir(parents=True, exist_ok=True)
    with response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    return True


def r2_put(account_id: str, token: str, bucket: str, key: str, source: Path, content_type: str) -> None:
    payload = source.read_bytes()
    request = urllib.request.Request(
        r2_url(account_id, bucket, key),
        data=payload,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()


def sha256_bytes(path: Path) -> tuple[str, int]:
    import hashlib

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def put_if_absent_or_identical(
    account_id: str,
    token: str,
    bucket: str,
    key: str,
    source: Path,
    content_type: str,
    scratch: Path,
    *,
    apply: bool,
) -> str:
    local_sha, local_size = sha256_bytes(source)
    existing = scratch / (stable_id("remote", key) + source.suffix)
    if r2_download(account_id, token, bucket, key, existing):
        remote_sha, remote_size = sha256_bytes(existing)
        if remote_sha != local_sha or remote_size != local_size:
            raise RuntimeError(f"colisão R2 em chave content-addressed: {key}")
        existing.unlink(missing_ok=True)
        return "reused"
    if not apply:
        return "planned"
    r2_put(account_id, token, bucket, key, source, content_type)
    if not r2_download(account_id, token, bucket, key, existing):
        raise RuntimeError(f"objeto R2 ausente após PUT: {key}")
    remote_sha, remote_size = sha256_bytes(existing)
    existing.unlink(missing_ok=True)
    if remote_sha != local_sha or remote_size != local_size:
        raise RuntimeError(f"read-back R2 divergiu após PUT: {key}")
    return "written"


def read_json_object(account_id: str, token: str, bucket: str, key: str, scratch: Path) -> dict[str, Any]:
    target = scratch / "current-gallery.json"
    if not r2_download(account_id, token, bucket, key, target):
        raise RuntimeError(f"manifesto atual ausente no R2: {key}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"manifesto atual inválido no R2: {key}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("manifesto atual não é objeto JSON")
    return data


def validate_current_manifest(manifest: dict[str, Any], model: dict[str, Any]) -> list[str]:
    model_id = str(model["id"])
    images = manifest.get("images")
    if manifest.get("modelId") != model_id:
        raise RuntimeError("manifesto atual pertence a outro modelo")
    if int(manifest.get("version") or 0) != int(model.get("gallery_version") or 0):
        raise RuntimeError("gallery_version D1/R2 divergente antes do apply")
    if not isinstance(images, list) or len(images) != int(model.get("image_count") or 0):
        raise RuntimeError("image_count D1/R2 divergente antes do apply")
    if not images or not isinstance(images[0], dict) or images[0].get("role") != "cover":
        raise RuntimeError("manifesto atual sem capa canônica na posição zero")
    variant_keys = images[0].get("variantKeys")
    if not isinstance(variant_keys, dict) or variant_keys.get("card") != model.get("cover_storage_key"):
        raise RuntimeError("cover_storage_key D1/R2 divergente antes do apply")
    hierarchy = manifest.get("sourceHierarchy")
    if not isinstance(hierarchy, list) or not hierarchy or any(not isinstance(item, str) or not item for item in hierarchy):
        raise RuntimeError("sourceHierarchy atual ausente/inválida")
    return hierarchy


def validate_current_state(evidence: dict[str, Any], model: dict[str, Any]) -> None:
    identity = evidence["identityReview"]
    cover = evidence["metricReview"]["existingCover"]
    if str(model.get("id") or "") != str(identity.get("targetModelId") or ""):
        raise RuntimeError("model id atual divergiu da evidência")
    if str(model.get("slug") or "") != str(identity.get("targetSlug") or ""):
        raise RuntimeError("slug atual divergiu da evidência")
    if str(model.get("code") or "") != str(identity.get("targetCode") or ""):
        raise RuntimeError("code atual divergiu da evidência")
    if int(model.get("published") or 0) != 1:
        raise RuntimeError("modelo alvo deixou de estar publicado")
    if int(model.get("image_count") or 0) != int(identity.get("targetImageCountBeforePromotion") or 0):
        raise RuntimeError("image_count atual mudou desde a revisão")
    if str(model.get("cover_storage_key") or "") != str(cover.get("storageKey") or ""):
        raise RuntimeError("capa atual mudou desde a revisão")


def mime_for_source(ext: str) -> str:
    if ext in {"jpg", "jpeg"}:
        return "image/jpeg"
    return mimetypes.guess_type(f"file.{ext}")[0] or f"image/{ext}"


def build_candidate_bundle(
    evidence: dict[str, Any],
    contract: dict[str, Any],
    hierarchy: list[str],
    staging_bucket: str,
    production_bucket: str,
    account_id: str,
    token: str,
    root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], Path]:
    target_model, _, images = validate_evidence(evidence)
    staging_prefix = decode_b64_text(contract.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    source_id = str(evidence.get("sourceId") or "")
    if source_id != str(contract.get("sourceId") or ""):
        raise RuntimeError("sourceId diverge do contrato")

    replacement = [row for row in images if row.get("proposedAction") == "replace_existing"]
    if len(replacement) != 1:
        raise RuntimeError("apply atual exige exatamente uma substituição explícita da capa")
    ordered = [replacement[0], *[row for row in sorted(images, key=lambda item: int(item["position"])) if row is not replacement[0]]]

    source_root = root / "source"
    r2_root = root / "bundle" / "r2"
    gallery_images: list[dict[str, Any]] = []
    source_index: list[dict[str, Any]] = []
    for index, row in enumerate(ordered):
        sha = validate_sha(row["sha256"], "source sha256")
        ext = extension(row.get("extension"))
        source = source_root / f"{sha}.{ext}"
        get_r2_object(account_id, token, staging_bucket, staging_key(staging_prefix, sha, ext), source)
        metrics = image_metrics(source)
        if metrics["sha256"] != sha or int(metrics["size"]) != int(row["bytes"]):
            raise RuntimeError(f"bytes staging mudaram após preflight: {sha}")
        image_id = stable_id("img", sha)
        base_key = f"media/{target_model}/{image_id}"
        variant_keys, variant_meta = render_variants(source, r2_root, base_key)
        gallery_images.append({
            "id": image_id,
            "role": "cover" if index == 0 else "gallery",
            "width": metrics["width"],
            "height": metrics["height"],
            "bytes": metrics["size"],
            "mime": mime_for_source(ext),
            "qualityScore": metrics["quality_score"],
            "sourceSha256": sha,
            "variantKeys": variant_keys,
            "variants": variant_meta,
        })
        source_index.append({
            "model_id": target_model,
            "image_id": image_id,
            "position": index,
            "role": "cover" if index == 0 else "gallery",
            "source_sha256": sha,
        })

    identity_payload = {
        "modelId": target_model,
        "sourceHierarchy": hierarchy,
        "images": gallery_images,
    }
    gallery_digest, gallery_version = gallery_identity(identity_payload)
    gallery_key = f"gallery/{target_model}/{gallery_digest[:24]}.json"
    gallery = {
        "version": gallery_version,
        **identity_payload,
        "generatedBy": "tools/apply_existing_gallery_promotion.py",
    }
    gallery_path = r2_root / gallery_key
    gallery_path.parent.mkdir(parents=True, exist_ok=True)
    gallery_path.write_text(json.dumps(gallery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "modelId": target_model,
        "imageCount": len(gallery_images),
        "coverStorageKey": gallery_images[0]["variantKeys"]["card"],
        "galleryManifestKey": gallery_key,
        "galleryVersion": gallery_version,
        "galleryDigest": gallery_digest,
        "sourceHierarchy": hierarchy,
    }, source_index, r2_root


def publish_bundle_objects(
    account_id: str,
    token: str,
    bucket: str,
    r2_root: Path,
    scratch: Path,
    *,
    apply: bool,
) -> dict[str, int]:
    counts = {"planned": 0, "written": 0, "reused": 0}
    files = sorted(path for path in r2_root.rglob("*") if path.is_file())
    if not files:
        raise RuntimeError("bundle R2 vazio")
    for path in files:
        key = path.relative_to(r2_root).as_posix()
        content_type = "application/json" if path.suffix.lower() == ".json" else "image/webp"
        state = put_if_absent_or_identical(
            account_id, token, bucket, key, path, content_type, scratch, apply=apply
        )
        counts[state] += 1
    return counts


def build_d1_statements(
    before: dict[str, Any],
    candidate: dict[str, Any],
    source_index: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    version = int(candidate["galleryVersion"])
    for row in source_index:
        statements.append({
            "sql": """INSERT INTO model_image_sources(
model_id,image_id,position,role,source_sha256,gallery_version)
VALUES(?,?,?,?,?,?)
ON CONFLICT(model_id,image_id) DO UPDATE SET
position=excluded.position,role=excluded.role,source_sha256=excluded.source_sha256,
gallery_version=excluded.gallery_version""",
            "params": [
                row["model_id"], row["image_id"], row["position"], row["role"],
                row["source_sha256"], version,
            ],
        })
    statements.append({
        "sql": """UPDATE models
SET image_count=?,cover_storage_key=?,gallery_manifest_key=?,gallery_version=?,updated_at=CURRENT_TIMESTAMP
WHERE id=? AND published=1 AND image_count=? AND cover_storage_key=?
  AND gallery_manifest_key=? AND gallery_version=?""",
        "params": [
            int(candidate["imageCount"]),
            candidate["coverStorageKey"],
            candidate["galleryManifestKey"],
            version,
            before["id"],
            int(before["image_count"]),
            before["cover_storage_key"],
            before["gallery_manifest_key"],
            int(before["gallery_version"]),
        ],
    })
    return statements


def is_candidate_current(model: dict[str, Any], candidate: dict[str, Any]) -> bool:
    return (
        int(model.get("image_count") or 0) == int(candidate["imageCount"])
        and str(model.get("cover_storage_key") or "") == str(candidate["coverStorageKey"])
        and str(model.get("gallery_manifest_key") or "") == str(candidate["galleryManifestKey"])
        and int(model.get("gallery_version") or 0) == int(candidate["galleryVersion"])
    )


def apply(args: argparse.Namespace) -> dict[str, Any]:
    evidence_path = Path(args.evidence)
    authorization_path = Path(args.authorization)
    evidence = load_json(evidence_path)
    authorization = load_json(authorization_path)
    contract = load_json(Path(args.source_contract))
    target_model, _, images = validate_evidence(evidence)
    if authorization.get("decision", {}).get("canonicalPromotionAuthorizationReady") is not True:
        raise RuntimeError("autorização canônica não está pronta")
    if str(authorization.get("targetModelId") or "") != target_model:
        raise RuntimeError("autorização aponta para outro targetModelId")
    expected_resolution = validate_sha(
        authorization.get("resolution", {}).get("resolutionSha256"),
        "resolutionSha256",
    )

    account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
    token = require_env("CLOUDFLARE_API_TOKEN")
    require_env("CLOUDFLARE_D1_DATABASE_ID")
    staging_bucket = str(contract.get("stagingBucket") or "").strip()
    production_bucket = str(args.production_bucket or "").strip()
    if not staging_bucket or not production_bucket or staging_bucket == production_bucket:
        raise RuntimeError("buckets staging/produção inválidos")

    with tempfile.TemporaryDirectory(prefix="existing-gallery-apply-") as temp_dir:
        root = Path(temp_dir)
        preflight_dir = root / "preflight"
        preflight_summary = run_preflight(SimpleNamespace(
            evidence=str(evidence_path),
            source_contract=str(args.source_contract),
            production_bucket=production_bucket,
            output=str(preflight_dir),
        ))
        actual_resolution = str(preflight_summary["promotionSummary"]["resolutionSha256"])
        if actual_resolution != expected_resolution:
            raise RuntimeError(
                f"preflight canônico mudou desde a autorização: {actual_resolution} != {expected_resolution}"
            )

        before = fetch_model_state(target_model)
        validate_current_state(evidence, before)
        current_manifest = read_json_object(
            account_id, token, production_bucket, str(before["gallery_manifest_key"]), root / "scratch"
        )
        hierarchy = validate_current_manifest(current_manifest, before)

        reviewed_shas = [validate_sha(row["sha256"], "source sha256") for row in images]
        foreign_owners = fetch_other_sha_owners(target_model, reviewed_shas)
        if foreign_owners:
            raise RuntimeError(f"SHA já pertence a outro modelo publicado/histórico: {foreign_owners[:3]}")

        candidate, source_index, r2_root = build_candidate_bundle(
            evidence, contract, hierarchy, staging_bucket, production_bucket,
            account_id, token, root,
        )
        candidate_model = fetch_model_state(target_model)
        if is_candidate_current(candidate_model, candidate):
            current_sources = fetch_current_sources(target_model, int(candidate["galleryVersion"]))
            if len(current_sources) != int(candidate["imageCount"]):
                raise RuntimeError("galeria já aplicada mas índice SHA atual está incompleto")
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
            Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
            Path(args.summary).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False))
            return result

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
                raise RuntimeError("UPDATE condicional do modelo não alterou exatamente uma linha")
            d1_writes = sum(int((item.get("meta") or {}).get("changes") or 0) for item in results)
            after = fetch_model_state(target_model)
            if not is_candidate_current(after, candidate):
                raise RuntimeError("estado D1 pós-commit divergiu do candidato")
            current_sources = fetch_current_sources(target_model, int(candidate["galleryVersion"]))
            if len(current_sources) != int(candidate["imageCount"]):
                raise RuntimeError("índice SHA pós-commit incompleto")
            indexed = {(str(row["source_sha256"]), int(row["position"]), str(row["role"])) for row in current_sources}
            expected = {(row["source_sha256"], int(row["position"]), row["role"]) for row in source_index}
            if indexed != expected:
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
        summary_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
