#!/usr/bin/env python3
"""Versioned SSOT for public model identity that already exists in production D1."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

DEFAULT_PRODUCTION_MODEL_CONTRACT = (
    Path(__file__).resolve().parents[1] / "config" / "catalog-production-model-contract.json"
)

NONEMPTY_STRING_FIELDS = (
    "id",
    "slug",
    "code",
    "displayName",
    "categoryName",
    "categorySlug",
    "franchiseName",
    "franchiseSlug",
)
STRING_FIELDS = (
    "variantName",
    "collection",
    "folderPathKey",
)
PUBLIC_METADATA_FIELDS = (
    "slug",
    "displayName",
    "variantName",
    "categoryName",
    "categorySlug",
    "franchiseName",
    "franchiseSlug",
    "collection",
    "folderPath",
    "folderPathKey",
)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-") or "pasta"


def load_production_model_contract(path: Path | None = None) -> dict:
    config_path = path or DEFAULT_PRODUCTION_MODEL_CONTRACT
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"contrato de produção inválido: {config_path}: {exc}") from exc

    if data.get("version") != 1:
        raise RuntimeError(f"versão de contrato de produção inválida: {config_path}")
    source = data.get("source")
    models = data.get("models")
    if not isinstance(source, dict) or not isinstance(models, list):
        raise RuntimeError(f"estrutura de contrato de produção inválida: {config_path}")

    by_id: dict[str, dict] = {}
    slugs: set[str] = set()
    codes: set[str] = set()
    published_count = 0
    for index, raw in enumerate(models, 1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"modelo {index} inválido no contrato de produção")
        entry: dict[str, object] = {}
        for field in NONEMPTY_STRING_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(f"{field} inválido no modelo {index} do contrato de produção")
            entry[field] = value.strip()
        for field in STRING_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str):
                raise RuntimeError(f"{field} inválido no modelo {index} do contrato de produção")
            entry[field] = value

        folder_path = raw.get("folderPath")
        if not isinstance(folder_path, list) or any(
            not isinstance(item, str) or not item.strip() for item in folder_path
        ):
            raise RuntimeError(f"folderPath inválido no modelo {index} do contrato de produção")
        normalized_folder_path = [item.strip() for item in folder_path]
        expected_folder_key = "/".join(slugify(item) for item in normalized_folder_path)
        if str(entry["folderPathKey"]) != expected_folder_key:
            raise RuntimeError(
                f"folderPathKey divergente no modelo {index} do contrato de produção: "
                f"{entry['folderPathKey']!r} != {expected_folder_key!r}"
            )
        entry["folderPath"] = normalized_folder_path

        published = raw.get("published")
        if published not in (0, 1, False, True):
            raise RuntimeError(f"published inválido no modelo {index} do contrato de produção")
        entry["published"] = bool(published)

        model_id = str(entry["id"])
        slug = str(entry["slug"])
        code = str(entry["code"])
        if model_id in by_id:
            raise RuntimeError(f"ID duplicado no contrato de produção: {model_id}")
        if slug in slugs:
            raise RuntimeError(f"slug duplicado no contrato de produção: {slug}")
        if code in codes:
            raise RuntimeError(f"código duplicado no contrato de produção: {code}")
        by_id[model_id] = entry
        slugs.add(slug)
        codes.add(code)
        published_count += int(bool(published))

    expected_total = source.get("productionModels")
    expected_published = source.get("publishedModels")
    if expected_total != len(by_id):
        raise RuntimeError(
            f"contagem do contrato de produção divergiu: source={expected_total!r}, models={len(by_id)}"
        )
    if expected_published != published_count:
        raise RuntimeError(
            "contagem published do contrato de produção divergiu: "
            f"source={expected_published!r}, models={published_count}"
        )

    return {
        "version": 1,
        "source": source,
        "byId": by_id,
        "slugOwners": {str(entry["slug"]): model_id for model_id, entry in by_id.items()},
    }


def apply_production_model_contract(
    metadata: dict,
    contract_by_id: dict[str, dict],
    *,
    allow_retired: bool = False,
) -> dict:
    model_id = str(metadata.get("id") or "")
    entry = contract_by_id.get(model_id)
    if entry is None:
        return metadata

    generated_code = str(metadata.get("code") or "")
    if generated_code != str(entry["code"]):
        raise RuntimeError(
            "contrato de produção encontrou code incompatível para o mesmo ID: "
            f"{model_id}: produção={entry['code']!r}, candidato={generated_code!r}"
        )
    if not bool(entry.get("published")) and not allow_retired:
        raise RuntimeError(
            "identidade histórica aposentada reapareceu no bundle; exige migração explícita: "
            f"{model_id} / {entry.get('slug')}"
        )

    reconciled = dict(metadata)
    for field in PUBLIC_METADATA_FIELDS:
        value = entry[field]
        reconciled[field] = list(value) if field == "folderPath" else value

    search_text = str(reconciled.get("searchText") or "").strip()
    historical_terms = [
        str(entry[field]).strip()
        for field in (
            "displayName",
            "variantName",
            "categoryName",
            "franchiseName",
            "collection",
        )
        if str(entry[field]).strip()
    ]
    historical_terms.extend(str(item).strip() for item in entry["folderPath"] if str(item).strip())
    combined = " ".join([search_text, *historical_terms]).strip().casefold()
    reconciled["searchText"] = " ".join(dict.fromkeys(combined.split()))
    return reconciled


def production_contract_fingerprint_entry(contract_by_id: dict[str, dict], model_id: str) -> dict | None:
    entry = contract_by_id.get(model_id)
    if entry is None:
        return None
    return {
        field: (list(entry[field]) if field == "folderPath" else entry[field])
        for field in (
            "id",
            "slug",
            "code",
            "displayName",
            "variantName",
            "categoryName",
            "categorySlug",
            "franchiseName",
            "franchiseSlug",
            "collection",
            "folderPath",
            "folderPathKey",
            "published",
        )
    }
