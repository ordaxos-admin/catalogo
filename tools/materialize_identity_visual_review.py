#!/usr/bin/env python3
"""Materialize an exact external gallery plus one public production candidate for identity review.

This tool is strictly read-only. External source bytes are verified against versioned
SHA-256/byte evidence. The production candidate is fetched through the public catalog
media gateway using its canonical cover storage key. No identity decision is made.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
from pathlib import Path
from typing import Any

from materialize_external_source_review import (
    decode_b64_text,
    download,
    load_json,
    safe_source_url,
    validate_sha,
)


def normalize_api_base(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise RuntimeError("api-base deve ser origem HTTPS sem query/fragment")
    return raw


def source_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    images = data.get("sourceImages")
    if not isinstance(images, list) or not images:
        raise RuntimeError("review sem sourceImages")
    rows: list[dict[str, Any]] = []
    seen_positions: set[int] = set()
    seen_shas: set[str] = set()
    for raw in images:
        if not isinstance(raw, dict):
            raise RuntimeError("sourceImages contém linha inválida")
        position = int(raw.get("position") or 0)
        if position < 1 or position in seen_positions:
            raise RuntimeError("posição de fonte inválida/duplicada")
        seen_positions.add(position)
        sha = validate_sha(raw.get("sha256"), f"source sha posição {position}")
        if sha in seen_shas:
            raise RuntimeError("sourceImages contém SHA duplicado")
        seen_shas.add(sha)
        if int(raw.get("bytes") or 0) < 1:
            raise RuntimeError(f"bytes inválidos na posição {position}")
        path = str(raw.get("sourcePath") or "").strip()
        if not path:
            raise RuntimeError(f"sourcePath ausente na posição {position}")
        rows.append(raw)
    rows.sort(key=lambda row: int(row["position"]))
    positions = [int(row["position"]) for row in rows]
    expected = list(range(1, len(rows) + 1))
    if positions != expected:
        raise RuntimeError(f"posições de fonte não contíguas: {positions} != {expected}")
    return rows


def production_candidate(data: dict[str, Any]) -> dict[str, Any]:
    candidate = data.get("productionCandidate")
    if not isinstance(candidate, dict):
        raise RuntimeError("productionCandidate ausente")
    model_id = str(candidate.get("modelId") or "").strip()
    slug = str(candidate.get("slug") or "").strip()
    cover_storage_key = str(candidate.get("coverStorageKey") or "").strip()
    source_sha = validate_sha(candidate.get("sourceSha256"), "production sourceSha256")
    if not model_id.startswith("mdl_") or not slug or not cover_storage_key.startswith("media/"):
        raise RuntimeError("productionCandidate inválido")
    return {
        "modelId": model_id,
        "slug": slug,
        "name": str(candidate.get("name") or "").strip(),
        "franchise": str(candidate.get("franchise") or "").strip(),
        "coverStorageKey": cover_storage_key,
        "sourceSha256": source_sha,
    }


def public_media_url(api_base: str, storage_key: str) -> str:
    base = normalize_api_base(api_base)
    safe_key = urllib.parse.quote(storage_key, safe="/")
    return f"{base}/media/{safe_key}"


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    review = load_json(Path(args.review))
    contract = load_json(Path(args.source_contract))
    source_id = str(review.get("sourceId") or "").strip()
    if source_id != str(contract.get("sourceId") or "").strip():
        raise RuntimeError("sourceId diverge do contrato")
    decision = review.get("decision") or {}
    if decision.get("identityResolved") is True:
        raise RuntimeError("identity review já resolvida")

    origin = decode_b64_text(contract.get("sourceOriginB64"), "sourceOriginB64")
    candidate = production_candidate(review)
    output = Path(args.output)
    source_dir = output / "source"
    candidate_dir = output / "candidate"
    source_manifest: list[dict[str, Any]] = []
    total_bytes = 0

    for row in source_rows(review):
        position = int(row["position"])
        source_path = str(row["sourcePath"])
        url = safe_source_url(origin, source_path)
        suffix = Path(urllib.parse.unquote(source_path)).suffix.lower() or ".bin"
        target = source_dir / f"position-{position:02d}{suffix}"
        actual_sha, actual_bytes, content_type = download(url, target, int(args.max_image_bytes))
        expected_sha = validate_sha(row["sha256"], f"expected source sha posição {position}")
        expected_bytes = int(row["bytes"])
        if actual_sha != expected_sha or actual_bytes != expected_bytes:
            raise RuntimeError(
                f"fonte posição {position} divergiu: {actual_sha}/{actual_bytes} != "
                f"{expected_sha}/{expected_bytes}"
            )
        total_bytes += actual_bytes
        source_manifest.append({
            "position": position,
            "sourceImageId": str(row.get("sourceImageId") or ""),
            "sourcePath": source_path,
            "sha256": actual_sha,
            "bytes": actual_bytes,
            "contentType": content_type,
            "artifactPath": str(target.relative_to(output)).replace("\\", "/"),
        })

    candidate_target = candidate_dir / "cover.webp"
    candidate_sha, candidate_bytes, candidate_content_type = download(
        public_media_url(args.api_base, candidate["coverStorageKey"]),
        candidate_target,
        int(args.max_image_bytes),
    )
    total_bytes += candidate_bytes
    if total_bytes > int(args.max_total_bytes):
        raise RuntimeError("identity review excedeu max-total-bytes")

    summary = {
        "version": 1,
        "sourceId": source_id,
        "sourceProductId": int(review.get("sourceProductId") or 0),
        "productName": str(review.get("productName") or ""),
        "sourceImages": source_manifest,
        "sourceImageCount": len(source_manifest),
        "productionCandidate": {
            **candidate,
            "artifactPath": str(candidate_target.relative_to(output)).replace("\\", "/"),
            "publicObjectSha256": candidate_sha,
            "publicObjectBytes": candidate_bytes,
            "contentType": candidate_content_type,
        },
        "allSourceBytesVerified": True,
        "candidateFetchedFromCanonicalPublicStorageKey": True,
        "visualDecisionMade": False,
        "identityResolved": False,
        "productionWrites": 0,
        "stagingWrites": 0,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "identity-review-manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("review")
    parser.add_argument("--source-contract", required=True)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-image-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--max-total-bytes", type=int, default=128 * 1024 * 1024)
    return parser.parse_args()


if __name__ == "__main__":
    materialize(parse_args())
