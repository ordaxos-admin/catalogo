#!/usr/bin/env python3
"""Materialize only explicitly blocked perceptual-review pairs from private staging.

Read-only evidence helper: exact SHA/size are revalidated before short-lived review
artifacts are emitted. It never writes to R2 or D1 and never decides the review.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "catalog-staged-perceptual-review/1.0"
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
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{field} inválido") from exc
    if not decoded:
        raise RuntimeError(f"{field} vazio")
    return decoded


def validate_sha(value: Any, label: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise RuntimeError(f"{label} inválido: {value!r}")
    return digest


def asset_extension(asset: dict[str, Any]) -> str:
    raw = str(asset.get("file") or asset.get("sourceObjectPath") or "")
    ext = Path(urllib.parse.unquote(raw)).suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED_EXTS:
        raise RuntimeError(f"extensão não permitida: {raw!r}")
    return ext


def staging_key(prefix: str, sha: str, ext: str) -> str:
    return f"{prefix.strip('/')}/sha256/{sha[:2]}/{sha}.{ext}"


def review_assets(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    assets = evidence.get("assets")
    review = evidence.get("perceptualReview")
    if not isinstance(assets, list) or not isinstance(review, dict):
        raise RuntimeError("evidência sem assets/perceptualReview")
    by_sha: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        sha = validate_sha(asset.get("sha256"), "asset sha256")
        if sha in by_sha:
            raise RuntimeError(f"asset SHA duplicado: {sha}")
        by_sha[sha] = asset

    pairs = review.get("candidatePairs")
    if not isinstance(pairs, list) or not pairs:
        raise RuntimeError("nenhum candidato perceptual explícito")
    selected: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        if not isinstance(pair, dict):
            raise RuntimeError("candidato perceptual inválido")
        if pair.get("state") != "explicit_visual_decision_required" or pair.get("automaticPromotionAllowed") is not False:
            raise RuntimeError("candidato perceptual não está fail-closed para revisão explícita")
        for field in ("leftSha256", "rightSha256"):
            sha = validate_sha(pair.get(field), field)
            if sha not in by_sha:
                raise RuntimeError(f"candidato perceptual sem asset correspondente: {sha}")
            selected[sha] = by_sha[sha]
    return list(selected.values())


def download_object(account_id: str, token: str, bucket: str, key: str, target: Path) -> None:
    encoded = urllib.parse.quote(key, safe="/")
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{encoded}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT})
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as handle:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            handle.write(chunk)


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    evidence = load_json(Path(args.evidence))
    contract = load_json(Path(args.source_contract))
    source_id = str(evidence.get("sourceId") or "").strip()
    if source_id != str(contract.get("sourceId") or "").strip():
        raise RuntimeError("sourceId da evidência diverge do contrato")
    account_id = str(os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = str(os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if not account_id or not token:
        raise RuntimeError("credenciais Cloudflare ausentes")
    bucket = str(contract.get("stagingBucket") or "").strip()
    prefix = decode_b64_text(contract.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    if not bucket or not prefix:
        raise RuntimeError("contrato staging incompleto")

    output = Path(args.output)
    review_dir = output / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for asset in review_assets(evidence):
        sha = validate_sha(asset.get("sha256"), "asset sha256")
        ext = asset_extension(asset)
        label = str(asset.get("file") or f"{sha}.{ext}").strip()
        target = review_dir / label
        download_object(account_id, token, bucket, staging_key(prefix, sha, ext), target)
        actual_sha, actual_size = sha256_file(target)
        expected_size = int(asset.get("bytes") or 0)
        if actual_sha != sha or actual_size != expected_size:
            raise RuntimeError(f"bytes staging divergentes para {label}")
        rows.append({"file": label, "sha256": sha, "bytes": actual_size})

    summary = {
        "version": 1,
        "sourceId": source_id,
        "sourceProductId": evidence.get("sourceProductId"),
        "reviewFiles": rows,
        "reviewDecisionMade": False,
        "productionWrites": 0,
        "stagingWrites": 0,
    }
    (output / "review-manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence")
    parser.add_argument("--source-contract", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    materialize(parse_args())
