#!/usr/bin/env python3
"""Build an R2-ready media bundle from the reviewed ingestion manifest.

The source catalog is read-only. Only records marked OK + canonical are
published. Variants are generated without upscaling and grouped by the stable
hierarchical model key produced by ingest_catalog.py.

Every media object and gallery manifest gets a deterministic/content-addressed
key so already published data can be cached for a long time and skipped by the
incremental R2 publisher.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from PIL import Image, ImageOps

from model_identity import (
    DEFAULT_IDENTITY_ALIAS_CONFIG,
    canonical_identity_key,
    load_identity_aliases,
    validate_identity_comparison_collisions,
    validate_taxonomy_slug_mappings,
    stem_view_descriptor,
)

VARIANTS = {
    "thumb": (360, 80),
    "card": (800, 84),
    "detail": (1600, 88),
}


def stable_id(prefix: str, value: str, length: int = 20) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "modelo"


DEFAULT_TAXONOMY_CONFIG = Path(__file__).resolve().parents[1] / "config" / "catalog-taxonomy.json"
MEDIA_BUILD_STATE_VERSION = 1
MEDIA_RENDERER_VERSION = 1
MEDIA_METADATA_VERSION = 7
MEDIA_CHECKPOINT_INTERVAL = 10
MEDIA_STATE_REPLACE_ATTEMPTS = 10
MEDIA_STATE_REPLACE_DELAY_SECONDS = 0.1


def humanize_stem(value: str) -> str:
    text = re.sub(r"[-_]+", " ", value).strip()
    return text[:1].upper() + text[1:] if text else "Modelo"


def model_variant_name(
    identity_label: str,
    display_name: str,
    franchise_name: str,
    grouped_gallery: bool,
) -> str:
    raw = identity_label.strip()
    if grouped_gallery:
        match = stem_view_descriptor(raw)
        if match:
            raw = match[0]

    label = humanize_stem(raw)
    prefixes = [prefix.strip() for prefix in (franchise_name, display_name) if prefix.strip()]
    # Remove nested public prefixes repeatedly. Example:
    # "Star Wars Darth Vader 04" -> "Darth Vader 04" -> "04".
    # This keeps the visible variant concise without changing product identity.
    for _ in range(len(prefixes) + 1):
        previous = label
        for normalized in prefixes:
            pattern = re.compile(rf"^{re.escape(normalized)}(?:\s*[-_:]\s*|\s+)", re.IGNORECASE)
            label = pattern.sub("", label).strip()
        if label == previous:
            break

    if label:
        label = label[:1].upper() + label[1:]

    if not label or label.casefold() in {display_name.casefold(), franchise_name.casefold()}:
        return ""
    if re.fullmatch(r"\d{1,6}", label):
        return f"Modelo {label}"
    return label[:160]


def model_display_name(hierarchy: list[str], public_collection_parts: list[str], source_stem: str, audited_public: bool) -> str:
    """Return the customer-facing model name without discarding source search context.

    Audited public models keep their file-level identity for IDs/search, while the
    visible title comes from the semantic public taxonomy (for example
    ``Homem-Aranha / Abutre`` -> ``Abutre``).
    """
    if audited_public:
        source_slug = slugify(source_stem)
        for part in reversed(public_collection_parts):
            normalized = part.strip()
            if not normalized:
                continue
            semantic_slug = slugify(normalized)
            if source_slug == semantic_slug or source_slug.startswith(f"{semantic_slug}-"):
                return normalized
            break
        return humanize_stem(source_stem)
    return hierarchy[-1] if hierarchy else humanize_stem(source_stem)


def public_identity_label(identity_key: str, hierarchy_key: str, source_stem: str) -> str:
    prefix = f"{hierarchy_key} / "
    if identity_key.startswith(prefix):
        value = identity_key[len(prefix):].strip()
        if value:
            return value
    # Renomes/mudanças de pasta aprovados continuam usando o último segmento
    # da identidade canônica histórica para preservar o slug público.
    if " / " in identity_key:
        historical_leaf = identity_key.rsplit(" / ", 1)[-1].strip()
        if historical_leaf:
            return historical_leaf
    return source_stem


def franchise_index(hierarchy: list[str], taxonomy: dict) -> int:
    if len(hierarchy) < 2:
        return 0
    category = hierarchy[0]
    second = hierarchy[1]
    category_slug = slugify(category)
    rules = taxonomy.get("sourceHierarchy", {}).get("categoryIntermediates", {})
    rule = rules.get(category_slug, {}) if isinstance(rules, dict) else {}
    if len(hierarchy) > 2 and isinstance(rule, dict):
        names = rule.get("names", [])
        if isinstance(names, list) and second in names:
            return 2
        pattern = rule.get("pattern")
        if isinstance(pattern, str) and pattern and re.match(pattern, second):
            return 2
    return 1


def validate_taxonomy_config(data: dict, config_path: Path) -> None:
    if data.get("version") != 1 or not isinstance(data.get("franchises"), dict):
        raise RuntimeError(f"configuração de taxonomia inválida: {config_path}")
    source_hierarchy = data.get("sourceHierarchy", {})
    if not isinstance(source_hierarchy, dict):
        raise RuntimeError(f"sourceHierarchy inválido em {config_path}")
    category_intermediates = source_hierarchy.get("categoryIntermediates", {})
    if not isinstance(category_intermediates, dict):
        raise RuntimeError(f"categoryIntermediates inválido em {config_path}")
    for category_slug, rule in category_intermediates.items():
        if not isinstance(category_slug, str) or not category_slug.strip() or not isinstance(rule, dict):
            raise RuntimeError(f"regra de hierarquia fonte inválida em {config_path}: {category_slug!r}")
        names = rule.get("names", [])
        pattern = rule.get("pattern")
        if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
            raise RuntimeError(f"names inválido em {config_path}: {category_slug}")
        if pattern is not None:
            if not isinstance(pattern, str) or not pattern:
                raise RuntimeError(f"pattern inválido em {config_path}: {category_slug}")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise RuntimeError(f"pattern regex inválido em {config_path}: {category_slug}: {exc}") from exc
    for franchise_key, rule in data["franchises"].items():
        if not isinstance(franchise_key, str) or franchise_key.count("/") != 1 or not all(part.strip() for part in franchise_key.split("/")):
            raise RuntimeError(f"chave de franquia inválida em {config_path}: {franchise_key!r}")
        if not isinstance(rule, dict):
            raise RuntimeError(f"regra de franquia inválida em {config_path}: {franchise_key}")
        overrides = rule.get("pathOverrides", {})
        if not isinstance(overrides, dict):
            raise RuntimeError(f"pathOverrides inválido em {config_path}: {franchise_key}")
        groups = rule.get("pathGroups", {})
        if not isinstance(groups, dict):
            raise RuntimeError(f"pathGroups inválido em {config_path}: {franchise_key}")
        grouped_sources: set[str] = set()
        for group_name, members in groups.items():
            if not isinstance(group_name, str) or not group_name.strip() or group_name.strip() in {".", ".."} or "/" in group_name or "\\" in group_name:
                raise RuntimeError(f"grupo público inválido em {config_path}: {franchise_key} -> {group_name!r}")
            if not isinstance(members, list) or not members:
                raise RuntimeError(f"membros de grupo inválidos em {config_path}: {franchise_key} -> {group_name}")
            for source_key in members:
                if not isinstance(source_key, str) or not source_key.strip() or any(not part.strip() for part in source_key.split(" / ")):
                    raise RuntimeError(f"caminho fonte de grupo inválido em {config_path}: {franchise_key} -> {group_name}")
                if source_key in grouped_sources:
                    raise RuntimeError(f"caminho fonte em múltiplos grupos em {config_path}: {franchise_key} -> {source_key}")
                grouped_sources.add(source_key)
        for source_key, replacement in overrides.items():
            if not isinstance(source_key, str) or not source_key.strip() or any(not part.strip() for part in source_key.split(" / ")):
                raise RuntimeError(f"caminho fonte inválido em {config_path}: {franchise_key} -> {source_key!r}")
            if not isinstance(replacement, list) or not replacement:
                raise RuntimeError(f"caminho público inválido em {config_path}: {franchise_key} -> {source_key}")
            for part in replacement:
                if not isinstance(part, str) or not part.strip() or part.strip() in {".", ".."} or "/" in part or "\\" in part:
                    raise RuntimeError(f"segmento público inválido em {config_path}: {franchise_key} -> {source_key}")


def load_taxonomy_config(path: Path | None = None) -> dict:
    config_path = path or DEFAULT_TAXONOMY_CONFIG
    if not config_path.is_file():
        return {"version": 1, "franchises": {}}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON de taxonomia inválido: {config_path}: {exc}") from exc
    validate_taxonomy_config(data, config_path)
    return data


def public_folder_path(category_slug: str, franchise_slug: str, source_parts: list[str], taxonomy: dict) -> list[str]:
    rule = taxonomy.get("franchises", {}).get(f"{category_slug}/{franchise_slug}", {})
    overrides = rule.get("pathOverrides", {}) if isinstance(rule, dict) else {}
    groups = rule.get("pathGroups", {}) if isinstance(rule, dict) else {}
    source_key = " / ".join(source_parts)
    replacement = overrides.get(source_key)
    if replacement is not None:
        if not isinstance(replacement, list) or not replacement or not all(isinstance(item, str) and item.strip() for item in replacement):
            raise RuntimeError(f"pathOverride inválido para {category_slug}/{franchise_slug}: {source_key}")
        return [item.strip() for item in replacement]
    for group_name, members in groups.items():
        if source_key in members:
            return [group_name.strip(), *source_parts]
    return source_parts


def read_manifest(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def render_variants(source: Path, r2_root: Path, base_key: str) -> tuple[dict[str, str], dict[str, dict]]:
    variants: dict[str, str] = {}
    variant_meta: dict[str, dict] = {}
    with Image.open(source) as opened:
        base_image = ImageOps.exif_transpose(opened).convert("RGB")
        for variant, (max_side, quality) in VARIANTS.items():
            key = f"{base_key}/{variant}.webp"
            destination = r2_root / key
            destination.parent.mkdir(parents=True, exist_ok=True)
            image = base_image.copy()
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            width, height = image.size
            # method=4: benchmark real no catálogo auditado mostrou ~2x menos CPU
            # com a mesma resolução/quality, ~3-4% mais bytes e PSNR equivalente.
            image.save(destination, "WEBP", quality=quality, method=4, optimize=True)
            variants[variant] = key
            variant_meta[variant] = {
                "width": width,
                "height": height,
                "bytes": destination.stat().st_size,
                "mime": "image/webp",
            }
    return variants, variant_meta


def copy_original(source: Path, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination.stat().st_size


def gallery_identity(payload: dict) -> tuple[str, int]:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    # 48 bits remain safely below JavaScript's maximum exact integer and are
    # enough for cache/version invalidation at this catalog scale.
    version = int(digest[:12], 16)
    return digest, version


def model_metadata(
    identity_key: str,
    hierarchy_key: str,
    source_path: str,
    slug_collisions: Counter[str],
    taxonomy: dict,
    audited_public: bool = False,
    grouped_gallery: bool = False,
    stable_identity_key: str | None = None,
) -> dict:
    hierarchy = hierarchy_key.split(" / ")
    canonical_key = stable_identity_key or identity_key
    model_id = stable_id("mdl", canonical_key)
    category_name = hierarchy[0] if hierarchy else "Outros"
    franchise_pos = franchise_index(hierarchy, taxonomy)
    franchise_name = hierarchy[franchise_pos] if hierarchy else category_name
    source_stem = Path(source_path).stem
    hierarchy_key_normalized = " / ".join(hierarchy)
    source_collection_parts = hierarchy[franchise_pos + 1:-1] if not audited_public else hierarchy[franchise_pos + 1:]
    category_slug = slugify(category_name)
    franchise_slug = slugify(franchise_name)
    collection_parts = public_folder_path(category_slug, franchise_slug, source_collection_parts, taxonomy) if audited_public else source_collection_parts
    display_name = hierarchy[-1] if grouped_gallery and hierarchy else model_display_name(hierarchy, collection_parts, source_stem, audited_public)
    collection = " / ".join(collection_parts)

    canonical_identity_label = public_identity_label(canonical_key, hierarchy_key_normalized, source_stem) if audited_public else source_stem
    source_identity_label = public_identity_label(identity_key, hierarchy_key_normalized, source_stem) if audited_public else source_stem
    canonical_label = humanize_stem(canonical_identity_label)
    source_label = humanize_stem(source_identity_label)
    variant_name = model_variant_name(source_identity_label, display_name, franchise_name, grouped_gallery)
    slug_name = canonical_label if audited_public else display_name
    clean_parts = [franchise_name, slug_name] if audited_public else [franchise_name, *collection_parts, display_name]
    base_slug = slugify(" ".join(clean_parts) or slug_name)
    model_slug = base_slug
    if slug_collisions[base_slug] > 1:
        model_slug = f"{base_slug}-{model_id.split('_', 1)[1][:10]}"

    code_hash = hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:12].upper()
    code = f"TS-{code_hash}"
    search_text = " ".join(dict.fromkeys([*hierarchy, *collection_parts, display_name, variant_name, canonical_label, source_label, code])).casefold()

    return {
        "identityKey": canonical_key,
        "sourceIdentityKey": identity_key,
        "id": model_id,
        "slug": model_slug,
        "code": code,
        "sourceHierarchy": hierarchy,
        "categoryName": category_name,
        "categorySlug": category_slug,
        "franchiseName": franchise_name,
        "franchiseSlug": franchise_slug,
        "displayName": display_name,
        "variantName": variant_name,
        "collection": collection,
        "folderPath": collection_parts,
        "folderPathKey": "/".join(slugify(part) for part in collection_parts),
        "searchText": search_text,
    }


def validate_model_group(identity_key: str, rows: list[dict]) -> None:
    if not rows:
        raise RuntimeError(f"grupo de modelo vazio: {identity_key}")
    model_keys = {str(row.get("model_key") or "").strip() for row in rows}
    if "" in model_keys or len(model_keys) != 1:
        raise RuntimeError(
            f"identidade pública usada em hierarquias diferentes: {identity_key}: {sorted(model_keys)}"
        )
    explicit_groups = {str(row.get("audit_model_group") or "").strip() for row in rows if row.get("audit_model_group")}
    if len(explicit_groups) > 1:
        raise RuntimeError(
            f"identidade pública mistura produtos auditados diferentes: {identity_key}: {sorted(explicit_groups)}"
        )


def build_model_bundle(
    identity_key: str,
    rows: list[dict],
    source_root: Path,
    r2_root: Path,
    slug_collisions: Counter[str],
    taxonomy: dict,
    include_original: bool,
    stable_identity_key: str | None = None,
) -> tuple[dict, int, int]:
    validate_model_group(identity_key, rows)
    rows = sorted(
        rows,
        key=lambda row: (
            float(row.get("quality_score") or 0),
            int(row.get("width") or 0),
            int(row.get("height") or 0),
        ),
        reverse=True,
    )
    grouped_gallery = any(bool(row.get("audit_model_group")) for row in rows)
    metadata = model_metadata(
        identity_key,
        str(rows[0]["model_key"]),
        str(rows[0]["path"]),
        slug_collisions,
        taxonomy,
        bool(rows[0].get("public_model_key")),
        grouped_gallery,
        stable_identity_key,
    )
    model_id = metadata["id"]
    gallery_images = []
    variant_files = 0

    for position, row in enumerate(rows):
        source = source_root / str(row["path"])
        if not source.is_file():
            raise FileNotFoundError(f"imagem ausente: {source}")
        sha = str(row["sha256"])
        image_id = stable_id("img", sha)
        base_key = f"media/{model_id}/{image_id}"
        variants, variant_meta = render_variants(source, r2_root, base_key)
        variant_files += len(variants)

        if include_original:
            suffix = source.suffix.lower() or ".bin"
            key = f"{base_key}/original{suffix}"
            byte_count = copy_original(source, r2_root / key)
            variants["original"] = key
            variant_meta["original"] = {
                "width": row.get("width"),
                "height": row.get("height"),
                "bytes": byte_count,
                "mime": f"image/{suffix.lstrip('.').replace('jpg', 'jpeg')}",
            }
            variant_files += 1

        gallery_images.append({
            "id": image_id,
            "role": "cover" if position == 0 else "gallery",
            "width": row.get("width"),
            "height": row.get("height"),
            "bytes": row.get("size"),
            "mime": f"image/{source.suffix.lower().lstrip('.').replace('jpg', 'jpeg')}",
            "qualityScore": row.get("quality_score"),
            "sourceSha256": sha,
            "variantKeys": variants,
            "variants": variant_meta,
        })

    identity_payload = {
        "modelId": model_id,
        "sourceHierarchy": metadata["sourceHierarchy"],
        "images": gallery_images,
    }
    gallery_digest, gallery_version = gallery_identity(identity_payload)
    gallery_key = f"gallery/{model_id}/{gallery_digest[:24]}.json"
    gallery = {
        "version": gallery_version,
        **identity_payload,
        "generatedBy": "tools/build_media_bundle.py",
    }
    gallery_path = r2_root / gallery_key
    gallery_path.parent.mkdir(parents=True, exist_ok=True)
    gallery_path.write_text(
        json.dumps(gallery, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    cover_key = gallery_images[0]["variantKeys"]["card"] if gallery_images else None
    model_entry = {
        **metadata,
        "imageCount": len(gallery_images),
        "coverStorageKey": cover_key,
        "galleryManifestKey": gallery_key,
        "galleryVersion": gallery_version,
    }
    return model_entry, len(gallery_images), variant_files



def validate_built_model_identifiers(entries: list[dict]) -> None:
    for field in ("id", "slug", "code"):
        owners: dict[str, str] = {}
        for entry in entries:
            value = str(entry.get(field) or "").strip()
            identity = str(entry.get("identityKey") or "").strip()
            if not value or not identity:
                raise RuntimeError(f"modelo construído sem {field}/identityKey: {entry!r}")
            previous = owners.get(value)
            if previous is not None and previous != identity:
                raise RuntimeError(
                    f"colisão de {field} no bundle entre produtos distintos: "
                    f"{value}: {previous} | {identity}"
                )
            owners[value] = identity


def validate_built_taxonomy_identifiers(entries: list[dict]) -> None:
    validate_taxonomy_slug_mappings(entries)


def validate_variant_disambiguation(entries: list[dict]) -> None:
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for entry in entries:
        key = (
            str(entry.get("categorySlug") or "").strip(),
            str(entry.get("franchiseSlug") or "").strip(),
            str(entry.get("folderPathKey") or "").strip(),
            str(entry.get("displayName") or "").strip().casefold(),
        )
        groups[key].append(entry)

    for key, members in groups.items():
        if len(members) < 2:
            continue
        owners: dict[str, str] = {}
        for entry in members:
            identity = str(entry.get("identityKey") or "").strip()
            variant = str(entry.get("variantName") or "").strip()
            normalized_variant = re.sub(r"\s+", " ", variant).casefold()
            if not normalized_variant:
                raise RuntimeError(
                    "produtos distintos ficariam visualmente indistinguíveis sem variante: "
                    f"{'/'.join(key[:3])} / {entry.get('displayName')!r}; identity={identity}"
                )
            previous = owners.get(normalized_variant)
            if previous is not None and previous != identity:
                raise RuntimeError(
                    "produtos distintos possuem a mesma variante pública: "
                    f"{variant!r}: {previous} | {identity}"
                )
            owners[normalized_variant] = identity


def model_build_fingerprint(
    identity_key: str,
    rows: list[dict],
    include_original: bool,
    taxonomy: dict,
    stable_identity_key: str | None = None,
) -> str:
    hierarchy = str(rows[0]["model_key"]).split(" / ")
    franchise_pos = franchise_index(hierarchy, taxonomy)
    category_name = hierarchy[0] if hierarchy else "Outros"
    franchise_name = hierarchy[franchise_pos] if hierarchy else category_name
    taxonomy_key = f"{slugify(category_name)}/{slugify(franchise_name)}"
    relevant_rule = taxonomy.get("franchises", {}).get(taxonomy_key, {})
    normalized_rows = [
        {
            "path": str(row.get("path") or ""),
            "sha256": str(row.get("sha256") or ""),
            "size": int(row.get("size") or 0),
            "width": int(row.get("width") or 0),
            "height": int(row.get("height") or 0),
            "quality_score": float(row.get("quality_score") or 0),
            "model_key": str(row.get("model_key") or ""),
            "public_model_key": str(row.get("public_model_key") or ""),
            "audit_model_group": str(row.get("audit_model_group") or ""),
            "identification": str(row.get("identification") or ""),
        }
        for row in sorted(rows, key=lambda item: str(item.get("path") or "").casefold())
    ]
    payload = {
        "stateVersion": MEDIA_BUILD_STATE_VERSION,
        "rendererVersion": MEDIA_RENDERER_VERSION,
        "metadataVersion": MEDIA_METADATA_VERSION,
        "identityKey": identity_key,
        "stableIdentityKey": stable_identity_key or identity_key,
        "includeOriginal": include_original,
        "variants": VARIANTS,
        "taxonomyRule": relevant_rule,
        "rows": normalized_rows,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_media_build_state(path: Path) -> dict:
    if not path.exists():
        return {"version": MEDIA_BUILD_STATE_VERSION, "models": {}, "identityHistory": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"checkpoint de mídia inválido: {path}: {exc}") from exc
    if state.get("version") != MEDIA_BUILD_STATE_VERSION or not isinstance(state.get("models"), dict):
        raise RuntimeError(f"versão de checkpoint de mídia incompatível: {path}")
    history = state.get("identityHistory", {})
    if not isinstance(history, dict):
        raise RuntimeError(f"histórico de identidade inválido no checkpoint: {path}")
    normalized_history: dict[str, str] = {}
    for raw_sha, raw_identity in history.items():
        sha = str(raw_sha).strip().lower()
        identity = str(raw_identity).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or not identity:
            raise RuntimeError(f"entrada inválida no histórico de identidade: {raw_sha!r}")
        normalized_history[sha] = identity
    state["identityHistory"] = normalized_history
    return state


def checkpoint_identity_history(state: dict, r2_root: Path) -> dict[str, str]:
    history = dict(state.get("identityHistory") or {})
    for stable_identity, entry in state.get("models", {}).items():
        if not isinstance(entry, dict):
            continue
        model_entry = entry.get("modelEntry")
        if not isinstance(model_entry, dict):
            continue
        manifest_key = str(model_entry.get("galleryManifestKey") or "").strip()
        if not manifest_key:
            continue
        manifest_path = r2_root / manifest_key
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        images = manifest.get("images")
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            sha = str(image.get("sourceSha256") or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                continue
            previous = history.get(sha)
            if previous is not None and previous != stable_identity:
                raise RuntimeError(
                    "checkpoint contém SHA histórico atribuído a identidades diferentes: "
                    f"{sha}: {previous} | {stable_identity}"
                )
            history[sha] = stable_identity
    return history


def validate_identity_continuity(
    by_model: dict[str, list[dict]],
    stable_keys: dict[str, str],
    history: dict[str, str],
) -> None:
    for identity_key, rows in by_model.items():
        stable_identity = stable_keys[identity_key]
        for row in rows:
            sha = str(row.get("sha256") or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise RuntimeError(f"SHA-256 inválido ao validar continuidade: {identity_key}")
            previous = history.get(sha)
            if previous is not None and previous != stable_identity:
                raise RuntimeError(
                    "identity drift detectado: imagem histórica reapareceu sob outro produto; "
                    "registre o renome em config/catalog-model-identity-aliases.json antes de publicar: "
                    f"{sha}: histórico={previous!r}, atual={identity_key!r}"
                )


def update_identity_history(
    history: dict[str, str],
    by_model: dict[str, list[dict]],
    stable_keys: dict[str, str],
) -> dict[str, str]:
    updated = dict(history)
    validate_identity_continuity(by_model, stable_keys, updated)
    for identity_key, rows in by_model.items():
        stable_identity = stable_keys[identity_key]
        for row in rows:
            updated[str(row["sha256"]).strip().lower()] = stable_identity
    return updated


def save_media_build_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for attempt in range(MEDIA_STATE_REPLACE_ATTEMPTS):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt + 1 >= MEDIA_STATE_REPLACE_ATTEMPTS:
                raise
            time.sleep(MEDIA_STATE_REPLACE_DELAY_SECONDS * (attempt + 1))


def cached_model_outputs_valid(entry: dict, r2_root: Path, include_original: bool) -> bool:
    model_entry = entry.get("modelEntry")
    if not isinstance(model_entry, dict):
        return False
    gallery_key = model_entry.get("galleryManifestKey")
    cover_key = model_entry.get("coverStorageKey")
    if not isinstance(gallery_key, str) or not gallery_key or not isinstance(cover_key, str) or not cover_key:
        return False
    gallery_path = r2_root / gallery_key
    cover_path = r2_root / cover_key
    if not gallery_path.is_file() or gallery_path.stat().st_size <= 0 or not cover_path.is_file() or cover_path.stat().st_size <= 0:
        return False
    try:
        gallery = json.loads(gallery_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    images = gallery.get("images")
    if gallery.get("modelId") != model_entry.get("id") or not isinstance(images, list) or len(images) != int(entry.get("imageCount") or -1):
        return False
    for image in images:
        keys = image.get("variantKeys")
        if not isinstance(keys, dict) or not all(name in keys for name in VARIANTS):
            return False
        if include_original and "original" not in keys:
            return False
        for key in keys.values():
            if not isinstance(key, str) or not key:
                return False
            path = r2_root / key
            if not path.is_file() or path.stat().st_size <= 0:
                return False
    return True


@contextmanager
def media_build_output_lock(output: Path):
    """Hold an OS-backed exclusive lock for one media bundle output directory.

    The lock file intentionally remains on disk; ownership is represented by the
    operating-system lock, so a crashed process cannot leave a stale logical
    lock that blocks future runs.
    """
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / ".media-build.lock"
    handle = lock_path.open("a+b")
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)

        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError(f"bundle de mídia já está em execução para: {output}") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"bundle de mídia já está em execução para: {output}") from exc
        locked = True
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def locked_media_build(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        output = kwargs.get("output")
        if output is None:
            if len(args) < 3:
                raise TypeError("output é obrigatório")
            output = args[2]
        with media_build_output_lock(Path(output)):
            return func(*args, **kwargs)

    return wrapped


@locked_media_build
def build_bundle(
    source_root: Path,
    manifest_path: Path,
    output: Path,
    include_original: bool,
    taxonomy_config: Path | None = None,
    identity_alias_config: Path | None = None,
    workers: int = 4,
    resume: bool = True,
) -> dict:
    taxonomy = load_taxonomy_config(taxonomy_config)
    identity_aliases = load_identity_aliases(identity_alias_config)
    records = [row for row in read_manifest(manifest_path) if row.get("status") == "OK" and row.get("canonical") is True]
    by_model: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        identity_key = str(row.get("public_model_key") or row["model_key"])
        by_model[identity_key].append(row)

    model_keys = sorted(by_model, key=str.casefold)
    validate_identity_comparison_collisions(model_keys)
    stable_keys = {
        identity_key: canonical_identity_key(identity_key, identity_aliases)
        for identity_key in model_keys
    }
    stable_owners: dict[str, str] = {}
    for identity_key, stable_key in stable_keys.items():
        previous = stable_owners.get(stable_key)
        if previous is not None and previous != identity_key:
            raise RuntimeError(
                "duas identidades atuais apontam para o mesmo produto canônico: "
                f"{stable_key}: {previous} | {identity_key}"
            )
        stable_owners[stable_key] = identity_key
    base_slugs = []
    for identity_key in model_keys:
        row = by_model[identity_key][0]
        hierarchy = str(row["model_key"]).split(" / ")
        franchise_pos = franchise_index(hierarchy, taxonomy)
        franchise_name = hierarchy[franchise_pos] if hierarchy else "Outros"
        audited_public = bool(row.get("public_model_key"))
        source_collection_parts = hierarchy[franchise_pos + 1:] if audited_public else hierarchy[franchise_pos + 1:-1]
        category_name = hierarchy[0] if hierarchy else "Outros"
        category_slug = slugify(category_name)
        franchise_slug = slugify(franchise_name)
        collection_parts = public_folder_path(category_slug, franchise_slug, source_collection_parts, taxonomy) if audited_public else source_collection_parts
        source_stem = Path(str(row["path"])).stem
        grouped_gallery = any(bool(member.get("audit_model_group")) for member in by_model[identity_key])
        display_name = hierarchy[-1] if grouped_gallery and hierarchy else model_display_name(hierarchy, collection_parts, source_stem, audited_public)
        stable_identity = stable_keys[identity_key]
        identity_label = public_identity_label(stable_identity, str(row["model_key"]), source_stem) if audited_public else source_stem
        slug_name = humanize_stem(identity_label) if audited_public else display_name
        slug_parts = [franchise_name, slug_name] if audited_public else [franchise_name, *collection_parts, display_name]
        base_slugs.append(slugify(" ".join(slug_parts)))
    slug_collisions = Counter(base_slugs)

    if workers < 1 or workers > 16:
        raise ValueError("workers deve ficar entre 1 e 16")

    r2_root = output / "r2"
    model_index = output / "models.jsonl"
    state_path = output / "media-build-state.json"
    output.mkdir(parents=True, exist_ok=True)
    state = load_media_build_state(state_path) if resume else {
        "version": MEDIA_BUILD_STATE_VERSION,
        "models": {},
        "identityHistory": {},
    }
    identity_history = checkpoint_identity_history(state, r2_root)
    validate_identity_continuity(by_model, stable_keys, identity_history)
    fingerprints = {
        identity_key: model_build_fingerprint(
            identity_key,
            by_model[identity_key],
            include_original,
            taxonomy,
            stable_keys[identity_key],
        )
        for identity_key in model_keys
    }
    results: dict[str, tuple[dict, int, int]] = {}
    pending: list[str] = []
    resumed_models = 0

    for identity_key in model_keys:
        cached = state["models"].get(stable_keys[identity_key])
        if (
            resume
            and isinstance(cached, dict)
            and cached.get("fingerprint") == fingerprints[identity_key]
            and cached_model_outputs_valid(cached, r2_root, include_original)
        ):
            results[identity_key] = (
                cached["modelEntry"],
                int(cached["imageCount"]),
                int(cached["variantFiles"]),
            )
            resumed_models += 1
        else:
            pending.append(identity_key)

    def build_one(identity_key: str) -> tuple[dict, int, int]:
        return build_model_bundle(
            identity_key=identity_key,
            rows=by_model[identity_key],
            source_root=source_root,
            r2_root=r2_root,
            slug_collisions=slug_collisions,
            taxonomy=taxonomy,
            include_original=include_original,
            stable_identity_key=stable_keys[identity_key],
        )

    built_models = 0
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="media-build")
    futures = {executor.submit(build_one, identity_key): identity_key for identity_key in pending}
    try:
        for future in as_completed(futures):
            identity_key = futures[future]
            model_entry, image_count, model_variant_files = future.result()
            results[identity_key] = (model_entry, image_count, model_variant_files)
            state["models"][stable_keys[identity_key]] = {
                "sourceIdentityKey": identity_key,
                "fingerprint": fingerprints[identity_key],
                "modelEntry": model_entry,
                "imageCount": image_count,
                "variantFiles": model_variant_files,
            }
            built_models += 1
            if built_models % MEDIA_CHECKPOINT_INTERVAL == 0:
                save_media_build_state(state_path, state)
                print(f"MEDIA_CHECKPOINT={built_models} RESUMED={resumed_models}", flush=True)
    except Exception:
        save_media_build_state(state_path, state)
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    state["models"] = {
        stable_keys[identity_key]: state["models"][stable_keys[identity_key]]
        for identity_key in model_keys
    }
    state["identityHistory"] = update_identity_history(identity_history, by_model, stable_keys)
    save_media_build_state(state_path, state)

    built_entries = [results[identity_key][0] for identity_key in model_keys]
    validate_built_model_identifiers(built_entries)
    validate_built_taxonomy_identifiers(built_entries)
    validate_variant_disambiguation(built_entries)

    temporary_index = model_index.with_suffix(model_index.suffix + ".tmp")
    published_images = 0
    variant_files = 0
    with temporary_index.open("w", encoding="utf-8") as index_handle:
        for identity_key in model_keys:
            model_entry, image_count, model_variant_files = results[identity_key]
            index_handle.write(json.dumps(model_entry, ensure_ascii=False) + "\n")
            published_images += image_count
            variant_files += model_variant_files
    temporary_index.replace(model_index)

    summary = {
        "models": len(by_model),
        "canonicalImages": published_images,
        "variantFiles": variant_files,
        "galleryManifests": len(by_model),
        "includeOriginal": include_original,
        "resumedModels": resumed_models,
        "builtModels": built_models,
        "r2Root": str(r2_root),
        "mediaBuildState": str(state_path),
        "identityAliasesApplied": sum(1 for key in model_keys if stable_keys[key] != key),
        "identityHistoryEntries": len(state["identityHistory"]),
    }
    (output / "publish-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera variantes WebP e manifestos prontos para o R2.")
    parser.add_argument("source", type=Path, help="raiz do catálogo original")
    parser.add_argument("manifest", type=Path, help="manifest.jsonl gerado por ingest_catalog.py")
    parser.add_argument("--output", type=Path, default=Path(".publish-bundle"))
    parser.add_argument("--include-original", action="store_true", help="inclui cópia do original no bundle R2")
    parser.add_argument("--taxonomy-config", type=Path, default=DEFAULT_TAXONOMY_CONFIG, help="regras explícitas de pastas públicas")
    parser.add_argument(
        "--identity-alias-config",
        type=Path,
        default=DEFAULT_IDENTITY_ALIAS_CONFIG,
        help="registro explícito de renomes de identidade que preservam mdl/code históricos",
    )
    parser.add_argument("--workers", type=int, default=4, help="modelos processados em paralelo; padrão: 4")
    parser.add_argument("--no-resume", action="store_true", help="ignora o checkpoint de mídia e recompõe todos os modelos")
    args = parser.parse_args()

    if not args.source.is_dir():
        parser.error(f"pasta fonte não encontrada: {args.source}")
    if not args.manifest.is_file():
        parser.error(f"manifesto não encontrado: {args.manifest}")

    summary = build_bundle(
        source_root=args.source.resolve(),
        manifest_path=args.manifest.resolve(),
        output=args.output.resolve(),
        include_original=args.include_original,
        taxonomy_config=args.taxonomy_config.resolve(),
        identity_alias_config=args.identity_alias_config.resolve(),
        workers=args.workers,
        resume=not args.no_resume,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
