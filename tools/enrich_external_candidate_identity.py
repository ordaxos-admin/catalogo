#!/usr/bin/env python3
"""Attach read-only production identity-review candidates to discovery evidence.

Name/search matches are review hints only. This tool never maps identity and never writes
Cloudflare state. For complete staged products it queries possible production name matches
and materializes their public cover objects into the short-lived review artifact so a human
can compare sculptures directly.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "catalog-external-identity-review/1.0"


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"objeto JSON esperado: {path}")
    return data


def subject_hint(value: str) -> str:
    text = html.unescape(str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    text = re.split(r"\bSTL\b", text, maxsplit=1, flags=re.IGNORECASE)[0].strip(" -–—")
    if len(text) >= 3:
        return text
    return html.unescape(str(value or "")).strip()


def post_json(url: str, payload: dict[str, Any], token: str) -> Any:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def find_candidates(account_id: str, token: str, database_id: str, hint: str) -> list[dict[str, Any]]:
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
    pattern = f"%{hint.casefold()}%"
    payload = post_json(
        endpoint,
        {
            "sql": (
                "SELECT id,name,slug,code,image_count,cover_storage_key "
                "FROM models WHERE published=1 AND (lower(name) LIKE ? OR lower(search_text) LIKE ?) "
                "ORDER BY CASE WHEN lower(name)=? THEN 0 ELSE 1 END,name,slug LIMIT 20"
            ),
            "params": [pattern, pattern, hint.casefold()],
        },
        token,
    )
    if not payload.get("success"):
        raise RuntimeError(f"falha na consulta D1: {payload.get('errors')}")
    rows: list[dict[str, Any]] = []
    for result in payload.get("result") or []:
        rows.extend(result.get("results") or [])
    return rows


def download_r2_object(account_id: str, token: str, bucket: str, key: str, destination: Path) -> int:
    encoded_key = urllib.parse.quote(key, safe="/")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{encoded_key}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 8 * 1024 * 1024:
                raise RuntimeError("capa de revisão excede limite de 8 MiB")
            handle.write(chunk)
    if total < 1:
        raise RuntimeError("capa de produção vazia")
    return total


def enrich(report_path: Path, descriptor_path: Path, review_root: Path) -> dict[str, Any]:
    report = load_json(report_path)
    descriptor = load_json(descriptor_path)
    production_bucket = str(descriptor.get("productionBucket") or "").strip()
    account_id = str(os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = str(os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    database_id = str(os.environ.get("CLOUDFLARE_D1_DATABASE_ID") or "").strip()
    if not production_bucket or not account_id or not token or not database_id:
        raise RuntimeError("bindings de revisão ausentes")

    materialized = 0
    for product in report.get("products") or []:
        state = str(product.get("state") or "")
        if state not in {"visual_review_required", "identity_review_required_exact_sha_overlap"}:
            continue
        hint = subject_hint(str(product.get("productName") or ""))
        matches = find_candidates(account_id, token, database_id, hint)
        review_candidates: list[dict[str, Any]] = []
        for row in matches:
            model_id = str(row.get("id") or "")
            key = str(row.get("cover_storage_key") or "")
            item = {
                "modelId": model_id,
                "name": str(row.get("name") or ""),
                "slug": str(row.get("slug") or ""),
                "code": str(row.get("code") or ""),
                "imageCount": int(row.get("image_count") or 0),
                "coverStorageKey": key,
                "matchBasis": "name_or_search_hint_review_only",
            }
            if model_id and key.startswith("media/"):
                ext = Path(key).suffix.lower() or ".bin"
                destination = review_root / f"product-{product['productId']}" / "production-candidates" / f"{model_id}{ext}"
                try:
                    item["coverBytes"] = download_r2_object(account_id, token, production_bucket, key, destination)
                    item["reviewArtifactPath"] = str(destination.relative_to(review_root.parent)).replace("\\", "/")
                    materialized += 1
                except Exception as exc:  # fail closed in report, do not hide missing comparison evidence
                    item["coverMaterializationError"] = str(exc)
            review_candidates.append(item)
        product["identityReviewHint"] = hint
        product["identityReviewCandidates"] = review_candidates
        product["identityDecision"] = "unresolved"
        product["promotable"] = False

    report["productionCoverReviewObjects"] = materialized
    report["productionWrites"] = 0
    report["stagingWrites"] = 0
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"productionCoverReviewObjects": materialized, "identityDecisions": 0}, ensure_ascii=False))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    parser.add_argument("--descriptor", default="config/external-batches/external-source-001-batch-004.json")
    parser.add_argument("--review-root", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    enrich(Path(args.report), Path(args.descriptor), Path(args.review_root))
