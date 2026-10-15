#!/usr/bin/env python3
"""Materialize a versioned external gallery into a short-lived visual-review artifact.

The operation is read-only. Every source URL is reconstructed from the authorized
source origin, then SHA-256 and byte length are verified against repository evidence.
No R2/D1 write occurs and no watermark decision is made automatically.
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


def review_rows(source_map: dict[str, Any]) -> list[dict[str, Any]]:
    images = source_map.get("images")
    if not isinstance(images, list) or not images:
        raise RuntimeError("source map sem images")
    rows: list[dict[str, Any]] = []
    positions: set[int] = set()
    for raw in images:
        if not isinstance(raw, dict):
            raise RuntimeError("imagem inválida no source map")
        position = int(raw.get("position") or 0)
        if position < 1 or position in positions:
            raise RuntimeError("posição de imagem inválida/duplicada")
        positions.add(position)
        validate_sha(raw.get("sha256"), f"sha256 posição {position}")
        if int(raw.get("bytes") or 0) < 1:
            raise RuntimeError(f"bytes inválidos na posição {position}")
        if str(raw.get("reviewState") or "") != "visual_review_required":
            raise RuntimeError(f"posição {position} não exige revisão visual")
        source_path = str(raw.get("sourcePath") or "").strip()
        if not source_path:
            raise RuntimeError(f"sourcePath ausente na posição {position}")
        rows.append(raw)
    rows.sort(key=lambda row: int(row["position"]))
    expected = list(range(1, len(rows) + 1))
    actual = [int(row["position"]) for row in rows]
    if actual != expected:
        raise RuntimeError(f"galeria exige posições contíguas: {actual} != {expected}")
    return rows


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    source_map = load_json(Path(args.source_map))
    contract = load_json(Path(args.source_contract))
    source_id = str(source_map.get("sourceId") or "").strip()
    if source_id != str(contract.get("sourceId") or "").strip():
        raise RuntimeError("sourceId diverge do contrato")
    if source_map.get("decision", {}).get("sourceReviewComplete") is True:
        raise RuntimeError("source review já está concluída")

    origin = decode_b64_text(contract.get("sourceOriginB64"), "sourceOriginB64")
    output = Path(args.output)
    review_dir = output / "review"
    manifest_rows: list[dict[str, Any]] = []
    total_bytes = 0

    for row in review_rows(source_map):
        position = int(row["position"])
        source_path = str(row["sourcePath"])
        url = safe_source_url(origin, source_path)
        suffix = Path(urllib.parse.unquote(source_path)).suffix.lower()
        target = review_dir / f"position-{position:02d}{suffix}"
        actual_sha, actual_bytes, content_type = download(url, target, int(args.max_image_bytes))
        expected_sha = validate_sha(row["sha256"], f"expected sha256 posição {position}")
        expected_bytes = int(row["bytes"])
        if actual_sha != expected_sha or actual_bytes != expected_bytes:
            raise RuntimeError(
                f"posição {position} divergiu da evidência: "
                f"sha={actual_sha} bytes={actual_bytes} esperado={expected_sha}/{expected_bytes}"
            )
        total_bytes += actual_bytes
        if total_bytes > int(args.max_total_bytes):
            raise RuntimeError("galeria excedeu max-total-bytes")
        manifest_rows.append({
            "sourceImageId": str(row.get("sourceImageId") or ""),
            "position": position,
            "sourcePath": source_path,
            "sha256": actual_sha,
            "bytes": actual_bytes,
            "contentType": content_type,
            "stagingVerified": bool(row.get("stagingVerified")),
            "artifactPath": str(target.relative_to(output)).replace("\\", "/"),
            "visualDecisionMade": False,
        })

    summary = {
        "version": 1,
        "sourceId": source_id,
        "sourceProductId": int(source_map.get("sourceProductId") or 0),
        "productName": str(source_map.get("productName") or ""),
        "images": manifest_rows,
        "imageCount": len(manifest_rows),
        "bytes": total_bytes,
        "allSourceBytesVerified": True,
        "visualDecisionMade": False,
        "productionWrites": 0,
        "stagingWrites": 0,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "review-manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_map")
    parser.add_argument("--source-contract", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-image-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--max-total-bytes", type=int, default=96 * 1024 * 1024)
    return parser.parse_args()


if __name__ == "__main__":
    materialize(parse_args())
