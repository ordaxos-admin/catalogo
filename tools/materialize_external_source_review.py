#!/usr/bin/env python3
"""Materialize explicitly versioned external source images for visual review.

Read-only helper. Source URLs are reconstructed from the authorized source
origin plus repository-versioned sourcePath values. Exact SHA-256 and byte
length are verified before a short-lived review artifact is emitted. It never
writes R2 or D1 and never decides watermark eligibility.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "catalog-external-source-review/1.0"
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


def safe_source_url(origin: str, source_path: str) -> str:
    parsed_origin = urllib.parse.urlsplit(origin.rstrip("/"))
    if parsed_origin.scheme != "https" or not parsed_origin.netloc:
        raise RuntimeError("source origin inválida")
    path = str(source_path or "").strip()
    if not path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise RuntimeError("sourcePath inseguro")
    parsed_path = urllib.parse.urlsplit(path)
    if parsed_path.scheme or parsed_path.netloc or parsed_path.query or parsed_path.fragment:
        raise RuntimeError("sourcePath deve ser caminho canônico sem query/fragment")
    ext = Path(urllib.parse.unquote(parsed_path.path)).suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED_EXTS:
        raise RuntimeError(f"extensão não permitida: {ext!r}")
    return urllib.parse.urlunsplit((parsed_origin.scheme, parsed_origin.netloc, parsed_path.path, "", ""))


def _review_specs(source_map: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = source_map.get("reviewSources")
    if isinstance(explicit, list) and explicit:
        if any(not isinstance(row, dict) for row in explicit):
            raise RuntimeError("reviewSources contém entrada inválida")
        return list(explicit)
    legacy = source_map.get("missingSource")
    if isinstance(legacy, dict):
        return [legacy]
    raise RuntimeError("source map sem reviewSources/missingSource")


def selected_reviews(source_map: dict[str, Any]) -> list[dict[str, Any]]:
    images = source_map.get("images")
    if not isinstance(images, list):
        raise RuntimeError("source map sem images")
    selected: list[dict[str, Any]] = []
    seen_positions: set[int] = set()
    seen_shas: set[str] = set()
    for spec in _review_specs(source_map):
        position = int(spec.get("position") or 0)
        if position < 1 or position in seen_positions:
            raise RuntimeError("review source position inválida/duplicada")
        matches = [
            row for row in images
            if isinstance(row, dict) and int(row.get("position") or 0) == position
        ]
        if len(matches) != 1:
            raise RuntimeError("review source não corresponde a uma imagem única")
        row = matches[0]
        for field in ("sourcePath", "sha256", "bytes"):
            if str(row.get(field)) != str(spec.get(field)):
                raise RuntimeError(f"review source diverge da imagem em {field}")
        if row.get("stagingVerified") is not False:
            raise RuntimeError("review source deve continuar ausente do staging")
        if str(row.get("reviewState") or "") != "visual_review_required":
            raise RuntimeError("review source não está marcado para revisão visual")
        sha = validate_sha(row.get("sha256"), "sha256")
        if sha in seen_shas:
            raise RuntimeError("review source SHA duplicado")
        if int(row.get("bytes") or 0) < 1:
            raise RuntimeError("review source sem bytes esperados")
        seen_positions.add(position)
        seen_shas.add(sha)
        selected.append(row)
    return selected


def selected_review(source_map: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible single-review accessor used by older tests/callers."""
    rows = selected_reviews(source_map)
    if len(rows) != 1:
        raise RuntimeError("source map possui múltiplas imagens de revisão")
    return rows[0]


def download(url: str, target: Path, max_bytes: int) -> tuple[str, int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    content_type = ""
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as handle:
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            raise RuntimeError(f"source não retornou imagem: {content_type or '<vazio>'}")
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"source excede limite de {max_bytes} bytes")
            digest.update(chunk)
            handle.write(chunk)
    if total < 1:
        raise RuntimeError("source vazio")
    return digest.hexdigest(), total, content_type


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    source_map = load_json(Path(args.source_map))
    contract = load_json(Path(args.source_contract))
    if str(source_map.get("sourceId") or "") != str(contract.get("sourceId") or ""):
        raise RuntimeError("sourceId diverge do contrato")
    rows = selected_reviews(source_map)
    origin = decode_b64_text(contract.get("sourceOriginB64"), "sourceOriginB64")
    output = Path(args.output)
    review_dir = output / "review"
    review_files: list[dict[str, Any]] = []
    for row in rows:
        url = safe_source_url(origin, str(row["sourcePath"]))
        expected_sha = validate_sha(row["sha256"], "expected sha256")
        expected_bytes = int(row["bytes"])
        suffix = Path(urllib.parse.unquote(str(row["sourcePath"]))).suffix.lower()
        target = review_dir / f"position-{int(row['position']):02d}{suffix}"
        actual_sha, actual_bytes, content_type = download(url, target, int(args.max_bytes))
        if actual_sha != expected_sha or actual_bytes != expected_bytes:
            raise RuntimeError(
                "source atual divergiu da evidência versionada: "
                f"position={row['position']} sha={actual_sha} bytes={actual_bytes} "
                f"esperado={expected_sha}/{expected_bytes}"
            )
        review_files.append({
            "position": int(row["position"]),
            "sourcePath": row["sourcePath"],
            "sha256": actual_sha,
            "bytes": actual_bytes,
            "contentType": content_type,
            "artifactPath": str(target.relative_to(output)).replace("\\", "/"),
        })
    summary = {
        "version": 1,
        "sourceId": source_map["sourceId"],
        "targetModelId": source_map.get("targetModelId"),
        "reviewFiles": review_files,
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
    parser.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    return parser.parse_args()


if __name__ == "__main__":
    materialize(parse_args())
