#!/usr/bin/env python3
"""Canonical read-only preflight for enriching an existing gallery by exact source SHA.

Unlike the visual-replacement path, the existing cover is represented by the
canonical source_sha256 stored in D1 while its perceptual metrics are recomputed
from the current derived cover object. This lets the planner emit skip_exact for
the already-published source image and add only genuinely new reviewed views.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from build_gallery_promotion_manifest import build_promotion_rows, write_csv
from media_quality_policy import AUTHORIZED_EXTERNAL_PROVENANCE
from plan_gallery_merge import plan_gallery_merge
from resolve_gallery_merge import resolve_gallery_merge
from preflight_existing_gallery_evidence import (
    compact_manifest_row,
    decode_b64_text,
    extension,
    get_r2_object,
    image_metrics,
    load_json,
    staging_key,
    validate_sha,
)

USER_AGENT = "catalog-exact-source-gallery-preflight/1.0"


def require_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"variável obrigatória ausente: {name}")
    return value


def d1_query(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
    database_id = require_env("CLOUDFLARE_D1_DATABASE_ID")
    token = require_env("CLOUDFLARE_API_TOKEN")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
    request = urllib.request.Request(
        url,
        data=json.dumps({"sql": sql, "params": params}, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("success") is not True:
        raise RuntimeError(f"D1 recusou consulta: {payload.get('errors')}")
    rows: list[dict[str, Any]] = []
    for result in payload.get("result") or []:
        if result.get("success") is not True:
            raise RuntimeError("statement D1 falhou")
        rows.extend(row for row in (result.get("results") or []) if isinstance(row, dict))
    return rows


def validate_evidence(evidence: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
    identity = evidence.get("identityReview")
    decision = evidence.get("decision")
    images = evidence.get("images")
    anchor = (evidence.get("metricReview") or {}).get("existingCover")
    if not isinstance(identity, dict) or identity.get("decision") != "map_existing" or identity.get("mapExistingApproved") is not True:
        raise RuntimeError("evidência não possui map_existing aprovado")
    if identity.get("exactSourceShaOverlap") is not True:
        raise RuntimeError("gate exact-source exige overlap SHA explícito")
    if not isinstance(decision, dict) or decision.get("galleryMediaReviewApproved") is not True:
        raise RuntimeError("auditoria visual da galeria ainda não aprovada")
    if decision.get("newModelApproved") is True or decision.get("batch005Approved") is True:
        raise RuntimeError("gate de galeria existente não aceita new_model/batch")
    if not isinstance(images, list) or not images:
        raise RuntimeError("evidência sem imagens")
    if not isinstance(anchor, dict):
        raise RuntimeError("evidência sem existingCover")
    target = str(identity.get("targetModelId") or "").strip()
    incoming = str(evidence.get("incomingModel") or "").strip()
    if not target or not incoming:
        raise RuntimeError("identidades target/incoming ausentes")
    positions = {int(row.get("position") or 0) for row in images if isinstance(row, dict)}
    if positions != set(range(1, len(images) + 1)):
        raise RuntimeError("posições incompletas/duplicadas")
    skip_rows = []
    for row in images:
        if row.get("stagingVerified") is not True:
            raise RuntimeError("preflight exige cobertura staging 100%")
        if row.get("watermarkStatus") not in {"clear", "creator_attribution"}:
            raise RuntimeError("preflight bloqueado por watermarkStatus")
        if row.get("proposedAction") not in {"add_view", "skip_exact"}:
            raise RuntimeError("ação exact-source inválida")
        validate_sha(row.get("sha256"), "source sha256")
        if int(row.get("bytes") or 0) < 1:
            raise RuntimeError("imagem sem bytes esperados")
        if row.get("proposedAction") == "skip_exact":
            skip_rows.append(row)
    if len(skip_rows) != 1:
        raise RuntimeError("gate exact-source exige exatamente um skip_exact de âncora")
    anchor_sha = validate_sha(anchor.get("sourceSha256"), "existing source sha256")
    if validate_sha(skip_rows[0].get("sha256"), "skip sha256") != anchor_sha:
        raise RuntimeError("skip_exact não corresponde à âncora sourceSha256")
    return target, incoming, images, anchor


def validate_recorded_source_metrics(row: dict[str, Any], actual: dict[str, Any]) -> None:
    expected = {
        "sha256": str(row.get("sha256") or ""),
        "size": int(row.get("bytes") or 0),
        "width": int(row.get("width") or 0),
        "height": int(row.get("height") or 0),
        "dhash": str(row.get("dhash") or ""),
    }
    for key, value in expected.items():
        if key == "dhash" and not value:
            continue
        if str(actual.get(key)) != str(value):
            raise RuntimeError(f"métrica source divergente position={row.get('position')} field={key}")


def fetch_current_anchor(target: str) -> dict[str, Any]:
    rows = d1_query(
        """SELECT m.id,m.slug,m.code,m.image_count,m.gallery_version,m.cover_storage_key,m.public_gallery_version,m.published,
 s.source_sha256,s.position,s.role
FROM models m JOIN model_image_sources s ON s.model_id=m.id AND s.gallery_version=m.gallery_version
WHERE m.id=? ORDER BY s.position""",
        [target],
    )
    if not rows:
        raise RuntimeError("modelo alvo sem índice SHA atual")
    model = rows[0]
    cover_rows = [
        row
        for row in rows
        if row.get("position") is not None
        and int(row["position"]) == 0
        and row.get("role") == "cover"
    ]
    if len(cover_rows) != 1:
        raise RuntimeError("modelo alvo sem âncora de capa única")
    return {**model, "source_sha256": cover_rows[0]["source_sha256"]}


def run_preflight(args: argparse.Namespace) -> dict[str, Any]:
    evidence = load_json(Path(args.evidence))
    contract = load_json(Path(args.source_contract))
    target, incoming_model, image_rows, anchor = validate_evidence(evidence)
    source_id = str(evidence.get("sourceId") or "").strip()
    if source_id != str(contract.get("sourceId") or "").strip():
        raise RuntimeError("sourceId diverge do contrato")

    current = fetch_current_anchor(target)
    identity = evidence["identityReview"]
    expected = {
        "id": target,
        "slug": str(identity.get("targetSlug") or ""),
        "code": str(identity.get("targetCode") or ""),
        "image_count": int(identity.get("targetImageCountBeforePromotion") or 0),
        "gallery_version": int(identity.get("targetGalleryVersionBeforePromotion") or 0),
        "cover_storage_key": str(anchor.get("storageKey") or ""),
        "source_sha256": validate_sha(anchor.get("sourceSha256"), "existing source sha256"),
    }
    for field, value in expected.items():
        if str(current.get(field)) != str(value):
            raise RuntimeError(f"estado atual divergiu da revisão: {field}")
    if int(current.get("published") or 0) != 1:
        raise RuntimeError("modelo alvo deixou de estar publicado")

    account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
    token = require_env("CLOUDFLARE_API_TOKEN")
    staging_bucket = str(contract.get("stagingBucket") or "").strip()
    staging_prefix = decode_b64_text(contract.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    production_bucket = str(args.production_bucket or "").strip()
    if not staging_bucket or not production_bucket or staging_bucket == production_bucket:
        raise RuntimeError("buckets staging/produção inválidos")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="exact-source-preflight-") as temp_dir:
        temp = Path(temp_dir)
        cover_path = temp / "existing-cover.webp"
        get_r2_object(account_id, token, production_bucket, expected["cover_storage_key"], cover_path)
        cover_metrics = image_metrics(cover_path)
        existing_row = compact_manifest_row(
            path=f"production/{target}/cover.webp",
            metrics=cover_metrics,
            model=target,
            watermark=None,
        )
        existing_row["sha256"] = expected["source_sha256"]

        incoming_manifest = []
        for row in sorted(image_rows, key=lambda item: int(item["position"])):
            sha = validate_sha(row["sha256"], "source sha256")
            ext = extension(row.get("extension"))
            source = temp / f"{int(row['position']):02d}.{ext}"
            get_r2_object(account_id, token, staging_bucket, staging_key(staging_prefix, sha, ext), source)
            metrics = image_metrics(source)
            validate_recorded_source_metrics(row, metrics)
            incoming_manifest.append(compact_manifest_row(
                path=f"staging/{source_id}/product-{evidence['sourceProductId']}/{int(row['position']):02d}.{ext}",
                metrics=metrics,
                model=incoming_model,
                watermark=str(row["watermarkStatus"]),
                source_id=source_id,
            ))

    threshold = int((evidence.get("metricReview") or {}).get("visualThreshold") or 6)
    plan = plan_gallery_merge([existing_row], incoming_manifest, visual_threshold=threshold, identity_mapping={incoming_model: target})
    evidence_by_sha = {str(row["sha256"]): row for row in image_rows}
    decisions: dict[tuple[str, str], dict[str, str]] = {}
    for action in plan["actions"]:
        incoming = action["incoming"]
        sha = str(incoming["sha256"])
        reviewed = evidence_by_sha[sha]
        proposal = str(reviewed.get("proposedAction") or "")
        kind = str(action.get("action") or "")
        if kind == "skip_exact":
            if proposal != "skip_exact":
                raise RuntimeError("planner encontrou exact mas evidência não autorizou skip_exact")
        elif kind == "add_view":
            if proposal != "add_view":
                raise RuntimeError("planner/evidência divergem em add_view")
        elif kind == "review_visual_candidate":
            if proposal != "add_view":
                raise RuntimeError("candidato perceptual não possui decisão add_view explícita")
            decisions[(str(incoming["path"]), sha)] = {"decision": "add_view", "replace_sha256": ""}
        else:
            raise RuntimeError(f"ação inesperada no gate exact-source: {kind}")

    resolution = resolve_gallery_merge(plan, decisions)
    promotions, promotion_summary = build_promotion_rows(resolution)
    expected_add = sum(row.get("proposedAction") == "add_view" for row in image_rows)
    expected_skip = sum(row.get("proposedAction") == "skip_exact" for row in image_rows)
    if promotion_summary["addView"] != expected_add or promotion_summary["replaceExisting"] != 0:
        raise RuntimeError("resolução diverge das decisões versionadas")
    if int(plan["counts"].get("skip_exact") or 0) != expected_skip:
        raise RuntimeError("planner não preservou âncora exact-source")

    (output / "gallery-merge-plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "gallery-merge-resolution.json").write_text(json.dumps(resolution, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(output / "gallery-promotions.csv", promotions)
    summary = {
        "version": 1,
        "ready": True,
        "sourceId": source_id,
        "sourceProductId": int(evidence["sourceProductId"]),
        "targetModelId": target,
        "incomingImages": len(incoming_manifest),
        "planCounts": plan["counts"],
        "promotionSummary": promotion_summary,
        "currentAnchorSourceSha256": expected["source_sha256"],
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
