#!/usr/bin/env python3
"""Normalize an authorized WooCommerce product export for external ingestion.

This tool is deliberately network-free. Vendor-specific origin/host/taxonomy
configuration is supplied from an untracked file under `.external-ingest/` (or
another operator-controlled path). The repository therefore owns the ingestion
contract, not a third-party brand or endpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from catalog_scope import PUBLIC_TOP_LEVEL_CATEGORIES

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".jfif"
}
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
DASH_SPLIT_RE = re.compile(r"\s+[–—-]\s+")
GENERIC_TITLE_TOKENS_RE = re.compile(r"(?i)(?:\s*[-–—]\s*)?STL(?:\s*Files?)?\s*$")
FEATURE_SEGMENT_RE = re.compile(
    r"(?i)\s*\+\s*(?:bust|busto|nsfw|extras?|extra\s+content|pre[- ]?supported|uncut(?:/merged)?|split\s+pieces?)\b"
)
STL_TOKEN_RE = re.compile(r"(?i)\bSTL\b")
SAFE_SEGMENT_RE = re.compile(r"[^a-z0-9._-]+")


class ImportContractError(RuntimeError):
    pass


def normalized_text(value: Any) -> str:
    text = html.unescape(TAG_RE.sub(" ", str(value or "")))
    return SPACE_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def normalized_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def safe_segment(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    safe = SAFE_SEGMENT_RE.sub("-", ascii_value).strip("._-")
    if not safe:
        raise ImportContractError(f"segmento inseguro/vazio após normalização: {value!r}")
    return safe


def stable_product_id(value: Any) -> str:
    """Return the immutable WooCommerce product id in canonical decimal form."""
    if isinstance(value, bool):
        raise ImportContractError("produto sem id estável positivo")
    if isinstance(value, int):
        number = value
    else:
        text = normalized_text(value)
        if not text.isdigit():
            raise ImportContractError("produto sem id estável positivo")
        number = int(text)
    if number <= 0:
        raise ImportContractError("produto sem id estável positivo")
    return str(number)


def stable_image_identity(image: dict[str, Any], source_url: str) -> tuple[str, str]:
    """Return an order-independent image identity and its provenance kind."""
    provider_id = normalized_text(image.get("id"))
    if provider_id:
        return provider_id, "provider_id"
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return f"url-sha256-{digest}", "url_sha256"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImportContractError(f"arquivo não encontrado: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ImportContractError(f"JSON inválido em {path}: {exc}") from exc


def load_source_config(path: Path, source_id: str) -> dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ImportContractError(f"configuração externa inválida: {path}")
    sources = payload.get("sources")
    if not isinstance(sources, dict) or source_id not in sources:
        raise ImportContractError(f"fonte externa não configurada: {source_id}")
    source = sources[source_id]
    if not isinstance(source, dict):
        raise ImportContractError(f"configuração inválida para fonte: {source_id}")

    origin = str(source.get("origin") or "").rstrip("/")
    parsed = urlparse(origin)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ImportContractError(f"origin HTTPS inválida para {source_id}: {origin!r}")

    hosts = source.get("allowedImageHosts")
    if not isinstance(hosts, list) or not hosts or not all(
        isinstance(item, str) and item.strip() for item in hosts
    ):
        raise ImportContractError(f"allowedImageHosts inválido para {source_id}")

    signals = source.get("taxonomySignals")
    if not isinstance(signals, dict) or not signals:
        raise ImportContractError(f"taxonomySignals inválido para {source_id}")

    allowed_categories = set(PUBLIC_TOP_LEVEL_CATEGORIES)
    invalid_targets = sorted(
        {str(target) for target in signals.values() if str(target) not in allowed_categories},
        key=str.casefold,
    )
    if invalid_targets:
        raise ImportContractError(
            f"taxonomySignals aponta para categorias fora do SSOT público: {invalid_targets}"
        )
    return source


def product_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        products = payload
    elif isinstance(payload, dict) and isinstance(payload.get("products"), list):
        products = payload["products"]
    else:
        raise ImportContractError("export deve ser um array WooCommerce ou {'products': [...]}")
    if not all(isinstance(item, dict) for item in products):
        raise ImportContractError("export contém produto não-objeto")
    return products


def entity_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            name = normalized_text(item.get("name") or item.get("slug"))
        else:
            name = normalized_text(item)
        if name:
            result.append(name)
    return result


def source_features(
    title: str,
    categories: list[str],
    tags: list[str],
    feature_terms: dict[str, str],
) -> list[str]:
    haystack = [title, *categories, *tags]
    found: set[str] = set()
    for raw_term, canonical in feature_terms.items():
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(raw_term.casefold())}(?![a-z0-9])")
        if any(pattern.search(value.casefold()) for value in haystack):
            found.add(str(canonical))
    return sorted(found, key=str.casefold)


def taxonomy_resolution(
    categories: list[str],
    signals: dict[str, str],
) -> tuple[str | None, list[dict[str, str]], str]:
    signal_map = {normalized_key(key): str(value) for key, value in signals.items()}
    evidence: list[dict[str, str]] = []
    for category in categories:
        target = signal_map.get(normalized_key(category))
        if target:
            evidence.append({"source": category, "target": target})
    targets = sorted({item["target"] for item in evidence}, key=str.casefold)
    if len(targets) == 1:
        return targets[0], evidence, "resolved"
    if not targets:
        return None, evidence, "review_missing"
    return None, evidence, "review_conflict"


def normalize_franchise_name(value: str) -> str:
    cleaned = re.sub(r"(?i)\s+STL$", "", value).strip()
    return cleaned or value


def franchise_resolution(
    title: str,
    categories: list[str],
    non_franchise_terms: list[str],
) -> tuple[str | None, list[str], str]:
    blocked = {normalized_key(value) for value in non_franchise_terms}
    candidates = [
        normalize_franchise_name(value)
        for value in categories
        if normalized_key(value) not in blocked
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = normalized_key(candidate)
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)

    title_parts = DASH_SPLIT_RE.split(title)
    if len(title_parts) >= 2:
        suffix = normalize_franchise_name(title_parts[-1].strip())
        matches = [item for item in deduped if normalized_key(item) == normalized_key(suffix)]
        if len(matches) == 1:
            return matches[0], deduped, "resolved"

    if len(deduped) == 1:
        return deduped[0], deduped, "resolved"
    if not deduped:
        return None, deduped, "review_missing"
    return None, deduped, "review_ambiguous"


def display_name(title: str, franchise: str | None) -> str:
    name = GENERIC_TITLE_TOKENS_RE.sub("", title).strip()
    parts = DASH_SPLIT_RE.split(name)
    if franchise and len(parts) >= 2 and normalized_key(parts[-1]) == normalized_key(franchise):
        name = " – ".join(parts[:-1]).strip()
    name = FEATURE_SEGMENT_RE.sub("", name)
    name = STL_TOKEN_RE.sub("", name)
    name = SPACE_RE.sub(" ", name)
    name = re.sub(r"\s+([,.;:])", r"\1", name)
    name = re.sub(r"\s*\+\s*$", "", name)
    return name.strip(" -–—") or title


def validate_product_url(url: str, origin: str, product_prefixes: list[str]) -> str:
    parsed = urlparse(url)
    expected = urlparse(origin)
    if parsed.scheme != "https" or normalized_key(parsed.netloc) != normalized_key(expected.netloc):
        raise ImportContractError(f"URL de produto fora da origem configurada: {url}")
    prefixes = product_prefixes or ["/"]
    if not any(parsed.path.startswith(prefix) for prefix in prefixes):
        raise ImportContractError(f"URL de produto fora dos prefixes permitidos: {url}")
    return url


def image_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in IMAGE_EXTS:
        raise ImportContractError(f"extensão de imagem não suportada: {url}")
    return suffix


def normalize_product(
    product: dict[str, Any],
    *,
    source_id: str,
    source: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_product_id = stable_product_id(product.get("id"))
    title = normalized_text(product.get("name"))
    slug = normalized_text(product.get("slug"))
    permalink = normalized_text(product.get("permalink"))
    if not title or not slug or not permalink:
        raise ImportContractError("produto sem name/slug/permalink")

    origin = str(source["origin"]).rstrip("/")
    validate_product_url(permalink, origin, list(source.get("productPathPrefixes") or []))
    categories = entity_names(product.get("categories"))
    tags = entity_names(product.get("tags"))
    category, category_evidence, category_state = taxonomy_resolution(
        categories, dict(source.get("taxonomySignals") or {})
    )
    franchise, franchise_candidates, franchise_state = franchise_resolution(
        title, categories, list(source.get("nonFranchiseTerms") or [])
    )
    feature_terms = {
        normalized_key(key): str(value)
        for key, value in dict(source.get("featureTerms") or {}).items()
    }
    features = source_features(title, categories, tags, feature_terms)

    source_product_key = f"{source_id}:product:{source_product_id}"
    incoming_model = f"{source_id} / product:{source_product_id}"
    allowed_hosts = {normalized_key(value) for value in source.get("allowedImageHosts") or []}
    raw_images = product.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        raise ImportContractError(f"produto sem imagens: {source_product_key}")

    image_rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_image_identities: set[str] = set()
    for position, image in enumerate(raw_images, 1):
        if not isinstance(image, dict):
            raise ImportContractError(f"imagem inválida em {source_product_key}, posição {position}")
        src = normalized_text(image.get("src"))
        if not src:
            raise ImportContractError(f"imagem sem src em {source_product_key}, posição {position}")
        parsed = urlparse(src)
        host = normalized_key(parsed.netloc)
        if parsed.scheme != "https" or host not in allowed_hosts:
            raise ImportContractError(
                f"host de imagem não autorizado em {source_product_key}: {src}"
            )
        if src in seen_urls:
            raise ImportContractError(f"imagem duplicada no produto {source_product_key}: {src}")
        seen_urls.add(src)

        image_id, identity_kind = stable_image_identity(image, src)
        if image_id in seen_image_identities:
            raise ImportContractError(
                f"identidade de imagem duplicada no produto {source_product_key}: {image_id}"
            )
        seen_image_identities.add(image_id)
        extension = image_extension(src)
        stable_id = safe_segment(image_id)
        target_path = (
            Path(source_id)
            / f"product-{safe_segment(source_product_id)}"
            / f"{stable_id}{extension}"
        ).as_posix()
        image_rows.append(
            {
                "version": 1,
                "sourceId": source_id,
                "sourceProductKey": source_product_key,
                "incomingModel": incoming_model,
                "position": position,
                "sourceImageId": image_id,
                "sourceImageIdentityKind": identity_kind,
                "sourceUrl": src,
                "sourceAlt": normalized_text(image.get("alt")),
                "targetPath": target_path,
            }
        )

    review_reasons: list[str] = []
    if category_state != "resolved":
        review_reasons.append(category_state)
    if franchise_state != "resolved":
        review_reasons.append(franchise_state)

    row = {
        "version": 1,
        "sourceId": source_id,
        "sourceProductKey": source_product_key,
        "incomingModel": incoming_model,
        "sourceProductId": source_product_id,
        "sourceSlug": slug,
        "sourceUrl": permalink,
        "sourceTitle": title,
        "displayName": display_name(title, franchise),
        "canonicalCategoryCandidate": category,
        "categoryResolution": category_state,
        "categorySignals": category_evidence,
        "franchiseCandidate": franchise,
        "franchiseResolution": franchise_state,
        "franchiseCandidates": franchise_candidates,
        "sourceCategories": categories,
        "sourceTags": tags,
        "features": features,
        "imageCount": len(image_rows),
        "readyForMaterialization": not review_reasons,
        "reviewReasons": review_reasons,
    }
    return row, image_rows


def build_inventory(
    payload: Any,
    *,
    source_id: str,
    source: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    products = product_list(payload)
    product_rows: list[dict[str, Any]] = []
    image_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_urls: set[str] = set()

    for product in products:
        row, images = normalize_product(product, source_id=source_id, source=source)
        key = row["sourceProductKey"]
        if key in seen_keys:
            raise ImportContractError(f"produto duplicado no export: {key}")
        seen_keys.add(key)
        url = row["sourceUrl"]
        if url in seen_urls:
            raise ImportContractError(f"URL de produto duplicada no export: {url}")
        seen_urls.add(url)
        product_rows.append(row)
        image_rows.extend(images)

    product_rows.sort(key=lambda row: str(row["sourceProductKey"]).casefold())
    image_rows.sort(
        key=lambda row: (
            str(row["sourceProductKey"]).casefold(),
            int(row["position"]),
            str(row["sourceUrl"]),
        )
    )
    nsfw_products = sum(
        1 for row in product_rows if "NSFW" in set(row.get("features") or [])
    )
    ready_products = sum(1 for row in product_rows if row["readyForMaterialization"])
    summary = {
        "version": 1,
        "sourceId": source_id,
        "products": len(product_rows),
        "images": len(image_rows),
        "nsfwProducts": nsfw_products,
        "readyForMaterialization": ready_products,
        "requiresReview": len(product_rows) - ready_products,
        "categoryResolution": {
            state: sum(1 for row in product_rows if row["categoryResolution"] == state)
            for state in sorted({row["categoryResolution"] for row in product_rows})
        },
        "franchiseResolution": {
            state: sum(1 for row in product_rows if row["franchiseResolution"] == state)
            for state in sorted({row["franchiseResolution"] for row in product_rows})
        },
        "networkDownloads": 0,
    }
    return product_rows, image_rows, summary


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normaliza um export WooCommerce autorizado em inventário externo "
            "sem baixar mídia nem alterar o catálogo."
        )
    )
    parser.add_argument("export", type=Path, help="JSON exportado/autorizado da Store API")
    parser.add_argument("--source-id", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="configuração da fonte mantida fora do repositório (ex.: .external-ingest/source.json)",
    )
    parser.add_argument("--output", type=Path, default=Path(".external-ingest"))
    args = parser.parse_args()

    try:
        source = load_source_config(args.config, args.source_id)
        products, images, summary = build_inventory(
            load_json(args.export), source_id=args.source_id, source=source
        )
        output = args.output / args.source_id
        write_jsonl(output / "products.jsonl", products)
        write_jsonl(output / "image-download-plan.jsonl", images)
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (ImportContractError, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
