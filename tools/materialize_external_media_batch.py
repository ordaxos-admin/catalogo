#!/usr/bin/env python3
"""Materialize one reviewed external-media batch from private R2 staging.

The committed descriptor/evidence are the release authorization. Source URLs are
provenance only; release bytes must come from the already verified private
staging bucket and must reproduce the committed SHA-256 and byte count exactly.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
SAFE_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
EXPECTED_MODEL_ID_RE = re.compile(r"^mdl_[0-9a-f]{20}$")
EXPECTED_CODE_RE = re.compile(r"^TS-[0-9A-F]{12}$")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"JSON inválido: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"objeto JSON esperado: {path}")
    return data


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RuntimeError(f"linha JSONL inválida em {path}:{line_no}")
                rows.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"JSONL inválido: {path}: {exc}") from exc
    return rows


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


def evidence_extension(row: dict[str, Any]) -> str:
    ext = str(row.get("extension") or "").lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED_EXTS:
        raise RuntimeError(f"extensão de evidência não permitida: {ext!r}")
    return ext


def staging_key(prefix: str, row: dict[str, Any]) -> str:
    sha = str(row.get("sha256") or "").strip().lower()
    ext = evidence_extension(row)
    if not SHA_RE.fullmatch(sha):
        raise RuntimeError("SHA-256 inválido ao derivar staging key")
    return f"{prefix}/sha256/{sha[:2]}/{sha}.{ext}"


def validate_descriptor(data: dict[str, Any], path: Path) -> dict[int, dict[str, Any]]:
    if data.get("version") != 2:
        raise RuntimeError(f"versão de batch incompatível: {path}")
    source_id = str(data.get("sourceId") or "").strip()
    if not SAFE_ID_RE.fullmatch(source_id):
        raise RuntimeError(f"sourceId inválido: {source_id!r}")

    origin = decode_b64_text(data.get("sourceOriginB64"), "sourceOriginB64").rstrip("/")
    parsed = urllib.parse.urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RuntimeError("sourceOriginB64 não representa uma origem HTTPS válida")

    prefix = decode_b64_text(data.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    if not prefix or prefix in {".", ".."} or "\\" in prefix or any(part in {"", ".", ".."} for part in prefix.split("/")):
        raise RuntimeError("stagingPrefixB64 resolve para prefixo inseguro")

    for field in ("stagingBucket", "productionBucket"):
        value = str(data.get(field) or "").strip()
        if not SAFE_BUCKET_RE.fullmatch(value):
            raise RuntimeError(f"{field} inválido: {value!r}")
    if data["stagingBucket"] == data["productionBucket"]:
        raise RuntimeError("stagingBucket e productionBucket devem permanecer separados")

    products = data.get("products")
    if not isinstance(products, list) or not products:
        raise RuntimeError(f"batch sem produtos: {path}")
    expected_products = int(data.get("expectedProducts") or 0)
    expected_images = int(data.get("expectedImages") or 0)
    if expected_products != len(products) or expected_images < 1:
        raise RuntimeError("contagens esperadas do batch são inválidas")

    by_id: dict[int, dict[str, Any]] = {}
    seen_model_ids: set[str] = set()
    seen_slugs: set[str] = set()
    seen_codes: set[str] = set()
    for item in products:
        if not isinstance(item, dict):
            raise RuntimeError("produto do batch inválido")
        product_id = int(item.get("productId") or 0)
        display = str(item.get("displayName") or "").strip()
        public_key = str(item.get("publicModelKey") or "").strip()
        folders = item.get("sourceFolders")
        count = int(item.get("expectedImages") or 0)
        identity_key = str(item.get("expectedIdentityKey") or "").strip()
        model_id = str(item.get("expectedModelId") or "").strip()
        slug = str(item.get("expectedSlug") or "").strip()
        code = str(item.get("expectedCode") or "").strip()
        if product_id < 1 or not display or not public_key or count < 1 or not identity_key:
            raise RuntimeError(f"produto do batch incompleto: {item!r}")
        if not EXPECTED_MODEL_ID_RE.fullmatch(model_id) or not slug or not EXPECTED_CODE_RE.fullmatch(code):
            raise RuntimeError(f"identidade técnica esperada inválida para productId={product_id}")
        if not isinstance(folders, list) or len(folders) < 2 or any(
            not isinstance(part, str) or not part.strip() or part.strip() in {".", ".."}
            or "/" in part or "\\" in part for part in folders
        ):
            raise RuntimeError(f"sourceFolders inválido para productId={product_id}")
        expected_identity = " / ".join([*(str(part).strip() for part in folders), public_key])
        if identity_key != expected_identity:
            raise RuntimeError(f"expectedIdentityKey diverge da hierarquia para productId={product_id}")
        if product_id in by_id or model_id in seen_model_ids or slug in seen_slugs or code in seen_codes:
            raise RuntimeError(f"identidade duplicada no batch: productId={product_id}")
        by_id[product_id] = item
        seen_model_ids.add(model_id)
        seen_slugs.add(slug)
        seen_codes.add(code)
    if sum(int(item["expectedImages"]) for item in products) != expected_images:
        raise RuntimeError("expectedImages diverge da soma por produto")
    return by_id


def validate_evidence(rows: list[dict[str, Any]], descriptor: dict[str, Any], products: dict[int, dict[str, Any]]) -> None:
    if len(rows) != int(descriptor["expectedImages"]):
        raise RuntimeError(f"evidência deve conter {descriptor['expectedImages']} imagens; recebeu {len(rows)}")
    origin = decode_b64_text(descriptor.get("sourceOriginB64"), "sourceOriginB64").rstrip("/")
    prefix = decode_b64_text(descriptor.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    base = urllib.parse.urlsplit(origin)
    counts: Counter[int] = Counter()
    seen_sha: set[str] = set()
    seen_image: set[tuple[int, str]] = set()
    positions: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        product_id = int(row.get("productId") or 0)
        product = products.get(product_id)
        if product is None:
            raise RuntimeError(f"evidência contém productId fora do batch: {product_id}")
        image_id = str(row.get("imageId") or "").strip()
        position = int(row.get("position") or 0)
        source_path = str(row.get("sourcePath") or "").strip()
        sha = str(row.get("sha256") or "").strip().lower()
        size = int(row.get("bytes") or 0)
        if not image_id or position < 1 or not SHA_RE.fullmatch(sha) or size < 1:
            raise RuntimeError(f"linha de evidência inválida: productId={product_id}, imageId={image_id!r}")
        if not source_path.startswith("/") or "\\" in source_path:
            raise RuntimeError(f"sourcePath inválido: {source_path!r}")
        parsed = urllib.parse.urlsplit(origin + source_path)
        if parsed.scheme != "https" or parsed.netloc.casefold() != base.netloc.casefold() or ".." in parsed.path.split("/"):
            raise RuntimeError(f"sourcePath escapa da origem autorizada: {source_path}")
        if row.get("review") != "clear" or row.get("stagingVerified") is not True:
            raise RuntimeError(f"mídia sem revisão clear/stagingVerified: {product_id}/{image_id}")
        if str(row.get("publicModelKey") or "") != str(product["publicModelKey"]):
            raise RuntimeError(f"publicModelKey divergente: {product_id}/{image_id}")
        evidence_extension(row)
        staging_key(prefix, row)
        key = (product_id, image_id)
        if key in seen_image or sha in seen_sha or position in positions[product_id]:
            raise RuntimeError(f"identidade/posição/SHA duplicado na evidência: {product_id}/{image_id}")
        seen_image.add(key)
        seen_sha.add(sha)
        positions[product_id].add(position)
        counts[product_id] += 1
    for product_id, product in products.items():
        expected = int(product["expectedImages"])
        if counts[product_id] != expected or positions[product_id] != set(range(1, expected + 1)):
            raise RuntimeError(f"cobertura incompleta para productId={product_id}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_staged_object(repo_root: Path, bucket: str, key: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "npx", "wrangler", "r2", "object", "get", f"{bucket}/{key}",
        "--remote", "--file", str(destination),
    ]
    completed = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"falha ao materializar objeto staging {key}: {detail[:800]}")
    if not destination.is_file():
        raise RuntimeError(f"Wrangler não materializou objeto staging: {key}")


def materialize(descriptor_path: Path, output: Path) -> dict[str, Any]:
    descriptor = load_json(descriptor_path)
    products = validate_descriptor(descriptor, descriptor_path)
    repo_root = descriptor_path.resolve().parents[2]
    evidence_path = (repo_root / str(descriptor["evidence"])).resolve()
    if repo_root not in evidence_path.parents or not evidence_path.is_file():
        raise RuntimeError(f"evidência ausente ou fora do repositório: {evidence_path}")
    evidence = load_jsonl(evidence_path)
    validate_evidence(evidence, descriptor, products)

    if output.exists():
        shutil.rmtree(output)
    source_root = output / "source"
    source_root.mkdir(parents=True)
    registry_path = output / "audit-registry.csv"
    registry_rows: list[dict[str, str]] = []
    total_bytes = 0
    staging_bucket = str(descriptor["stagingBucket"])
    staging_prefix = decode_b64_text(descriptor["stagingPrefixB64"], "stagingPrefixB64").strip("/")
    code_prefix = re.sub(r"[^A-Z0-9]+", "", str(descriptor["sourceId"]).upper())[:20]

    for row in sorted(evidence, key=lambda item: (int(item["productId"]), int(item["position"]))):
        product_id = int(row["productId"])
        product = products[product_id]
        folders = [f"OK - {str(part).strip()}" for part in product["sourceFolders"]]
        destination_dir = source_root.joinpath(*folders)
        destination_dir.mkdir(parents=True, exist_ok=True)
        ext = evidence_extension(row)
        destination = destination_dir / f"{int(row['position']):02d}-{row['imageId']}.{ext}"
        key = staging_key(staging_prefix, row)
        get_staged_object(repo_root, staging_bucket, key, destination)
        digest = sha256_file(destination)
        size = destination.stat().st_size
        expected_sha = str(row["sha256"])
        expected_size = int(row["bytes"])
        if digest != expected_sha or size != expected_size:
            destination.unlink(missing_ok=True)
            raise RuntimeError(
                f"staging divergiu para productId={product_id}/imageId={row['imageId']}: "
                f"sha/bytes obtidos {digest}/{size}, esperados {expected_sha}/{expected_size}"
            )
        rel = str(destination.relative_to(source_root))
        registry_rows.append({
            "caminho": rel,
            "sha256": digest,
            "status": "OK_VISUAL|EXT_SOURCE_REVIEWED",
            "codigo": f"{code_prefix}-{product_id}-{row['imageId']}",
            "identificacao": str(product["displayName"]),
            "modelo_publico": str(product["publicModelKey"]),
        })
        total_bytes += size

    with registry_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["caminho", "sha256", "status", "codigo", "identificacao", "modelo_publico"],
        )
        writer.writeheader()
        writer.writerows(registry_rows)

    summary = {
        "batch": descriptor_path.stem,
        "sourceId": descriptor["sourceId"],
        "products": len(products),
        "images": len(registry_rows),
        "bytes": total_bytes,
        "stagingBucket": staging_bucket,
        "sourceRoot": str(source_root),
        "auditRegistry": str(registry_path),
        "evidence": str(evidence_path),
    }
    (output / "materialize-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Materializa lote externo revisado a partir do R2 staging privado.")
    parser.add_argument("descriptor", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.descriptor.is_file():
            raise RuntimeError(f"descriptor ausente: {args.descriptor}")
        print(json.dumps(materialize(args.descriptor.resolve(), args.output.resolve()), ensure_ascii=False))
        return 0
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
