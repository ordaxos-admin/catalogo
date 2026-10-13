#!/usr/bin/env python3
"""Publish models.jsonl to Cloudflare D1 without destructive reconciliation."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from publish_r2 import discover as discover_r2
from publish_r2 import load_state as load_r2_state
from catalog_scope import PUBLIC_TOP_LEVEL_CATEGORIES, PUBLIC_TOP_LEVEL_KEYS
from audit_model_identity import (
    sibling_suffix_candidates,
    split_view_candidates,
    validate_numbered_sibling_review,
)
from legacy_gallery_registry import DEFAULT_LEGACY_GALLERY_CONFIG, load_legacy_gallery_registry
from model_identity import validate_identity_comparison_collisions, validate_taxonomy_slug_mappings

CATEGORY_ORDER = {
    name: (index + 1) * 10
    for index, name in enumerate(PUBLIC_TOP_LEVEL_CATEGORIES)
}
REQUIRED = {
    "identityKey", "id", "slug", "code", "displayName", "variantName", "categoryName", "categorySlug",
    "franchiseName", "franchiseSlug", "collection", "folderPath", "folderPathKey", "searchText",
    "imageCount", "coverStorageKey", "galleryManifestKey", "galleryVersion",
}


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-") or "pasta"


def folder_entries(rows: list[dict[str, Any]]) -> list[tuple[str, str, str, str, str, int]]:
    entries: dict[tuple[str, str, str], tuple[str, str, str, str, str, int]] = {}
    for row in rows:
        names = row.get("folderPath")
        if not isinstance(names, list) or not all(isinstance(item, str) and item.strip() for item in names):
            raise RuntimeError(f"folderPath inválido no modelo {row['id']}")
        slugs = [slugify(item) for item in names]
        expected_key = "/".join(slugs)
        if str(row.get("folderPathKey") or "") != expected_key:
            raise RuntimeError(f"folderPathKey divergente no modelo {row['id']}")
        for depth in range(1, len(names) + 1):
            path = "/".join(slugs[:depth])
            parent = "/".join(slugs[:depth - 1])
            key = (str(row["categorySlug"]), str(row["franchiseSlug"]), path)
            value = (str(row["categorySlug"]), str(row["franchiseSlug"]), parent, slugs[depth - 1], names[depth - 1].strip(), depth)
            previous = entries.get(key)
            if previous and previous != value:
                raise RuntimeError(f"conflito de pasta pública: {key}")
            entries[key] = value
    return sorted(entries.values(), key=lambda item: (item[5], item[0], item[1], item[2], item[3]))


def load_models(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"JSON inválido em {path}:{line_no}: {exc}") from exc
            missing = REQUIRED.difference(row)
            if missing:
                raise RuntimeError(f"campos ausentes em {path}:{line_no}: {sorted(missing)}")
            rows.append(row)
    if not rows:
        raise RuntimeError(f"índice vazio: {path}")
    validate_models(rows)
    return rows


def validate_models(rows: list[dict[str, Any]]) -> None:
    validate_identity_comparison_collisions(
        str(row.get("identityKey") or "")
        for row in rows
    )
    validate_taxonomy_slug_mappings(rows)
    for key in ("identityKey", "id", "slug", "code"):
        counts = Counter(str(row[key]).strip() for row in rows)
        duplicates = sorted(value for value, count in counts.items() if not value or count > 1)
        if duplicates:
            raise RuntimeError(f"{key} vazio ou duplicado: {duplicates[:5]}")
    for row in rows:
        category_name = str(row["categoryName"]).strip()
        if category_name.casefold() not in PUBLIC_TOP_LEVEL_KEYS:
            raise RuntimeError(f"categoria fora do escopo público no modelo {row['id']}: {category_name!r}")
        if not str(row["categorySlug"]).strip() or not str(row["franchiseSlug"]).strip():
            raise RuntimeError(f"taxonomia vazia no modelo {row['id']}")
        variant_name = row.get("variantName")
        if not isinstance(variant_name, str) or len(variant_name) > 160:
            raise RuntimeError(f"variantName inválido no modelo {row['id']}")
        if int(row["imageCount"]) < 1:
            raise RuntimeError(f"modelo sem imagem publicável: {row['id']}")
    folder_entries(rows)

    unresolved = [
        candidate
        for candidate in split_view_candidates(rows)
        if candidate["confidence"] == "high"
    ]
    if unresolved:
        sample = ", ".join(candidate["family"] for candidate in unresolved[:5])
        raise RuntimeError(
            f"fichas fragmentadas por vista detectadas: {len(unresolved)} grupo(s) de alta confiança; "
            f"consolide a identidade do produto antes de publicar: {sample}"
        )

    explicit_copy_markers = [
        candidate
        for candidate in sibling_suffix_candidates(rows)
        if candidate["kind"] == "explicit-copy-marker"
    ]
    if explicit_copy_markers:
        sample = ", ".join(candidate["baseSlug"] for candidate in explicit_copy_markers[:5])
        raise RuntimeError(
            f"marcadores explícitos de cópia detectados em {len(explicit_copy_markers)} grupo(s); "
            f"revise a identidade antes de publicar: {sample}"
        )

    # Sufixos numéricos são revisão de release, não erro estrutural do arquivo.
    # O snapshot completo é reconciliado em validate_complete_numbered_review().


def validate_complete_numbered_review(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return validate_numbered_sibling_review(rows, require_complete_registry=True)


def safe_storage_key(value: Any, expected_prefix: str, model_id: str) -> str:
    key = str(value or "").strip()
    path = PurePosixPath(key)
    model_prefix = f"{expected_prefix}{model_id}/"
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or ".." in path.parts
        or not key.startswith(model_prefix)
    ):
        raise RuntimeError(
            f"chave R2 inválida ou pertencente a outro modelo {model_id}: {key!r}"
        )
    return key


def validate_gallery_manifest_contract(
    manifest: dict[str, Any],
    row: dict[str, Any],
    manifest_key: str,
) -> list[str]:
    model_id = str(row["id"])
    if str(manifest.get("modelId") or "") != model_id:
        raise RuntimeError(f"manifesto pertence a outro modelo: {manifest_key}")
    try:
        version = int(manifest.get("version"))
        expected_version = int(row["galleryVersion"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"versão de galeria inválida no manifesto: {manifest_key}") from exc
    if version != expected_version:
        raise RuntimeError(
            f"versão de galeria divergente no manifesto {manifest_key}: "
            f"{version} != {expected_version}"
        )

    images = manifest.get("images")
    if not isinstance(images, list) or len(images) != int(row["imageCount"]):
        raise RuntimeError(f"quantidade de imagens divergente no manifesto: {manifest_key}")
    if not images:
        raise RuntimeError(f"manifesto sem imagens: {manifest_key}")

    image_ids: set[str] = set()
    cover_count = 0
    required_keys: list[str] = []
    first_card_key = ""
    for index, image in enumerate(images):
        if not isinstance(image, dict):
            raise RuntimeError(f"imagem inválida no manifesto: {manifest_key}")
        image_id = str(image.get("id") or "").strip()
        role = str(image.get("role") or "").strip()
        source_sha = str(image.get("sourceSha256") or "").strip().lower()
        if not image_id or image_id in image_ids:
            raise RuntimeError(f"id de imagem vazio/duplicado no manifesto: {manifest_key}")
        image_ids.add(image_id)
        if role not in {"cover", "gallery"}:
            raise RuntimeError(f"role de imagem inválido no manifesto: {manifest_key}")
        if role == "cover":
            cover_count += 1
        if index == 0 and role != "cover":
            raise RuntimeError(f"primeira imagem não é capa no manifesto: {manifest_key}")
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha):
            raise RuntimeError(f"sourceSha256 inválido no manifesto: {manifest_key}")

        variants = image.get("variantKeys")
        if not isinstance(variants, dict):
            raise RuntimeError(f"variantKeys inválido no manifesto: {manifest_key}")
        for variant_name in ("thumb", "card", "detail"):
            required_keys.append(
                safe_storage_key(variants.get(variant_name), "media/", model_id)
            )
        if variants.get("original") not in (None, ""):
            required_keys.append(
                safe_storage_key(variants.get("original"), "media/", model_id)
            )
        if index == 0:
            first_card_key = str(variants.get("card") or "").strip()

    if cover_count != 1:
        raise RuntimeError(f"manifesto deve possuir exatamente uma capa: {manifest_key}")
    expected_cover = str(row.get("coverStorageKey") or "").strip()
    if first_card_key != expected_cover:
        raise RuntimeError(
            f"coverStorageKey diverge da capa do manifesto {manifest_key}: "
            f"{expected_cover!r} != {first_card_key!r}"
        )
    return required_keys


def validate_r2_ready(models_path: Path, rows: list[dict[str, Any]], state_path: Path) -> dict[str, Any]:
    models_path = models_path.resolve()
    r2_root = models_path.parent / "r2"
    if not r2_root.is_dir():
        raise RuntimeError(f"bundle R2 ausente: {r2_root}")

    state = load_r2_state(state_path)
    candidates = discover_r2(r2_root, state)
    if not candidates:
        raise RuntimeError(f"bundle R2 vazio: {r2_root}")

    by_key = {candidate.key: candidate for candidate in candidates}
    required_keys: set[str] = set()

    for row in rows:
        model_id = str(row["id"])
        cover_key = safe_storage_key(row["coverStorageKey"], "media/", model_id)
        manifest_key = safe_storage_key(row["galleryManifestKey"], "gallery/", model_id)
        required_keys.update((cover_key, manifest_key))

        manifest_path = r2_root / PurePosixPath(manifest_key)
        if not manifest_path.is_file():
            raise RuntimeError(f"manifesto de galeria ausente no bundle: {manifest_key}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"manifesto de galeria inválido: {manifest_key}: {exc}") from exc
        for key in validate_gallery_manifest_contract(manifest, row, manifest_key):
            required_keys.add(key)

    missing_from_bundle = sorted(required_keys.difference(by_key))
    if missing_from_bundle:
        raise RuntimeError(
            f"bundle R2 incompleto: {len(missing_from_bundle)} chave(s) referenciada(s) ausente(s): "
            f"{missing_from_bundle[:5]}"
        )

    state_objects = state["objects"]
    pending = sorted(
        candidate.key
        for candidate in candidates
        if state_objects.get(candidate.key, {}).get("published_sha256") != candidate.sha256
    )
    if pending:
        raise RuntimeError(
            f"R2 ainda não está pronto: {len(pending)} de {len(candidates)} objeto(s) sem publicação confirmada "
            f"no checkpoint: {pending[:5]}"
        )

    return {
        "objects": len(candidates),
        "requiredKeys": len(required_keys),
        "checkpoint": str(state_path),
        "ready": True,
    }


def candidate_image_sources(
    models_path: Path,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    r2_root = models_path.resolve().parent / "r2"
    sources: list[dict[str, Any]] = []
    global_sha_owner: dict[str, str] = {}

    for row in sorted(rows, key=lambda item: str(item["id"])):
        model_id = str(row["id"])
        gallery_version = int(row["galleryVersion"])
        manifest_key = safe_storage_key(row["galleryManifestKey"], "gallery/", model_id)
        manifest_path = r2_root / PurePosixPath(manifest_key)
        if not manifest_path.is_file():
            raise RuntimeError(f"manifesto de galeria ausente no bundle: {manifest_key}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"manifesto de galeria inválido: {manifest_key}: {exc}") from exc

        validate_gallery_manifest_contract(manifest, row, manifest_key)
        images = manifest["images"]
        seen_sha: set[str] = set()
        for position, image in enumerate(images):
            source_sha = str(image["sourceSha256"]).strip().lower()
            if source_sha in seen_sha:
                raise RuntimeError(
                    f"SHA-256 duplicado dentro da mesma galeria {model_id}: {source_sha}"
                )
            seen_sha.add(source_sha)
            previous_owner = global_sha_owner.get(source_sha)
            if previous_owner is not None and previous_owner != model_id:
                raise RuntimeError(
                    "imagem exata aparece em produtos candidatos diferentes: "
                    f"{source_sha}: {previous_owner} | {model_id}"
                )
            global_sha_owner[source_sha] = model_id
            sources.append({
                "model_id": model_id,
                "image_id": str(image["id"]),
                "position": position,
                "role": str(image["role"]),
                "source_sha256": source_sha,
                "gallery_version": gallery_version,
            })

    return sources


def build_image_source_statements(
    models_path: Path,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sources = candidate_image_sources(models_path, rows)
    statements: list[dict[str, Any]] = []
    versions: dict[str, int] = {}

    for source in sources:
        model_id = str(source["model_id"])
        gallery_version = int(source["gallery_version"])
        versions[model_id] = gallery_version
        statements.append({
            "sql": """INSERT INTO model_image_sources(
model_id,image_id,position,role,source_sha256,gallery_version)
VALUES(?,?,?,?,?,?)
ON CONFLICT(model_id,image_id) DO UPDATE SET
position=excluded.position,role=excluded.role,source_sha256=excluded.source_sha256,
gallery_version=excluded.gallery_version""",
            "params": [
                model_id,
                str(source["image_id"]),
                str(int(source["position"])),
                str(source["role"]),
                str(source["source_sha256"]),
                str(gallery_version),
            ],
        })

    for model_id in sorted(versions):
        statements.append({
            "sql": "DELETE FROM model_image_sources WHERE model_id=? AND gallery_version<>?",
            "params": [model_id, str(versions[model_id])],
        })

    return statements


def production_sha_lookup_statements(
    sources: list[dict[str, Any]],
    chunk_size: int = 100,
) -> list[dict[str, Any]]:
    if chunk_size < 1 or chunk_size > 250:
        raise ValueError("chunk_size SHA deve ficar entre 1 e 250")
    shas = sorted({str(item["source_sha256"]) for item in sources})
    statements: list[dict[str, Any]] = []
    for start in range(0, len(shas), chunk_size):
        chunk = shas[start:start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        statements.append({
            "sql": f"""SELECT
s.source_sha256,
COALESCE(member.canonical_model_id,s.model_id) AS effective_model_id,
effective.slug AS effective_model_slug
FROM model_image_sources s
JOIN models source
  ON source.id=s.model_id
 AND source.gallery_version=s.gallery_version
LEFT JOIN model_gallery_members member ON member.source_model_id=source.id
JOIN models effective ON effective.id=COALESCE(member.canonical_model_id,source.id)
WHERE effective.published=1
  AND s.source_sha256 IN ({placeholders})
ORDER BY s.source_sha256,effective_model_id""",
            "params": chunk,
        })
    return statements


def legacy_gallery_owner_moves(
    rows: list[dict[str, Any]],
    config_path: Path | None = None,
) -> dict[str, str]:
    registry = load_legacy_gallery_registry(config_path or DEFAULT_LEGACY_GALLERY_CONFIG)
    candidate_by_slug = {str(row["slug"]): str(row["id"]) for row in rows}
    approved: dict[str, str] = {}
    for group in registry["groups"]:
        canonical_slug = str(group["canonicalSlug"])
        canonical_id = candidate_by_slug.get(canonical_slug)
        if canonical_id is None:
            continue
        for source_slug in group["memberSlugs"]:
            source_slug = str(source_slug)
            if source_slug == canonical_slug:
                continue
            previous = approved.get(source_slug)
            if previous is not None and previous != canonical_id:
                raise RuntimeError(
                    "fonte legada possui destinos canônicos conflitantes: "
                    f"{source_slug}: {previous} | {canonical_id}"
                )
            approved[source_slug] = canonical_id
    return approved


def validate_production_image_identity(
    sources: list[dict[str, Any]],
    production_rows: list[dict[str, Any]],
    approved_owner_moves: dict[str, str] | None = None,
) -> dict[str, Any]:
    candidate_owner = {
        str(source["source_sha256"]): str(source["model_id"])
        for source in sources
    }
    approved_owner_moves = approved_owner_moves or {}
    matches = 0
    approved_moves = 0
    for row in production_rows:
        sha = str(row.get("source_sha256") or "").strip().lower()
        effective_model_id = str(row.get("effective_model_id") or "").strip()
        effective_model_slug = str(row.get("effective_model_slug") or "").strip()
        if not sha or not effective_model_id or not effective_model_slug:
            raise RuntimeError(f"linha inválida no baseline SHA D1: {row!r}")
        expected_owner = candidate_owner.get(sha)
        if expected_owner is None:
            raise RuntimeError(f"baseline SHA retornou hash não solicitado: {sha}")
        if effective_model_id != expected_owner:
            if approved_owner_moves.get(effective_model_slug) != expected_owner:
                raise RuntimeError(
                    "identity drift de produção: imagem histórica pertence a outro produto efetivo; "
                    f"{sha}: produção={effective_model_id}, candidato={expected_owner}"
                )
            approved_moves += 1
        matches += 1
    return {
        "ready": True,
        "candidateImages": len(sources),
        "historicalMatches": matches,
        "approvedLegacyGalleryMoves": approved_moves,
        "identityDrift": 0,
    }


def fetch_production_sha_matches(
    account_id: str,
    database_id: str,
    token: str,
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    statements = production_sha_lookup_statements(sources)
    if not statements:
        return []
    body = request_batch_json(account_id, database_id, token, statements)
    result = body.get("result")
    if not isinstance(result, list) or len(result) != len(statements):
        raise RuntimeError("resposta inesperada ao consultar baseline SHA D1")
    rows: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict) or item.get("success") is not True:
            raise RuntimeError(
                "índice SHA de produção indisponível; aplique o contrato de schema antes de publicar"
            )
        found = item.get("results")
        if not isinstance(found, list):
            raise RuntimeError("baseline SHA D1 retornou results inválido")
        rows.extend(row for row in found if isinstance(row, dict))
    return rows


def exact_cross_model_sha_query(limit: int = 50) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise ValueError("limite de auditoria SHA deve ficar entre 1 e 500")
    return {
        "sql": """SELECT
s.source_sha256,
COUNT(DISTINCT COALESCE(member.canonical_model_id,s.model_id)) AS model_count,
GROUP_CONCAT(DISTINCT COALESCE(member.canonical_model_id,s.model_id)) AS model_ids
FROM model_image_sources s
JOIN models source ON source.id=s.model_id AND source.gallery_version=s.gallery_version
LEFT JOIN model_gallery_members member ON member.source_model_id=source.id
JOIN models effective ON effective.id=COALESCE(member.canonical_model_id,source.id)
WHERE effective.published=1
GROUP BY s.source_sha256
HAVING COUNT(DISTINCT COALESCE(member.canonical_model_id,s.model_id))>1
ORDER BY model_count DESC,s.source_sha256
LIMIT ?""",
        "params": [limit],
    }


def build_statements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    categories = {
        (str(row["categorySlug"]), str(row["categoryName"]))
        for row in rows
    }
    for slug, name in sorted(categories):
        statements.append({
            "sql": """INSERT INTO categories(slug,name,sort_order)
VALUES(?,?,?)
ON CONFLICT(slug) DO UPDATE SET name=excluded.name,sort_order=excluded.sort_order""",
            "params": [slug, name, str(CATEGORY_ORDER.get(name, 900))],
        })

    franchises = {
        (
            str(row["categorySlug"]), str(row["franchiseSlug"]),
            str(row["franchiseName"]),
        )
        for row in rows
    }
    for category_slug, slug, name in sorted(franchises):
        statements.append({
            "sql": """INSERT INTO franchises(category_id,slug,name)
SELECT id,?,? FROM categories WHERE slug=?
ON CONFLICT(category_id,slug) DO UPDATE SET name=excluded.name""",
            "params": [slug, name, category_slug],
        })

    for category_slug, franchise_slug, parent_path, folder_slug, folder_name, depth in folder_entries(rows):
        path = "/".join([part for part in [parent_path, folder_slug] if part])
        statements.append({
            "sql": """INSERT INTO catalog_folders(franchise_id,parent_id,slug,name,path,depth,sort_order)
SELECT f.id,
  CASE WHEN ?='' THEN NULL ELSE (SELECT id FROM catalog_folders WHERE franchise_id=f.id AND path=?) END,
  ?,?,?,?,0
FROM franchises f JOIN categories c ON c.id=f.category_id
WHERE c.slug=? AND f.slug=?
ON CONFLICT(franchise_id,path) DO UPDATE SET
parent_id=excluded.parent_id,slug=excluded.slug,name=excluded.name,depth=excluded.depth,sort_order=excluded.sort_order""",
            "params": [parent_path, parent_path, folder_slug, folder_name, path, str(depth), category_slug, franchise_slug],
        })
    for row in sorted(rows, key=lambda item: str(item["id"])):
        statements.append({
            "sql": """INSERT INTO models(
id,franchise_id,folder_id,slug,code,name,variant_name,collection,image_count,cover_storage_key,
gallery_manifest_key,gallery_version,published,search_text,updated_at)
SELECT ?,f.id,
  CASE WHEN ?='' THEN NULL ELSE (SELECT id FROM catalog_folders WHERE franchise_id=f.id AND path=?) END,
  ?,
  ?,
  ?,
  ?,
  ?,
  ?,
  ?,
  ?,
  ?,
  1,
  ?,
  CURRENT_TIMESTAMP
FROM franchises f JOIN categories c ON c.id=f.category_id
WHERE c.slug=? AND f.slug=?
ON CONFLICT(id) DO UPDATE SET
franchise_id=excluded.franchise_id,folder_id=excluded.folder_id,slug=excluded.slug,code=excluded.code,
name=excluded.name,variant_name=excluded.variant_name,collection=excluded.collection,image_count=excluded.image_count,
cover_storage_key=excluded.cover_storage_key,gallery_manifest_key=excluded.gallery_manifest_key,
gallery_version=excluded.gallery_version,published=1,
search_text=excluded.search_text,updated_at=CURRENT_TIMESTAMP""",
            "params": [
                str(row["id"]), str(row["folderPathKey"] or ""), str(row["folderPathKey"] or ""), str(row["slug"]), str(row["code"]),
                str(row["displayName"]), str(row["variantName"]), str(row["collection"] or ""),
                str(int(row["imageCount"])), str(row["coverStorageKey"] or ""),
                str(row["galleryManifestKey"] or ""),
                str(int(row["galleryVersion"])), str(row["searchText"]),
                str(row["categorySlug"]), str(row["franchiseSlug"]),
            ],
        })
    return statements


def chunked(items: list[dict[str, Any]], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def production_lookup_statements(rows: list[dict[str, Any]], chunk_size: int = 25) -> list[dict[str, Any]]:
    if chunk_size < 1:
        raise ValueError("chunk_size deve ser positivo")
    statements: list[dict[str, Any]] = []
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        ids = [str(row["id"]) for row in chunk]
        slugs = [str(row["slug"]) for row in chunk]
        codes = [str(row["code"]) for row in chunk]
        placeholders = ",".join("?" for _ in chunk)
        statements.append({
            "sql": f"""SELECT
m.id,m.slug,m.code,m.name,m.variant_name,m.collection,
COALESCE(cf.path,'') AS folder_path,
m.image_count,m.cover_storage_key,m.gallery_manifest_key,m.gallery_version,
EXISTS(
  SELECT 1 FROM model_gallery_members member
  WHERE member.source_model_id=m.id
) AS gallery_source_attached,
(
  SELECT member.canonical_model_id FROM model_gallery_members member
  WHERE member.source_model_id=m.id
  LIMIT 1
) AS gallery_canonical_model_id,
c.slug AS category_slug,f.slug AS franchise_slug
FROM models m
JOIN franchises f ON f.id=m.franchise_id
JOIN categories c ON c.id=f.category_id
LEFT JOIN catalog_folders cf ON cf.id=m.folder_id
WHERE m.id IN ({placeholders})
   OR m.slug IN ({placeholders})
   OR m.code IN ({placeholders})""",
            "params": [*ids, *slugs, *codes],
        })
    return statements


def read_gallery_shrink_approvals(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    if not path.is_file():
        raise RuntimeError(f"aprovações de redução de galeria não encontradas: {path}")
    required = {
        "model_id",
        "current_image_count",
        "new_image_count",
        "current_gallery_version",
        "new_gallery_version",
        "reason",
    }
    approvals: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError(
                f"aprovações de redução sem colunas obrigatórias {sorted(required)}: {path}"
            )
        for line_no, row in enumerate(reader, 2):
            model_id = str(row.get("model_id") or "").strip()
            reason = str(row.get("reason") or "").strip()
            if not model_id or not reason:
                raise RuntimeError(f"aprovação de redução inválida em {path}, linha {line_no}")
            if model_id in approvals:
                raise RuntimeError(f"aprovação de redução duplicada para modelo: {model_id}")
            try:
                current_count = int(str(row.get("current_image_count") or "").strip())
                new_count = int(str(row.get("new_image_count") or "").strip())
                current_version = int(str(row.get("current_gallery_version") or "").strip())
                new_version = int(str(row.get("new_gallery_version") or "").strip())
            except ValueError as exc:
                raise RuntimeError(
                    f"aprovação de redução com contagem/versão inválida em {path}, linha {line_no}"
                ) from exc
            if current_count < 1 or new_count < 1 or new_count >= current_count:
                raise RuntimeError(
                    f"aprovação não representa redução real em {path}, linha {line_no}"
                )
            if current_version < 1 or new_version < 1 or current_version == new_version:
                raise RuntimeError(
                    f"aprovação exige versões válidas e diferentes em {path}, linha {line_no}"
                )
            approvals[model_id] = {
                "model_id": model_id,
                "current_image_count": current_count,
                "new_image_count": new_count,
                "current_gallery_version": current_version,
                "new_gallery_version": new_version,
                "reason": reason,
            }
    return approvals


def read_model_retirement_approvals(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    if not path.is_file():
        raise RuntimeError(f"aprovações de aposentadoria não encontradas: {path}")
    required = {"model_id", "slug", "code", "reason"}
    approvals: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError(
                f"aprovações de aposentadoria sem colunas obrigatórias {sorted(required)}: {path}"
            )
        for line_no, row in enumerate(reader, 2):
            normalized = {key: str(row.get(key) or "").strip() for key in required}
            if not all(normalized.values()):
                raise RuntimeError(f"aprovação de aposentadoria inválida em {path}, linha {line_no}")
            model_id = normalized["model_id"]
            if model_id in approvals:
                raise RuntimeError(f"aprovação de aposentadoria duplicada para modelo: {model_id}")
            approvals[model_id] = normalized
    return approvals


def production_inventory_statement(after_id: str, limit: int) -> dict[str, Any]:
    if limit < 1 or limit > 5000:
        raise ValueError("limite do inventário deve ficar entre 1 e 5000")
    return {
        "sql": """SELECT
id,slug,code,name,published,
(SELECT COUNT(*) FROM model_gallery_members member WHERE member.canonical_model_id=models.id) AS gallery_member_count
FROM models
WHERE published=1 AND id>?
ORDER BY id
LIMIT ?""",
        "params": [after_id, limit],
    }


def validate_model_retirements(
    candidate_rows: list[dict[str, Any]],
    production_inventory: list[dict[str, Any]],
    approvals: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidate_ids = {str(row["id"]).strip() for row in candidate_rows}
    production_by_id: dict[str, dict[str, Any]] = {}
    for row in production_inventory:
        model_id = str(row.get("id") or "").strip()
        slug = str(row.get("slug") or "").strip()
        code = str(row.get("code") or "").strip()
        if not model_id or not slug or not code:
            raise RuntimeError(f"inventário publicado inválido: {row!r}")
        previous = production_by_id.get(model_id)
        if previous is not None and previous != row:
            raise RuntimeError(f"inventário publicado contém ID duplicado: {model_id}")
        production_by_id[model_id] = row

    absent_ids = sorted(set(production_by_id).difference(candidate_ids))
    approval_ids = set(approvals)

    unexpected = sorted(approval_ids.difference(absent_ids))
    if unexpected:
        raise RuntimeError(
            f"aprovações de aposentadoria sem modelo ausente correspondente: {unexpected[:5]}"
        )

    missing = sorted(set(absent_ids).difference(approval_ids))
    if missing:
        raise RuntimeError(
            f"snapshot não contém {len(absent_ids)} modelo(s) publicado(s), mas faltam "
            f"{len(missing)} aprovação(ões) de aposentadoria: {missing[:5]}"
        )

    retirements: list[dict[str, Any]] = []
    for model_id in absent_ids:
        current = production_by_id[model_id]
        approval = approvals[model_id]
        if approval["slug"] != str(current["slug"]) or approval["code"] != str(current["code"]):
            raise RuntimeError(
                f"aprovação de aposentadoria não corresponde ao estado atual de {model_id}: "
                f"slug/code divergentes"
            )
        if int(current.get("gallery_member_count") or 0) > 0:
            raise RuntimeError(
                f"modelo canônico com fontes anexadas não pode ser aposentado: {model_id}"
            )
        retirements.append({
            "sql": """UPDATE models
SET published=0,updated_at=CURRENT_TIMESTAMP
WHERE id=? AND slug=? AND code=? AND published=1""",
            "params": [model_id, approval["slug"], approval["code"]],
        })

    return {
        "ready": True,
        "candidateModels": len(candidate_ids),
        "publishedInventory": len(production_by_id),
        "absentPublishedModels": len(absent_ids),
        "approvedRetirements": len(retirements),
    }, retirements


def validate_production_compatibility(
    rows: list[dict[str, Any]],
    production_rows: list[dict[str, Any]],
    gallery_shrink_approvals: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    approvals = dict(gallery_shrink_approvals or {})
    used_approvals: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    by_slug: dict[str, dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}
    for row in production_rows:
        model_id = str(row.get("id") or "").strip()
        slug = str(row.get("slug") or "").strip()
        code = str(row.get("code") or "").strip()
        if not model_id or not slug or not code:
            raise RuntimeError(f"baseline D1 inválido: {row!r}")
        if model_id in by_id and by_id[model_id] != row:
            raise RuntimeError(f"ID duplicado no baseline D1: {model_id}")
        if slug in by_slug and str(by_slug[slug].get("id")) != model_id:
            raise RuntimeError(f"slug duplicado no baseline D1: {slug}")
        if code in by_code and str(by_code[code].get("id")) != model_id:
            raise RuntimeError(f"código duplicado no baseline D1: {code}")
        by_id[model_id] = row
        by_slug[slug] = row
        by_code[code] = row

    existing = 0
    new = 0
    for candidate in rows:
        model_id = str(candidate["id"]).strip()
        slug = str(candidate["slug"]).strip()
        code = str(candidate["code"]).strip()
        category_slug = str(candidate["categorySlug"]).strip()
        franchise_slug = str(candidate["franchiseSlug"]).strip()

        current = by_id.get(model_id)
        slug_owner = by_slug.get(slug)
        code_owner = by_code.get(code)

        if current is None:
            if slug_owner is not None:
                raise RuntimeError(
                    f"novo ID colide com slug já publicado: {model_id} -> {slug}; "
                    f"owner={slug_owner['id']}"
                )
            if code_owner is not None:
                raise RuntimeError(
                    f"novo ID colide com código já publicado: {model_id} -> {code}; "
                    f"owner={code_owner['id']}"
                )
            new += 1
            continue

        existing += 1
        if int(current.get("gallery_source_attached") or 0) == 1:
            canonical_id = str(current.get("gallery_canonical_model_id") or "").strip()
            raise RuntimeError(
                f"ficha-fonte consolidada reapareceu no snapshot publicável: {model_id}; "
                f"canônico={canonical_id or 'desconhecido'}"
            )
        if str(current["slug"]) != slug:
            raise RuntimeError(
                f"slug de modelo publicado mudaria para o mesmo ID: {model_id}: "
                f"{current['slug']!r} -> {slug!r}"
            )
        if str(current["code"]) != code:
            raise RuntimeError(
                f"código de modelo publicado mudaria para o mesmo ID: {model_id}: "
                f"{current['code']!r} -> {code!r}"
            )
        if str(current.get("category_slug") or "") != category_slug:
            raise RuntimeError(
                f"categoria de modelo publicado mudaria sem migração explícita: {model_id}: "
                f"{current.get('category_slug')!r} -> {category_slug!r}"
            )
        if str(current.get("franchise_slug") or "") != franchise_slug:
            raise RuntimeError(
                f"franquia de modelo publicado mudaria sem migração explícita: {model_id}: "
                f"{current.get('franchise_slug')!r} -> {franchise_slug!r}"
            )
        if str(current.get("name") or "") != str(candidate["displayName"]).strip():
            raise RuntimeError(
                f"nome de modelo publicado mudaria sem migração explícita: {model_id}: "
                f"{current.get('name')!r} -> {candidate['displayName']!r}"
            )
        if str(current.get("collection") or "") != str(candidate.get("collection") or ""):
            raise RuntimeError(
                f"coleção de modelo publicado mudaria sem migração explícita: {model_id}: "
                f"{current.get('collection')!r} -> {candidate.get('collection')!r}"
            )
        if str(current.get("folder_path") or "") != str(candidate.get("folderPathKey") or ""):
            raise RuntimeError(
                f"pasta de modelo publicado mudaria sem migração explícita: {model_id}: "
                f"{current.get('folder_path')!r} -> {candidate.get('folderPathKey')!r}"
            )

        try:
            current_count = int(current.get("image_count"))
            new_count = int(candidate.get("imageCount"))
            current_version = int(current.get("gallery_version"))
            new_version = int(candidate.get("galleryVersion"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"contrato de galeria inválido no modelo publicado {model_id}") from exc
        current_manifest = str(current.get("gallery_manifest_key") or "").strip()
        new_manifest = str(candidate.get("galleryManifestKey") or "").strip()
        current_cover = str(current.get("cover_storage_key") or "").strip()
        new_cover = str(candidate.get("coverStorageKey") or "").strip()
        if current_count < 1 or new_count < 1 or current_version < 1 or new_version < 1:
            raise RuntimeError(f"contrato de galeria inválido no modelo publicado {model_id}")
        if not current_manifest or not new_manifest or not current_cover or not new_cover:
            raise RuntimeError(f"metadados de galeria incompletos no modelo publicado {model_id}")

        same_manifest = current_manifest == new_manifest
        gallery_changed = (
            current_count != new_count
            or current_manifest != new_manifest
            or current_cover != new_cover
        )
        if same_manifest and (
            current_count != new_count
            or current_cover != new_cover
            or current_version != new_version
        ):
            raise RuntimeError(
                f"manifesto content-addressed igual com metadados divergentes: {model_id}"
            )
        if gallery_changed and current_version == new_version:
            raise RuntimeError(
                f"galeria mudou sem nova gallery_version: {model_id}: {current_version}"
            )
        if not gallery_changed and current_version != new_version:
            raise RuntimeError(
                f"gallery_version mudou sem alteração da galeria: {model_id}: "
                f"{current_version} -> {new_version}"
            )

        if new_count < current_count:
            approval = approvals.get(model_id)
            if approval is None:
                raise RuntimeError(
                    f"redução de galeria exige aprovação explícita: {model_id}: "
                    f"{current_count} -> {new_count}"
                )
            expected = {
                "current_image_count": current_count,
                "new_image_count": new_count,
                "current_gallery_version": current_version,
                "new_gallery_version": new_version,
            }
            mismatches = [
                key for key, value in expected.items()
                if int(approval.get(key, -1)) != value
            ]
            if mismatches:
                raise RuntimeError(
                    f"aprovação de redução não corresponde ao delta atual de {model_id}: {mismatches}"
                )
            used_approvals.add(model_id)

        if slug_owner is not None and str(slug_owner["id"]) != model_id:
            raise RuntimeError(f"slug pertence a outro modelo publicado: {slug} -> {slug_owner['id']}")
        if code_owner is not None and str(code_owner["id"]) != model_id:
            raise RuntimeError(f"código pertence a outro modelo publicado: {code} -> {code_owner['id']}")

    unused_approvals = sorted(set(approvals).difference(used_approvals))
    if unused_approvals:
        raise RuntimeError(
            f"aprovações de redução sem delta correspondente: {unused_approvals[:5]}"
        )

    expanded = 0
    changed_same_count = 0
    unchanged = 0
    for candidate in rows:
        current = by_id.get(str(candidate["id"]).strip())
        if current is None:
            continue
        current_count = int(current["image_count"])
        new_count = int(candidate["imageCount"])
        if new_count > current_count:
            expanded += 1
        elif new_count == current_count and str(current["gallery_manifest_key"]) != str(candidate["galleryManifestKey"]):
            changed_same_count += 1
        elif new_count == current_count:
            unchanged += 1

    return {
        "ready": True,
        "candidateModels": len(rows),
        "existingModels": existing,
        "newModels": new,
        "productionMatches": len(production_rows),
        "expandedGalleries": expanded,
        "changedSameCountGalleries": changed_same_count,
        "reducedGalleries": len(used_approvals),
        "unchangedGalleries": unchanged,
        "galleryShrinkApprovalsUsed": len(used_approvals),
    }


def request_batch_json(
    account_id: str,
    database_id: str,
    token: str,
    batch: list[dict[str, Any]],
) -> dict[str, Any]:
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/d1/database/{database_id}/query"
    )
    payload = json.dumps({"batch": batch}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "tonecos-catalogo-publisher/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"D1 HTTP {exc.code}: {detail}") from exc
    if not body.get("success"):
        raise RuntimeError(f"D1 recusou batch: {body.get('errors')}")
    return body


def fetch_production_matches(
    account_id: str,
    database_id: str,
    token: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    statements = production_lookup_statements(rows)
    if not statements:
        return []
    body = request_batch_json(account_id, database_id, token, statements)
    result = body.get("result")
    if not isinstance(result, list) or len(result) != len(statements):
        raise RuntimeError("resposta inesperada ao consultar baseline D1")
    matches: dict[str, dict[str, Any]] = {}
    for item in result:
        if not isinstance(item, dict) or item.get("success") is not True:
            raise RuntimeError(f"consulta de baseline D1 falhou: {item!r}")
        rows_found = item.get("results")
        if not isinstance(rows_found, list):
            raise RuntimeError("consulta de baseline D1 retornou results inválido")
        for row in rows_found:
            if not isinstance(row, dict):
                raise RuntimeError("linha inválida no baseline D1")
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                raise RuntimeError(f"linha sem ID no baseline D1: {row!r}")
            previous = matches.get(model_id)
            if previous is not None and previous != row:
                raise RuntimeError(f"baseline D1 inconsistente para ID: {model_id}")
            matches[model_id] = row
    return list(matches.values())


def fetch_published_inventory(
    account_id: str,
    database_id: str,
    token: str,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    after_id = ""
    while True:
        statement = production_inventory_statement(after_id, page_size)
        body = request_batch_json(account_id, database_id, token, [statement])
        result = body.get("result")
        if not isinstance(result, list) or len(result) != 1:
            raise RuntimeError("resposta inesperada ao consultar inventário D1")
        item = result[0]
        if not isinstance(item, dict) or item.get("success") is not True:
            raise RuntimeError(f"consulta do inventário D1 falhou: {item!r}")
        page = item.get("results")
        if not isinstance(page, list):
            raise RuntimeError("inventário D1 retornou results inválido")
        if not page:
            break
        for row in page:
            if not isinstance(row, dict):
                raise RuntimeError("linha inválida no inventário D1")
            rows.append(row)
        last_id = str(page[-1].get("id") or "").strip()
        if not last_id or last_id <= after_id:
            raise RuntimeError("paginação do inventário D1 não avançou")
        after_id = last_id
        if len(page) < page_size:
            break
    return rows


def request_batch(account_id: str, database_id: str, token: str, batch: list[dict[str, Any]]) -> None:
    request_batch_json(account_id, database_id, token, batch)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"variável obrigatória ausente: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica models.jsonl no D1 de forma idempotente.")
    parser.add_argument("models", type=Path)
    parser.add_argument("--apply", action="store_true", help="executa mutações; sem esta flag apenas valida/planeja")
    parser.add_argument(
        "--check-production",
        action="store_true",
        help="consulta o D1 em modo somente leitura e valida identidade antes de qualquer upload/publicação",
    )
    parser.add_argument(
        "--legacy-gallery-config",
        type=Path,
        default=DEFAULT_LEGACY_GALLERY_CONFIG,
        help="SSOT auditado de consolidações legadas permitido no gate de identidade",
    )
    parser.add_argument(
        "--gallery-shrink-approvals",
        type=Path,
        help="CSV de aprovações explícitas para reduções de imageCount em modelos já publicados",
    )
    parser.add_argument(
        "--retire-absent-approvals",
        type=Path,
        help=(
            "CSV model_id,slug,code,reason; quando informado, exige inventário completo "
            "e aposenta somente fichas publicadas ausentes do snapshot com aprovação exata"
        ),
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--r2-state", type=Path, help="checkpoint R2; padrão: r2-publish-state.json ao lado de models.jsonl")
    args = parser.parse_args()

    if not args.models.is_file():
        parser.error(f"arquivo não encontrado: {args.models}")
    if args.batch_size < 1 or args.batch_size > 500:
        parser.error("--batch-size deve ficar entre 1 e 500")

    try:
        rows = load_models(args.models)
        numbered_review = validate_complete_numbered_review(rows)
        statements = build_statements(rows)
        summary: dict[str, Any] = {
            "models": len(rows),
            "categories": len({row["categorySlug"] for row in rows}),
            "franchises": len({(row["categorySlug"], row["franchiseSlug"]) for row in rows}),
            "folders": len(folder_entries(rows)),
            "statements": len(statements),
            "apply": args.apply,
            "destructiveDeletes": 0,
            "numberedSiblingReview": numbered_review,
        }
        if not args.apply and not args.check_production:
            print(json.dumps(summary, ensure_ascii=False))
            return 0

        account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
        database_id = require_env("CLOUDFLARE_D1_DATABASE_ID")
        token = require_env("CLOUDFLARE_API_TOKEN")
        production_rows = fetch_production_matches(account_id, database_id, token, rows)
        gallery_shrink_approvals = read_gallery_shrink_approvals(args.gallery_shrink_approvals)
        summary["productionCompatibility"] = validate_production_compatibility(
            rows,
            production_rows,
            gallery_shrink_approvals,
        )
        candidate_sources = candidate_image_sources(args.models, rows)
        production_sha_rows = fetch_production_sha_matches(
            account_id,
            database_id,
            token,
            candidate_sources,
        )
        summary["imageIdentityContinuity"] = validate_production_image_identity(
            candidate_sources,
            production_sha_rows,
            legacy_gallery_owner_moves(rows, args.legacy_gallery_config),
        )

        retirement_statements: list[dict[str, Any]] = []
        if args.retire_absent_approvals:
            retirement_approvals = read_model_retirement_approvals(args.retire_absent_approvals)
            production_inventory = fetch_published_inventory(account_id, database_id, token)
            retirement_summary, retirement_statements = validate_model_retirements(
                rows,
                production_inventory,
                retirement_approvals,
            )
            summary["retirementPlan"] = retirement_summary
            summary["destructiveDeletes"] = 0
            summary["softRetirements"] = len(retirement_statements)

        if not args.apply:
            print(json.dumps(summary, ensure_ascii=False))
            return 0

        state_path = args.r2_state.resolve() if args.r2_state else args.models.resolve().parent / "r2-publish-state.json"
        summary["r2Gate"] = validate_r2_ready(args.models, rows, state_path)
        image_source_statements = build_image_source_statements(args.models, rows)
        summary["imageSourceStatements"] = len(image_source_statements)

        batches = 0
        for batch in chunked(statements, args.batch_size):
            request_batch(account_id, database_id, token, batch)
            batches += 1
        for batch in chunked(image_source_statements, args.batch_size):
            request_batch(account_id, database_id, token, batch)
            batches += 1
        for batch in chunked(retirement_statements, args.batch_size):
            request_batch(account_id, database_id, token, batch)
            batches += 1
        summary["batches"] = batches
        sha_audit = request_batch_json(
            account_id,
            database_id,
            token,
            [exact_cross_model_sha_query()],
        )
        audit_result = sha_audit.get("result")
        audit_rows = (
            audit_result[0].get("results", [])
            if isinstance(audit_result, list) and audit_result and isinstance(audit_result[0], dict)
            else []
        )
        summary["crossModelExactImageCandidates"] = {
            "sampleLimit": 50,
            "groups": len(audit_rows),
            "samples": audit_rows,
            "automaticMerge": False,
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
