#!/usr/bin/env python3
"""Discover bounded external-media candidates without authorizing publication.

This tool is intentionally read-only with respect to Cloudflare storage and D1.
It fetches either one exact Store API product or a small source API page, hashes
source images, checks exact private-staging ownership, and checks global
production SHA ownership. Nothing here resolves identity or watermark status and
nothing here creates or applies a release batch.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}
DEFAULT_USER_AGENT = "catalog-external-candidate-discovery/1.0"
REVIEW_ARTIFACT_STATES = {
    "visual_review_required",
    "identity_review_required_exact_sha_overlap",
    "incomplete_staging",
}


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


def safe_source_path(origin: str, url: str) -> str:
    base = urllib.parse.urlsplit(origin.rstrip("/"))
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != base.netloc.casefold():
        raise RuntimeError("imagem escapa da origem autorizada")
    if not parsed.path.startswith("/") or ".." in parsed.path.split("/"):
        raise RuntimeError("sourcePath inseguro")
    if parsed.query or parsed.fragment:
        raise RuntimeError("URL de mídia canônica não pode conter query/fragment")
    return parsed.path


def canonical_source_url(origin: str, source_path: str) -> str:
    if not source_path.startswith("/") or ".." in source_path.split("/"):
        raise RuntimeError("sourcePath inseguro")
    return origin.rstrip("/") + urllib.parse.quote(source_path, safe="/%:@")


def extension_from_path(path: str) -> str:
    ext = Path(urllib.parse.unquote(path)).suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED_EXTS:
        raise RuntimeError(f"extensão de mídia não permitida: {ext!r}")
    return ext


def staging_key(prefix: str, sha256: str, extension: str) -> str:
    sha = sha256.strip().lower()
    ext = extension.strip().lower().lstrip(".")
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
        raise RuntimeError("SHA-256 inválido")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED_EXTS:
        raise RuntimeError("extensão inválida")
    return f"{prefix.strip('/')}/sha256/{sha[:2]}/{sha}.{ext}"


def request_json(url: str, *, token: str | None = None, timeout: int = 45) -> Any:
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], *, token: str, timeout: int = 45) -> Any:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_staging_inventory(account_id: str, token: str, bucket: str, prefix: str) -> dict[str, int]:
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects"
    inventory: dict[str, int] = {}
    cursor: str | None = None
    while True:
        query: dict[str, str | int] = {"per_page": 1000, "prefix": f"{prefix.strip('/')}/sha256/"}
        if cursor:
            query["cursor"] = cursor
        payload = request_json(endpoint + "?" + urllib.parse.urlencode(query), token=token)
        if not payload.get("success"):
            raise RuntimeError(f"falha ao listar staging: {payload.get('errors')}")
        for row in payload.get("result") or []:
            key = str(row.get("key") or "")
            size = int(row.get("size") or 0)
            if key:
                inventory[key] = size
        info = payload.get("result_info") or {}
        if not info.get("is_truncated"):
            break
        cursor = str(info.get("cursor") or "")
        if not cursor:
            raise RuntimeError("staging truncado sem cursor")
    return inventory


def fetch_source_image(url: str, destination: Path, max_bytes: int) -> tuple[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"imagem excede limite individual de {max_bytes} bytes")
            digest.update(chunk)
            handle.write(chunk)
    if total < 1:
        raise RuntimeError("imagem vazia")
    return digest.hexdigest(), total


def query_production_owners(
    account_id: str,
    token: str,
    database_id: str,
    shas: list[str],
) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {sha: [] for sha in shas}
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
    for offset in range(0, len(shas), 80):
        chunk = shas[offset : offset + 80]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        payload = post_json(
            endpoint,
            {
                "sql": f"SELECT source_sha256, model_id FROM model_image_sources WHERE source_sha256 IN ({placeholders})",
                "params": chunk,
            },
            token=token,
        )
        if not payload.get("success"):
            raise RuntimeError(f"falha no ownership D1: {payload.get('errors')}")
        for result in payload.get("result") or []:
            for row in result.get("results") or []:
                sha = str(row.get("source_sha256") or "")
                model_id = str(row.get("model_id") or "")
                if sha in owners and model_id and model_id not in owners[sha]:
                    owners[sha].append(model_id)
    return owners


def candidate_state(images: list[dict[str, Any]]) -> str:
    if not images:
        return "empty_gallery"
    if any(not row.get("stagingVerified") for row in images):
        return "incomplete_staging"
    if any(row.get("productionOwners") for row in images):
        return "identity_review_required_exact_sha_overlap"
    return "visual_review_required"


def review_artifact_eligible(state: str) -> bool:
    """Keep exact pixels for read-only review even when staging is incomplete."""
    return str(state or "") in REVIEW_ARTIFACT_STATES


def select_products(
    products: list[dict[str, Any]],
    max_products: int,
    product_id: int | None = None,
) -> list[dict[str, Any]]:
    if product_id is None:
        return products[:max_products]
    target = int(product_id)
    if target < 1:
        raise RuntimeError("product-id deve ser positivo")
    matches = [row for row in products if int(row.get("id") or 0) == target]
    if len(matches) != 1:
        raise RuntimeError(
            f"product-id {target} deve existir exatamente uma vez na página consultada; encontrado={len(matches)}"
        )
    return matches


def validate_exact_product(product: Any, product_id: int) -> dict[str, Any]:
    target = int(product_id)
    if target < 1:
        raise RuntimeError("product-id deve ser positivo")
    if not isinstance(product, dict):
        raise RuntimeError("Store API de produto exato não retornou objeto")
    found = int(product.get("id") or 0)
    if found != target:
        raise RuntimeError(f"endpoint exato retornou product-id {found}; esperado={target}")
    return product


def discover(args: argparse.Namespace) -> dict[str, Any]:
    descriptor = load_json(Path(args.descriptor))
    origin = decode_b64_text(descriptor.get("sourceOriginB64"), "sourceOriginB64").rstrip("/")
    prefix = decode_b64_text(descriptor.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    bucket = str(descriptor.get("stagingBucket") or "").strip()
    source_id = str(descriptor.get("sourceId") or "").strip()
    if not bucket or not source_id:
        raise RuntimeError("descriptor sem stagingBucket/sourceId")

    account_id = str(os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = str(os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    database_id = str(os.environ.get("CLOUDFLARE_D1_DATABASE_ID") or "").strip()
    if not account_id or not token or not database_id:
        raise RuntimeError("bindings Cloudflare ausentes")

    per_page = min(max(int(args.per_page), 1), 20)
    max_products = min(max(int(args.max_products), 1), per_page)
    selection_mode = "bounded_page"
    if args.product_id is not None:
        target = int(args.product_id)
        raw_product = request_json(origin + f"/wp-json/wc/store/v1/products/{target}")
        products = [validate_exact_product(raw_product, target)]
        selection_mode = "exact_product_endpoint"
    else:
        api_query = urllib.parse.urlencode({"per_page": per_page, "page": int(args.page)})
        raw_products = request_json(origin + "/wp-json/wc/store/v1/products?" + api_query)
        if not isinstance(raw_products, list):
            raise RuntimeError("Store API não retornou lista")
        products = select_products(raw_products, max_products)

    inventory = list_staging_inventory(account_id, token, bucket, prefix)
    output = Path(args.output)
    review_root = output / "review"
    output.mkdir(parents=True, exist_ok=True)
    review_root.mkdir(parents=True, exist_ok=True)

    product_rows: list[dict[str, Any]] = []
    total_source_bytes = 0
    max_total_bytes = int(args.max_total_bytes)
    max_image_bytes = int(args.max_image_bytes)

    with tempfile.TemporaryDirectory(prefix="catalog-discovery-") as temp_dir:
        temp_root = Path(temp_dir)
        for product in products:
            product_id = int(product.get("id") or 0)
            source_images = product.get("images") or []
            if product_id < 1 or not isinstance(source_images, list) or not source_images:
                continue
            product_dir = temp_root / str(product_id)
            image_rows: list[dict[str, Any]] = []
            bounded_stop = False
            for position, image in enumerate(source_images, 1):
                raw_source_url = str(image.get("src") or "")
                source_path = safe_source_path(origin, raw_source_url)
                source_url = canonical_source_url(origin, source_path)
                ext = extension_from_path(source_path)
                target = product_dir / f"{position:02d}.{ext}"
                sha, size = fetch_source_image(source_url, target, max_image_bytes)
                total_source_bytes += size
                if total_source_bytes > max_total_bytes:
                    bounded_stop = True
                    break
                key = staging_key(prefix, sha, ext)
                staged_size = inventory.get(key)
                image_rows.append(
                    {
                        "imageId": str(image.get("id") or position),
                        "position": position,
                        "sourcePath": source_path,
                        "extension": ext,
                        "sha256": sha,
                        "bytes": size,
                        "stagingVerified": staged_size == size,
                        "stagingSize": staged_size,
                        "productionOwners": [],
                    }
                )
            if bounded_stop:
                product_rows.append(
                    {
                        "productId": product_id,
                        "productName": str(product.get("name") or ""),
                        "sourceImageCount": len(source_images),
                        "state": "discovery_byte_limit_reached",
                        "images": image_rows,
                    }
                )
                break
            product_rows.append(
                {
                    "productId": product_id,
                    "productName": str(product.get("name") or ""),
                    "slug": str(product.get("slug") or ""),
                    "brands": [str(row.get("name") or "") for row in (product.get("brands") or []) if row.get("name")],
                    "categories": [str(row.get("name") or "") for row in (product.get("categories") or []) if row.get("name")],
                    "sourceImageCount": len(source_images),
                    "images": image_rows,
                }
            )

        all_shas = [row["sha256"] for product in product_rows for row in product.get("images", [])]
        ownership = query_production_owners(account_id, token, database_id, all_shas)
        review_kept = 0
        for product in product_rows:
            for image in product.get("images", []):
                image["productionOwners"] = ownership.get(image["sha256"], [])
            if product.get("state") == "discovery_byte_limit_reached":
                continue
            product["stagingCoverage"] = f"{sum(1 for row in product['images'] if row['stagingVerified'])}/{product['sourceImageCount']}"
            product["state"] = candidate_state(product["images"])
            product["watermarkReview"] = "required"
            product["identityDecision"] = "unresolved"
            product["promotable"] = False
            if review_artifact_eligible(str(product["state"])) and review_kept < int(args.max_review_products):
                source_dir = temp_root / str(product["productId"])
                destination = review_root / f"product-{product['productId']}"
                if source_dir.exists():
                    shutil.copytree(source_dir, destination)
                    product["reviewArtifactPath"] = str(destination.relative_to(output)).replace("\\", "/")
                    review_kept += 1

    report = {
        "version": 2,
        "sourceId": source_id,
        "page": int(args.page),
        "perPage": per_page,
        "selectionMode": selection_mode,
        "requestedProductId": args.product_id,
        "productsScanned": len(product_rows),
        "sourceBytesHashed": total_source_bytes,
        "stagingInventoryObjects": len(inventory),
        "reviewArtifactPolicy": "complete_or_incomplete_read_only",
        "productionWrites": 0,
        "stagingWrites": 0,
        "products": product_rows,
    }
    (output / "discovery-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    complete_states = {"visual_review_required", "identity_review_required_exact_sha_overlap"}
    summary = {
        "selectionMode": selection_mode,
        "requestedProductId": args.product_id,
        "productsScanned": len(product_rows),
        "completeStaging": sum(1 for row in product_rows if row.get("state") in complete_states),
        "visualReviewRequired": sum(1 for row in product_rows if row.get("state") == "visual_review_required"),
        "identityReviewRequired": sum(1 for row in product_rows if row.get("state") == "identity_review_required_exact_sha_overlap"),
        "incompleteStaging": sum(1 for row in product_rows if row.get("state") == "incomplete_staging"),
        "reviewArtifacts": sum(1 for row in product_rows if row.get("reviewArtifactPath")),
        "productionWrites": 0,
        "stagingWrites": 0,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor", default="config/external-batches/external-source-001-batch-004.json")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--per-page", type=int, default=10)
    parser.add_argument("--max-products", type=int, default=10)
    parser.add_argument("--product-id", type=int)
    parser.add_argument("--max-review-products", type=int, default=3)
    parser.add_argument("--max-image-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--max-total-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    discover(parse_args())
