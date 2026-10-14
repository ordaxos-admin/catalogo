#!/usr/bin/env python3
"""Materialize one reviewed external-media batch from versioned evidence.

The batch descriptor owns taxonomy and product grouping. The evidence owns source
URLs, hashes, sizes and review decisions. Network bytes are accepted only when
they reproduce the committed evidence exactly; the source is never trusted as a
mutable SSOT.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


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


def validate_descriptor(data: dict[str, Any], path: Path) -> dict[int, dict[str, Any]]:
    if data.get("version") != 1:
        raise RuntimeError(f"versão de batch incompatível: {path}")
    origin = str(data.get("sourceOrigin") or "").rstrip("/")
    parsed = urllib.parse.urlsplit(origin)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise RuntimeError(f"sourceOrigin inválido: {origin!r}")
    products = data.get("products")
    if not isinstance(products, list) or not products:
        raise RuntimeError(f"batch sem produtos: {path}")
    expected_products = int(data.get("expectedProducts") or 0)
    expected_images = int(data.get("expectedImages") or 0)
    if expected_products != len(products) or expected_images < 1:
        raise RuntimeError("contagens esperadas do batch são inválidas")
    by_id: dict[int, dict[str, Any]] = {}
    for item in products:
        if not isinstance(item, dict):
            raise RuntimeError("produto do batch inválido")
        product_id = int(item.get("productId") or 0)
        display = str(item.get("displayName") or "").strip()
        public_key = str(item.get("publicModelKey") or "").strip()
        folders = item.get("sourceFolders")
        count = int(item.get("expectedImages") or 0)
        if product_id < 1 or not display or not public_key or count < 1:
            raise RuntimeError(f"produto do batch incompleto: {item!r}")
        if not isinstance(folders, list) or len(folders) < 2 or any(
            not isinstance(part, str) or not part.strip() or part.strip() in {".", ".."}
            or "/" in part or "\\" in part
            for part in folders
        ):
            raise RuntimeError(f"sourceFolders inválido para productId={product_id}")
        if product_id in by_id:
            raise RuntimeError(f"productId duplicado no batch: {product_id}")
        by_id[product_id] = item
    if sum(int(item["expectedImages"]) for item in products) != expected_images:
        raise RuntimeError("expectedImages diverge da soma por produto")
    return by_id


def evidence_extension(row: dict[str, Any]) -> str:
    ext = str(row.get("extension") or "").lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED_EXTS:
        raise RuntimeError(f"extensão de evidência não permitida: {ext!r}")
    return ext


def validate_evidence(
    rows: list[dict[str, Any]],
    descriptor: dict[str, Any],
    products: dict[int, dict[str, Any]],
) -> None:
    if len(rows) != int(descriptor["expectedImages"]):
        raise RuntimeError(f"evidência deve conter {descriptor['expectedImages']} imagens; recebeu {len(rows)}")
    origin = str(descriptor["sourceOrigin"]).rstrip("/")
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
        url = str(row.get("sourceUrl") or "").strip()
        sha = str(row.get("sha256") or "").strip().lower()
        size = int(row.get("bytes") or 0)
        if not image_id or position < 1 or not SHA_RE.fullmatch(sha) or size < 1:
            raise RuntimeError(f"linha de evidência inválida: productId={product_id}, imageId={image_id!r}")
        parsed = urllib.parse.urlsplit(url)
        base = urllib.parse.urlsplit(origin)
        if parsed.scheme != "https" or parsed.netloc.casefold() != base.netloc.casefold():
            raise RuntimeError(f"URL fora da origem autorizada: {url}")
        if row.get("review") != "clear" or row.get("stagingVerified") is not True:
            raise RuntimeError(f"mídia sem revisão clear/stagingVerified: {product_id}/{image_id}")
        if str(row.get("publicModelKey") or "") != str(product["publicModelKey"]):
            raise RuntimeError(f"publicModelKey divergente: {product_id}/{image_id}")
        evidence_extension(row)
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


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "TonecosExternalBatch/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"falha ao obter mídia externa: {url}: {exc}") from exc


def materialize(descriptor_path: Path, output: Path) -> dict[str, Any]:
    descriptor = load_json(descriptor_path)
    products = validate_descriptor(descriptor, descriptor_path)
    evidence_path = (descriptor_path.parents[1] / str(descriptor["evidence"])).resolve()
    # Descriptor usually lives under config/external-batches; resolve repository-relative evidence.
    repo_root = descriptor_path.resolve().parents[2]
    evidence_path = (repo_root / str(descriptor["evidence"])).resolve()
    if not evidence_path.is_file():
        raise RuntimeError(f"evidência ausente: {evidence_path}")
    evidence = load_jsonl(evidence_path)
    validate_evidence(evidence, descriptor, products)

    if output.exists():
        shutil.rmtree(output)
    source_root = output / "source"
    source_root.mkdir(parents=True)
    registry_path = output / "audit-registry.csv"
    registry_rows: list[dict[str, str]] = []
    downloaded = 0

    for row in sorted(evidence, key=lambda item: (int(item["productId"]), int(item["position"]))):
        product_id = int(row["productId"])
        product = products[product_id]
        data = fetch_bytes(str(row["sourceUrl"]))
        digest = hashlib.sha256(data).hexdigest()
        expected_sha = str(row["sha256"])
        expected_size = int(row["bytes"])
        if digest != expected_sha or len(data) != expected_size:
            raise RuntimeError(
                f"fonte mudou para productId={product_id}/imageId={row['imageId']}: "
                f"sha/bytes obtidos {digest}/{len(data)}, esperados {expected_sha}/{expected_size}"
            )
        folders = [f"OK - {str(part).strip()}" for part in product["sourceFolders"]]
        destination_dir = source_root.joinpath(*folders)
        destination_dir.mkdir(parents=True, exist_ok=True)
        ext = evidence_extension(row)
        destination = destination_dir / f"{int(row['position']):02d}-{row['imageId']}.{ext}"
        destination.write_bytes(data)
        rel = str(destination.relative_to(source_root))
        registry_rows.append({
            "caminho": rel,
            "sha256": digest,
            "status": "OK_VISUAL|EXT_SOURCE_REVIEWED",
            "codigo": f"STLFORGE-{product_id}-{row['imageId']}",
            "identificacao": str(product["displayName"]),
            "modelo_publico": str(product["publicModelKey"]),
        })
        downloaded += len(data)

    with registry_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["caminho", "sha256", "status", "codigo", "identificacao", "modelo_publico"],
        )
        writer.writeheader()
        writer.writerows(registry_rows)

    summary = {
        "batch": descriptor_path.stem,
        "products": len(products),
        "images": len(registry_rows),
        "bytes": downloaded,
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
    parser = argparse.ArgumentParser(description="Materializa um lote externo somente se reproduzir evidência versionada.")
    parser.add_argument("descriptor", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.descriptor.is_file():
            raise RuntimeError(f"descriptor ausente: {args.descriptor}")
        print(json.dumps(materialize(args.descriptor.resolve(), args.output.resolve()), ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
