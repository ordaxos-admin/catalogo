#!/usr/bin/env python3
"""Run the canonical existing-gallery merge gates from versioned review evidence.

This is a read-only preflight. It materializes the exact reviewed source bytes from
private staging plus the current production cover, recomputes canonical PIL metrics,
runs plan_gallery_merge -> resolve_gallery_merge -> build_gallery_promotion_manifest,
and writes short-lived authorization artifacts. It never writes R2 or D1.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from build_gallery_promotion_manifest import build_promotion_rows, write_csv
from ingest_catalog import dhash_image, quality_score, sharpness_score
from media_quality_policy import AUTHORIZED_EXTERNAL_PROVENANCE
from plan_gallery_merge import plan_gallery_merge
from resolve_gallery_merge import resolve_gallery_merge

USER_AGENT = "catalog-existing-gallery-preflight/1.0"
ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"objeto JSON esperado: {path}")
    return data


def decode_b64_text(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise RuntimeError(f"{field} ausente")
    try:
        value = base64.b64decode(raw, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{field} inválido") from exc
    if not value:
        raise RuntimeError(f"{field} vazio")
    return value


def validate_sha(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise RuntimeError(f"{label} inválido: {value!r}")
    return digest


def extension(value: Any) -> str:
    ext = str(value or "").strip().lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED_EXTS:
        raise RuntimeError(f"extensão não permitida: {ext!r}")
    return ext


def staging_key(prefix: str, sha: str, ext: str) -> str:
    return f"{prefix.strip('/')}/sha256/{sha[:2]}/{sha}.{ext}"


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def get_r2_object(account_id: str, token: str, bucket: str, key: str, destination: Path) -> None:
    encoded = urllib.parse.quote(key, safe="/")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{encoded}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)


def image_metrics(path: Path) -> dict[str, Any]:
    digest, size = sha256_file(path)
    with Image.open(path) as verify_image:
        verify_image.verify()
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        width, height = image.size
        dhash = dhash_image(image)
        sharpness = sharpness_score(image)
    return {
        "sha256": digest,
        "size": size,
        "width": width,
        "height": height,
        "dhash": dhash,
        "sharpness": sharpness,
        "quality_score": quality_score(width, height, sharpness, path.suffix),
    }


def compact_manifest_row(
    *,
    path: str,
    metrics: dict[str, Any],
    model: str,
    watermark: str | None,
    source_id: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": path,
        "status": "OK",
        "canonical": True,
        "sha256": metrics["sha256"],
        "dhash": metrics["dhash"],
        "width": metrics["width"],
        "height": metrics["height"],
        "size": metrics["size"],
        "sharpness": metrics["sharpness"],
        "quality_score": metrics["quality_score"],
        "public_model_key": model,
        "model_key": model,
    }
    if watermark is not None:
        row["watermark_status"] = watermark
    if source_id:
        row["source_provenance"] = {
            "kind": AUTHORIZED_EXTERNAL_PROVENANCE,
            "sourceId": source_id,
        }
    return row


def validate_evidence(evidence: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    identity = evidence.get("identityReview")
    decision = evidence.get("decision")
    images = evidence.get("images")
    if not isinstance(identity, dict) or identity.get("decision") != "map_existing" or identity.get("mapExistingApproved") is not True:
        raise RuntimeError("evidência não possui map_existing explicitamente aprovado")
    if not isinstance(decision, dict) or decision.get("galleryMediaReviewApproved") is not True:
        raise RuntimeError("auditoria da galeria ainda não aprovada")
    if decision.get("newModelApproved") is True or decision.get("batch005Approved") is True:
        raise RuntimeError("preflight de galeria existente não aceita autorização de new_model/batch")
    if not isinstance(images, list) or not images:
        raise RuntimeError("evidência sem imagens")
    target = str(identity.get("targetModelId") or "").strip()
    incoming = str(evidence.get("incomingModel") or "").strip()
    if not target or not incoming:
        raise RuntimeError("identidades target/incoming ausentes")
    positions = {int(row.get("position") or 0) for row in images if isinstance(row, dict)}
    if positions != set(range(1, len(images) + 1)):
        raise RuntimeError("posições da galeria incompletas/duplicadas")
    for row in images:
        if row.get("stagingVerified") is not True:
            raise RuntimeError("preflight exige cobertura staging 100%")
        if row.get("watermarkStatus") not in {"clear", "creator_attribution"}:
            raise RuntimeError("preflight bloqueado por watermarkStatus")
        if row.get("proposedAction") not in {"add_view", "replace_existing"}:
            raise RuntimeError("imagem sem proposta explícita de ação")
        validate_sha(row.get("sha256"), "source sha256")
        if int(row.get("bytes") or 0) < 1:
            raise RuntimeError("imagem sem bytes esperados")
    return target, incoming, images


def compare_recorded_metrics(recorded: dict[str, Any], actual: dict[str, Any], label: str) -> None:
    fields = ("sha256", "bytes", "width", "height", "dhash")
    aliases = {"bytes": "size"}
    for field in fields:
        actual_field = aliases.get(field, field)
        expected = recorded.get(field)
        got = actual.get(actual_field)
        if str(expected) != str(got):
            raise RuntimeError(f"métrica divergente {label}.{field}: esperado={expected!r}, atual={got!r}")
    for field, tolerance in (("sharpness", 0.001), ("qualityScore", 0.001)):
        actual_field = "quality_score" if field == "qualityScore" else field
        if abs(float(recorded.get(field) or 0) - float(actual.get(actual_field) or 0)) > tolerance:
            raise RuntimeError(f"métrica divergente {label}.{field}")


def run_preflight(args: argparse.Namespace) -> dict[str, Any]:
    evidence = load_json(Path(args.evidence))
    contract = load_json(Path(args.source_contract))
    target_model, incoming_model, image_rows = validate_evidence(evidence)

    account_id = str(os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = str(os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if not account_id or not token:
        raise RuntimeError("credenciais Cloudflare ausentes")
    staging_bucket = str(contract.get("stagingBucket") or "").strip()
    staging_prefix = decode_b64_text(contract.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    production_bucket = str(args.production_bucket or "").strip()
    source_id = str(evidence.get("sourceId") or "").strip()
    if source_id != str(contract.get("sourceId") or "").strip():
        raise RuntimeError("sourceId da evidência diverge do contrato")
    if not staging_bucket or not production_bucket:
        raise RuntimeError("buckets staging/production ausentes")
    if staging_bucket == production_bucket:
        raise RuntimeError("staging e produção devem permanecer separados")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    existing_cover = ((evidence.get("metricReview") or {}).get("existingCover") or {})
    cover_key = str(existing_cover.get("storageKey") or "").strip()
    cover_sha = validate_sha(existing_cover.get("sha256"), "existing cover sha256")
    if not cover_key.startswith("media/"):
        raise RuntimeError("storageKey da capa existente inválido")

    with tempfile.TemporaryDirectory(prefix="existing-gallery-preflight-") as temp_dir:
        temp = Path(temp_dir)
        cover_path = temp / "existing.webp"
        get_r2_object(account_id, token, production_bucket, cover_key, cover_path)
        cover_metrics = image_metrics(cover_path)
        compare_recorded_metrics(existing_cover, cover_metrics, "existingCover")
        existing_manifest = [
            compact_manifest_row(
                path=f"production/{target_model}/cover.webp",
                metrics=cover_metrics,
                model=target_model,
                watermark=None,
            )
        ]

        incoming_manifest: list[dict[str, Any]] = []
        actual_by_sha: dict[str, dict[str, Any]] = {}
        for row in sorted(image_rows, key=lambda item: int(item["position"])):
            sha = validate_sha(row["sha256"], "source sha256")
            ext = extension(row.get("extension"))
            key = staging_key(staging_prefix, sha, ext)
            source_path = temp / f"{int(row['position']):02d}.{ext}"
            get_r2_object(account_id, token, staging_bucket, key, source_path)
            metrics = image_metrics(source_path)
            compare_recorded_metrics(row, metrics, f"images[{row['position']}]")
            actual_by_sha[sha] = metrics
            incoming_manifest.append(
                compact_manifest_row(
                    path=f"staging/{source_id}/product-{evidence['sourceProductId']}/{int(row['position']):02d}.{ext}",
                    metrics=metrics,
                    model=incoming_model,
                    watermark=str(row["watermarkStatus"]),
                    source_id=source_id,
                )
            )

    plan = plan_gallery_merge(
        existing_manifest,
        incoming_manifest,
        visual_threshold=int(((evidence.get("metricReview") or {}).get("visualThreshold") or 6)),
        identity_mapping={incoming_model: target_model},
    )

    decisions: dict[tuple[str, str], dict[str, str]] = {}
    evidence_by_sha = {str(row["sha256"]): row for row in image_rows}
    for action in plan["actions"]:
        incoming = action["incoming"]
        sha = str(incoming["sha256"])
        reviewed = evidence_by_sha[sha]
        proposal = str(reviewed.get("proposedAction") or "")
        kind = str(action.get("action") or "")
        if kind == "review_visual_candidate":
            if proposal == "replace_existing":
                replace_sha = validate_sha(reviewed.get("replaceSha256"), "replaceSha256")
                if replace_sha != cover_sha:
                    raise RuntimeError("replaceSha256 não corresponde à capa materializada")
                decisions[(str(incoming["path"]), sha)] = {
                    "decision": "replace_existing",
                    "replace_sha256": replace_sha,
                }
            elif proposal == "add_view":
                decisions[(str(incoming["path"]), sha)] = {"decision": "add_view", "replace_sha256": ""}
            else:
                raise RuntimeError("candidato perceptual sem decisão explícita na evidência")
        elif kind == "add_view" and proposal != "add_view":
            raise RuntimeError("planner e decisão de add_view divergem")
        elif kind not in {"add_view", "skip_exact", "reject_watermarked"}:
            raise RuntimeError(f"ação do planner não resolvida: {kind}")

    resolution = resolve_gallery_merge(plan, decisions)
    promotions, promotion_summary = build_promotion_rows(resolution)
    expected_add = sum(row.get("proposedAction") == "add_view" for row in image_rows)
    expected_replace = sum(row.get("proposedAction") == "replace_existing" for row in image_rows)
    if promotion_summary["addView"] != expected_add or promotion_summary["replaceExisting"] != expected_replace:
        raise RuntimeError("resolução canônica diverge das decisões visuais versionadas")

    (output / "gallery-merge-plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "gallery-merge-resolution.json").write_text(json.dumps(resolution, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(output / "gallery-promotions.csv", promotions)
    summary = {
        "version": 1,
        "ready": True,
        "sourceId": source_id,
        "sourceProductId": int(evidence["sourceProductId"]),
        "targetModelId": target_model,
        "incomingImages": len(incoming_manifest),
        "planCounts": plan["counts"],
        "promotionSummary": promotion_summary,
        "productionWrites": 0,
        "stagingWrites": 0,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence")
    parser.add_argument("--source-contract", required=True)
    parser.add_argument("--production-bucket", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    run_preflight(parse_args())
