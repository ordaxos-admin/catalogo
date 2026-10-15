#!/usr/bin/env python3
"""Build a resumable reverse index from private staged SHA-256 objects to source products.

Read-only by design: lists staging, reads the public Store API and hashes source
images. It never writes R2/D1 and never authorizes publication. Only products
with at least one exact staged SHA are retained in checkpoint/report.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from discover_external_staging_candidates import (
    DEFAULT_USER_AGENT, decode_b64_text, extension_from_path, fetch_source_image,
    list_staging_inventory, load_json, request_json, safe_source_path, staging_key,
)

STATE_VERSION = 3
SOURCE_ORDERING = "id_asc"


def empty_state(source_id: str, staging_objects: int) -> dict[str, Any]:
    return {
        "version": STATE_VERSION, "sourceId": source_id, "sourceOrdering": SOURCE_ORDERING,
        "stagingInventoryObjects": staging_objects, "pagesCompleted": [],
        "productsScanned": 0, "imagesHashed": 0, "sourceBytesHashed": 0,
        "matchedProducts": {}, "productionWrites": 0, "stagingWrites": 0,
        "nextPage": None, "nextProductOffset": 0,
    }


def load_state(path: Path, source_id: str, staging_objects: int) -> dict[str, Any]:
    if not path.exists():
        return empty_state(source_id, staging_objects)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        raise RuntimeError("checkpoint de índice inválido")
    if data.get("sourceId") != source_id:
        raise RuntimeError("checkpoint pertence a outra fonte")
    if data.get("sourceOrdering") != SOURCE_ORDERING:
        raise RuntimeError("checkpoint usa ordenação de fonte incompatível")
    if data.get("productionWrites") != 0 or data.get("stagingWrites") != 0:
        raise RuntimeError("checkpoint viola contrato read-only")
    if not isinstance(data.get("pagesCompleted"), list) or not isinstance(data.get("matchedProducts"), dict):
        raise RuntimeError("checkpoint incompleto")
    offset = data.get("nextProductOffset", 0)
    if not isinstance(offset, int) or offset < 0:
        raise RuntimeError("checkpoint com nextProductOffset inválido")
    next_page = data.get("nextPage")
    if next_page is not None and (not isinstance(next_page, int) or next_page < 1):
        raise RuntimeError("checkpoint com nextPage inválido")
    data["stagingInventoryObjects"] = staging_objects
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def summarize_product(product: dict[str, Any], images: list[dict[str, Any]]) -> dict[str, Any]:
    staged = [row for row in images if row["stagingVerified"]]
    review_only = [row for row in images if row.get("reviewOnly") is True]
    return {
        "productId": int(product.get("id") or 0), "productName": str(product.get("name") or ""),
        "slug": str(product.get("slug") or ""),
        "brands": [str(row.get("name") or "") for row in (product.get("brands") or []) if row.get("name")],
        "categories": [str(row.get("name") or "") for row in (product.get("categories") or []) if row.get("name")],
        "sourceImageCount": len(images), "stagedImageCount": len(staged),
        "reviewOnlyImageCount": len(review_only),
        "stagingCoverage": f"{len(staged)}/{len(images)}",
        "completeStaging": bool(images) and len(staged) == len(images), "images": images,
    }


def inventory_sizes_by_extension(inventory: dict[str, int]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for key, raw_size in inventory.items():
        size = int(raw_size or 0)
        if size < 1:
            continue
        extension = Path(key).suffix.lower().lstrip(".")
        if extension == "jpeg":
            extension = "jpg"
        if extension:
            result.setdefault(extension, set()).add(size)
    return result


def canonical_source_url(origin: str, source_path: str) -> str:
    """Rebuild one validated same-origin media URL using an ASCII request path."""
    if not source_path.startswith("/") or ".." in source_path.split("/"):
        raise RuntimeError("sourcePath inseguro")
    encoded_path = urllib.parse.quote(source_path, safe="/%:@")
    return origin.rstrip("/") + encoded_path


def fetch_source_size(url: str, timeout: int = 30) -> int | None:
    """Return exact identity-encoded Content-Length when safely available.

    Missing/ambiguous metadata is not an error: callers must fall back to the
    full SHA-256 path so this optimization cannot create false negatives.
    """
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Encoding": "identity"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            encoding = str(response.headers.get("Content-Encoding") or "").strip().casefold()
            if encoding not in {"", "identity"}:
                return None
            raw = str(response.headers.get("Content-Length") or "").strip()
            if not raw:
                return None
            size = int(raw)
            return size if size > 0 else None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        return None


def oversized_review_row(
    image: dict[str, Any], position: int, source_path: str, extension: str,
    *, known_size: int | None,
) -> dict[str, Any]:
    return {
        "sourceImageId": str(image.get("id") or position), "position": position,
        "sourcePath": source_path, "extension": extension, "sha256": None,
        "bytes": known_size, "stagingKey": None, "stagingVerified": False,
        "reviewOnly": True,
        "hashSkippedReason": (
            "source_exceeds_max_image_bytes"
            if known_size is not None
            else "source_exceeds_max_image_bytes_unknown_length"
        ),
    }


def scan_page(
    *, origin: str, page: int, per_page: int, inventory: dict[str, int],
    staging_prefix: str, max_image_bytes: int, remaining_bytes: int,
    product_offset: int = 0,
    fetch_products: Callable[[str], Any] = request_json,
    fetch_image: Callable[[str, Path, int], tuple[str, int]] = fetch_source_image,
    fetch_size: Callable[[str], int | None] | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, bool, int, int]:
    """Scan one Store API page from a zero-based product offset.

    Source pages are ordered by ascending immutable product id so newly-created
    products append after already-scanned pages instead of shifting the cursor.

    Returns matched, products_completed, images_hashed, bytes_hashed,
    bounded_stop, next_product_offset and products_returned. A product interrupted
    by the byte ceiling is not committed and its own offset is returned.
    """
    query = urllib.parse.urlencode({
        "per_page": per_page,
        "page": page,
        "orderby": "id",
        "order": "asc",
    })
    products = fetch_products(origin.rstrip("/") + "/wp-json/wc/store/v1/products?" + query)
    if not isinstance(products, list):
        raise RuntimeError("Store API não retornou lista")
    products_returned = len(products)
    if product_offset < 0 or product_offset > products_returned:
        raise RuntimeError("product_offset fora da página retornada")

    matched: list[dict[str, Any]] = []
    images_hashed = bytes_hashed = products_completed = 0
    bounded_stop = False
    next_product_offset = product_offset
    staged_sizes = inventory_sizes_by_extension(inventory)

    with tempfile.TemporaryDirectory(prefix=f"staging-product-index-{page}-") as temp_dir:
        temp_root = Path(temp_dir)
        for product_index in range(product_offset, products_returned):
            product = products[product_index]
            product_id = int(product.get("id") or 0)
            raw_images = product.get("images") or []
            if product_id < 1 or not isinstance(raw_images, list) or not raw_images:
                products_completed += 1
                next_product_offset = product_index + 1
                continue

            rows: list[dict[str, Any]] = []
            for position, image in enumerate(raw_images, 1):
                raw_source_url = str(image.get("src") or "")
                source_path = safe_source_path(origin, raw_source_url)
                source_url = canonical_source_url(origin, source_path)
                extension = extension_from_path(source_path)
                known_size = fetch_size(source_url) if fetch_size is not None else None
                eligible_sizes = staged_sizes.get(extension, set())

                if known_size is not None and known_size > max_image_bytes:
                    rows.append(oversized_review_row(
                        image, position, source_path, extension, known_size=known_size,
                    ))
                    continue

                if known_size is not None and known_size not in eligible_sizes:
                    rows.append({
                        "sourceImageId": str(image.get("id") or position), "position": position,
                        "sourcePath": source_path, "extension": extension, "sha256": None,
                        "bytes": known_size, "stagingKey": None, "stagingVerified": False,
                        "hashSkippedReason": "size_not_present_in_staging",
                    })
                    continue

                if known_size is not None and bytes_hashed + known_size > remaining_bytes:
                    bounded_stop = True
                    break

                target = temp_root / str(product_id) / f"{position:03d}.{extension}"
                try:
                    sha256, size = fetch_image(source_url, target, max_image_bytes)
                except RuntimeError as exc:
                    if known_size is None and "imagem excede limite individual de" in str(exc):
                        rows.append(oversized_review_row(
                            image, position, source_path, extension, known_size=None,
                        ))
                        continue
                    raise
                if bytes_hashed + size > remaining_bytes:
                    bounded_stop = True
                    break
                bytes_hashed += size
                images_hashed += 1
                key = staging_key(staging_prefix, sha256, extension)
                staged_size = inventory.get(key)
                rows.append({
                    "sourceImageId": str(image.get("id") or position), "position": position,
                    "sourcePath": source_path, "extension": extension, "sha256": sha256,
                    "bytes": size, "stagingKey": key if staged_size == size else None,
                    "stagingVerified": staged_size == size,
                })

            if bounded_stop:
                next_product_offset = product_index
                break

            products_completed += 1
            next_product_offset = product_index + 1
            if any(row["stagingVerified"] for row in rows):
                matched.append(summarize_product(product, rows))

    return (
        matched, products_completed, images_hashed, bytes_hashed,
        bounded_stop, next_product_offset, products_returned,
    )


def build_index(args: argparse.Namespace) -> dict[str, Any]:
    descriptor = load_json(Path(args.descriptor))
    source_id = str(descriptor.get("sourceId") or "").strip()
    origin = decode_b64_text(descriptor.get("sourceOriginB64"), "sourceOriginB64").rstrip("/")
    prefix = decode_b64_text(descriptor.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    bucket = str(descriptor.get("stagingBucket") or "").strip()
    if not source_id or not bucket:
        raise RuntimeError("descriptor sem sourceId/stagingBucket")
    account_id = str(os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = str(os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if not account_id or not token:
        raise RuntimeError("bindings Cloudflare ausentes")

    start_page, end_page = max(1, int(args.start_page)), max(1, int(args.end_page))
    end_page = max(start_page, end_page)
    if end_page - start_page + 1 > int(args.max_pages):
        raise RuntimeError("intervalo excede --max-pages")
    per_page = min(max(int(args.per_page), 1), 20)
    start_product_offset = int(args.start_product_offset)
    if start_product_offset < 0 or start_product_offset >= per_page:
        raise RuntimeError("--start-product-offset deve estar entre 0 e per_page-1")
    max_total_bytes = int(args.max_total_bytes)
    if max_total_bytes < 1:
        raise RuntimeError("--max-total-bytes deve ser positivo")

    inventory = list_staging_inventory(account_id, token, bucket, prefix)
    checkpoint = Path(args.checkpoint)
    state = load_state(checkpoint, source_id, len(inventory))
    completed = {int(page) for page in state["pagesCompleted"]}

    for page in range(start_page, end_page + 1):
        if page in completed:
            continue
        remaining = max_total_bytes - int(state["sourceBytesHashed"])
        if remaining <= 0:
            state["boundedStop"] = "max_total_bytes"
            state["nextPage"] = page
            state["nextProductOffset"] = start_product_offset if page == start_page else 0
            break

        page_offset = start_product_offset if page == start_page else 0
        matched, products_completed, image_count, byte_count, stopped, next_offset, products_returned = scan_page(
            origin=origin, page=page, per_page=per_page, inventory=inventory,
            staging_prefix=prefix, max_image_bytes=int(args.max_image_bytes), remaining_bytes=remaining,
            product_offset=page_offset, fetch_size=fetch_source_size,
        )
        state["productsScanned"] += products_completed
        state["imagesHashed"] += image_count
        state["sourceBytesHashed"] += byte_count
        for product in matched:
            state["matchedProducts"][str(product["productId"])] = product

        if stopped:
            state["boundedStop"] = "max_total_bytes"
            state["nextPage"] = page
            state["nextProductOffset"] = next_offset
            save_state(checkpoint, state)
            break

        state["pagesCompleted"].append(page)
        state["pagesCompleted"] = sorted({int(value) for value in state["pagesCompleted"]})
        state["nextPage"] = page + 1
        state["nextProductOffset"] = 0
        state.pop("boundedStop", None)
        save_state(checkpoint, state)

        if products_returned == 0:
            state["sourceExhaustedAtPage"] = page
            state["nextPage"] = None
            state["nextProductOffset"] = 0
            save_state(checkpoint, state)
            break

    products = list(state["matchedProducts"].values())
    products.sort(key=lambda row: (not row["completeStaging"], -row["stagedImageCount"], row["productId"]))
    report = {
        **state,
        "matchedProductCount": len(products),
        "completeStagingProducts": sum(1 for row in products if row["completeStaging"]),
        "products": products,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "sourceOrdering": state["sourceOrdering"],
        "pagesCompleted": state["pagesCompleted"],
        "productsScanned": state["productsScanned"],
        "matchedProducts": len(products),
        "completeStagingProducts": report["completeStagingProducts"],
        "sourceBytesHashed": state["sourceBytesHashed"],
        "nextPage": state.get("nextPage"),
        "nextProductOffset": state.get("nextProductOffset", 0),
        "productionWrites": 0,
        "stagingWrites": 0,
    }, ensure_ascii=False))
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Indexa produtos da fonte por SHA staged, sem writes.")
    parser.add_argument("--descriptor", default="config/external-batches/external-source-001-batch-004.json")
    parser.add_argument("--start-page", type=int, required=True)
    parser.add_argument("--start-product-offset", type=int, default=0)
    parser.add_argument("--end-page", type=int, required=True)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--per-page", type=int, default=20)
    parser.add_argument("--max-image-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--max-total-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    build_index(parse_args())
