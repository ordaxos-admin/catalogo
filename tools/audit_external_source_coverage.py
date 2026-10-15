#!/usr/bin/env python3
"""Audit external-source coverage against the published catalog without media downloads.

This report is intentionally conservative. A product is counted as confirmed present only
when versioned review evidence explicitly maps it to an existing model, or when a reviewed
new model's deterministic model id is already published. Name similarity is review-only.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

USER_AGENT = "catalog-external-coverage/1.0"
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
STL_SPLIT_RE = re.compile(r"\bSTL\b", re.IGNORECASE)


class CoverageError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(value: Any) -> str:
    text = html.unescape(TAG_RE.sub(" ", str(value or "")))
    text = unicodedata.normalize("NFKC", text).casefold()
    return SPACE_RE.sub(" ", text).strip()


def subject_hint(name: str) -> str:
    text = html.unescape(str(name or "")).strip()
    prefix = STL_SPLIT_RE.split(text, maxsplit=1)[0].strip(" -–—")
    return prefix or text


def decode_origin(descriptor: dict[str, Any]) -> str:
    raw = str(descriptor.get("sourceOriginB64") or "").strip()
    if not raw:
        raise CoverageError("sourceOriginB64 ausente")
    try:
        origin = base64.b64decode(raw, validate=True).decode("utf-8").rstrip("/")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CoverageError("sourceOriginB64 inválido") from exc
    parsed = urllib.parse.urlparse(origin)
    if parsed.scheme != "https" or not parsed.netloc:
        raise CoverageError("source origin deve ser HTTPS")
    return origin


def fetch_json(url: str, *, token: str | None = None, body: dict[str, Any] | None = None) -> tuple[Any, Any]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    data = None
    method = "GET"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        method = "POST"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return payload, response.headers


def fetch_source_products(origin: str, *, per_page: int = 100, max_pages: int = 100) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if per_page < 1 or per_page > 100:
        raise CoverageError("per_page deve ficar entre 1 e 100")
    params = {"per_page": per_page, "page": 1, "_fields": "id,name,slug"}
    first_url = f"{origin}/wp-json/wc/store/v1/products?{urllib.parse.urlencode(params)}"
    first, headers = fetch_json(first_url)
    if not isinstance(first, list):
        raise CoverageError("Store API não retornou array de produtos")
    try:
        total = int(headers.get("X-WP-Total") or headers.get("x-wp-total") or len(first))
        total_pages = int(headers.get("X-WP-TotalPages") or headers.get("x-wp-totalpages") or 1)
    except ValueError as exc:
        raise CoverageError("headers de paginação inválidos") from exc
    if total_pages < 1 or total_pages > max_pages:
        raise CoverageError(f"total_pages fora do limite: {total_pages}")
    products = list(first)
    for page in range(2, total_pages + 1):
        params["page"] = page
        payload, _ = fetch_json(f"{origin}/wp-json/wc/store/v1/products?{urllib.parse.urlencode(params)}")
        if not isinstance(payload, list):
            raise CoverageError(f"Store API página {page} inválida")
        products.extend(payload)
    ids = [int(item.get("id") or 0) for item in products]
    if any(value <= 0 for value in ids) or len(ids) != len(set(ids)):
        raise CoverageError("IDs de produto inválidos/duplicados")
    return products, {"reportedTotal": total, "totalPages": total_pages, "productsFetched": len(products)}


def fetch_published_models(account_id: str, token: str, database_id: str) -> list[dict[str, Any]]:
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
    payload, _ = fetch_json(
        endpoint,
        token=token,
        body={"sql": "SELECT id,name,slug,code,image_count FROM models WHERE published=1 ORDER BY id"},
    )
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise CoverageError(f"consulta D1 falhou: {payload.get('errors') if isinstance(payload, dict) else payload}")
    rows: list[dict[str, Any]] = []
    for result in payload.get("result") or []:
        rows.extend(result.get("results") or [])
    return rows


def batch_model_ids(root: Path) -> dict[int, str]:
    result: dict[int, str] = {}
    for path in sorted(root.glob("external-source-001-batch-*.json")):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for product in payload.get("products") or []:
            try:
                product_id = int(product.get("productId"))
            except (TypeError, ValueError):
                continue
            model_id = str(product.get("expectedModelId") or "").strip()
            if model_id:
                result[product_id] = model_id
    return result


def review_states(root: Path) -> dict[int, set[str]]:
    states: dict[int, set[str]] = defaultdict(set)
    for path in sorted(root.glob("external-source-001-*.json")):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        try:
            product_id = int(payload.get("sourceProductId"))
        except (TypeError, ValueError):
            continue
        identity = payload.get("identityReview") or {}
        decision = str(identity.get("decision") or "").strip()
        if decision in {"map_existing", "new_model", "unresolved"}:
            states[product_id].add(decision)
        top_decision = payload.get("decision") or {}
        if top_decision.get("newModelAuthorized") is True:
            states[product_id].add("new_model")
        if top_decision.get("identityResolved") is False:
            states[product_id].add("unresolved")
        if top_decision.get("galleryMediaReviewApproved") is False:
            states[product_id].add("media_blocked_or_pending")
        if str(payload.get("watermarkStatus") or "") == "watermarked":
            states[product_id].add("media_blocked_or_pending")
    return states


def classify(
    products: list[dict[str, Any]],
    models: list[dict[str, Any]],
    reviews: dict[int, set[str]],
    batch_ids: dict[int, str],
) -> dict[str, Any]:
    published_ids = {str(row.get("id") or "") for row in models}
    names: dict[str, list[str]] = defaultdict(list)
    for row in models:
        names[normalize(row.get("name"))].append(str(row.get("id") or ""))

    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for product in products:
        product_id = int(product["id"])
        name = str(product.get("name") or "")
        states = reviews.get(product_id, set())
        expected_id = batch_ids.get(product_id)
        if "map_existing" in states:
            state = "confirmed_present_map_existing"
        elif "new_model" in states and expected_id and expected_id in published_ids:
            state = "confirmed_present_reviewed_new_model"
        elif "new_model" in states:
            state = "reviewed_new_model_not_published"
        elif states:
            state = "reviewed_unresolved"
        else:
            candidates = names.get(normalize(subject_hint(name)), [])
            if not candidates:
                state = "unreviewed_no_exact_name_candidate"
            elif len(candidates) == 1:
                state = "unreviewed_single_exact_name_candidate"
            else:
                state = "unreviewed_multiple_exact_name_candidates"
        counts[state] += 1
        rows.append(
            {
                "sourceProductId": product_id,
                "sourceName": name,
                "sourceSlug": str(product.get("slug") or ""),
                "state": state,
                "expectedModelId": expected_id,
                "reviewStates": sorted(states),
            }
        )

    confirmed = counts["confirmed_present_map_existing"] + counts["confirmed_present_reviewed_new_model"]
    confirmed_missing = counts["reviewed_new_model_not_published"]
    return {
        "summary": {
            "sourceProducts": len(products),
            "publishedModels": len(models),
            "confirmedPresent": confirmed,
            "confirmedReviewedNewModelsNotPublished": confirmed_missing,
            "coverage100Proven": bool(products) and confirmed == len(products),
            "states": dict(sorted(counts.items())),
            "productionWrites": 0,
            "stagingWrites": 0,
        },
        "products": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="config/external-sources/external-source-001.json")
    parser.add_argument("--evidence-root", default="docs/evidence")
    parser.add_argument("--batch-root", default="config/external-batches")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    descriptor = load_json(Path(args.source))
    origin = decode_origin(descriptor)
    account_id = str(os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = str(os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    database_id = str(os.environ.get("CLOUDFLARE_D1_DATABASE_ID") or "").strip()
    if not account_id or not token or not database_id:
        raise CoverageError("bindings Cloudflare ausentes")
    products, source_meta = fetch_source_products(origin)
    models = fetch_published_models(account_id, token, database_id)
    report = classify(
        products,
        models,
        review_states(Path(args.evidence_root)),
        batch_model_ids(Path(args.batch_root)),
    )
    report["version"] = 1
    report["sourceId"] = str(descriptor.get("sourceId") or "")
    report["source"] = source_meta
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
