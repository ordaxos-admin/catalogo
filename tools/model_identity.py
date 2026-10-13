from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

DEFAULT_VIEW_CONFIG = Path(__file__).resolve().parents[1] / "config" / "catalog-view-descriptors.json"
DEFAULT_IDENTITY_ALIAS_CONFIG = Path(__file__).resolve().parents[1] / "config" / "catalog-model-identity-aliases.json"


def _load_identity_config(path: Path | None = None) -> tuple[Path, dict]:
    config_path = path or DEFAULT_IDENTITY_ALIAS_CONFIG
    if not config_path.is_file():
        return config_path, {"version": 1, "aliases": [], "comparisonCollisionGroups": []}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON de aliases de identidade inválido: {config_path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise RuntimeError(f"configuração de aliases de identidade inválida: {config_path}")
    return config_path, data


def load_identity_aliases(path: Path | None = None) -> dict[str, str]:
    config_path, data = _load_identity_config(path)
    raw = data.get("aliases")
    if not isinstance(raw, list):
        raise RuntimeError(f"aliases de identidade inválidos: {config_path}")

    alias_to_canonical: dict[str, str] = {}
    canonical_keys: set[str] = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise RuntimeError(f"alias de identidade inválido na posição {index}: {config_path}")
        canonical = str(item.get("canonicalIdentityKey") or "").strip()
        aliases = item.get("aliases")
        reason = str(item.get("reason") or "").strip()
        if not canonical or not reason or not isinstance(aliases, list) or not aliases:
            raise RuntimeError(f"alias de identidade incompleto na posição {index}: {config_path}")
        if canonical in canonical_keys:
            raise RuntimeError(f"identidade canônica repetida: {canonical}")
        canonical_keys.add(canonical)

        local: set[str] = set()
        for raw_alias in aliases:
            alias = str(raw_alias or "").strip()
            if not alias or alias == canonical:
                raise RuntimeError(f"alias de identidade inválido para {canonical}: {alias!r}")
            if alias in local:
                raise RuntimeError(f"alias repetido para {canonical}: {alias}")
            local.add(alias)
            previous = alias_to_canonical.get(alias)
            if previous is not None and previous != canonical:
                raise RuntimeError(
                    f"alias de identidade pertence a mais de um canônico: {alias}: "
                    f"{previous} | {canonical}"
                )
            alias_to_canonical[alias] = canonical

    for canonical in canonical_keys:
        owner = alias_to_canonical.get(canonical)
        if owner is not None:
            raise RuntimeError(
                f"identidade canônica também aparece como alias de outro produto: "
                f"{canonical} -> {owner}"
            )
    return alias_to_canonical


def canonical_identity_key(identity_key: str, aliases: dict[str, str]) -> str:
    key = str(identity_key).strip()
    if not key:
        raise RuntimeError("identityKey vazia")
    return aliases.get(key, key)


@dataclass(frozen=True)
class ViewDescriptor:
    stem_suffix: str
    slug_suffix: str
    priority: int
    kind: str


def load_view_descriptors(path: Path | None = None) -> tuple[ViewDescriptor, ...]:
    config_path = path or DEFAULT_VIEW_CONFIG
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"configuração de vistas ausente: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON de vistas inválido: {config_path}: {exc}") from exc

    raw = data.get("descriptors") if isinstance(data, dict) else None
    if data.get("version") != 1 or not isinstance(raw, list) or not raw:
        raise RuntimeError(f"configuração de vistas inválida: {config_path}")

    descriptors: list[ViewDescriptor] = []
    seen_stems: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError(f"descritor de vista inválido: {config_path}")
        stem_suffix = str(item.get("stemSuffix") or "").strip().casefold()
        slug_suffix = str(item.get("slugSuffix") or "").strip().casefold()
        kind = str(item.get("class") or "").strip()
        priority = item.get("priority")
        if (
            not stem_suffix
            or not slug_suffix.startswith("-")
            or not isinstance(priority, int)
            or priority < 0
            or kind not in {"directional", "framing"}
        ):
            raise RuntimeError(f"descritor de vista inválido: {item!r}")
        if stem_suffix in seen_stems:
            raise RuntimeError(f"descritor de vista duplicado: {stem_suffix}")
        seen_stems.add(stem_suffix)
        descriptors.append(ViewDescriptor(stem_suffix, slug_suffix, priority, kind))

    return tuple(sorted(descriptors, key=lambda item: (-len(item.stem_suffix), item.priority, item.stem_suffix)))


VIEW_DESCRIPTORS = load_view_descriptors()
VIEW_ANCHOR_PRIORITY = {item.stem_suffix: item.priority for item in VIEW_DESCRIPTORS}


def stem_view_descriptor(source_stem: str) -> tuple[str, ViewDescriptor] | None:
    normalized = re.sub(r"[-_]+", " ", source_stem).strip()
    lowered = normalized.casefold()
    for descriptor in VIEW_DESCRIPTORS:
        marker = f" {descriptor.stem_suffix}"
        if lowered.endswith(marker):
            base = normalized[: -len(marker)].strip(" -_")
            return (base, descriptor) if base else None
    return None


def slug_view_descriptor(slug: str) -> tuple[str, ViewDescriptor] | None:
    normalized = slug.strip().casefold()
    for descriptor in VIEW_DESCRIPTORS:
        if normalized.endswith(descriptor.slug_suffix):
            base = normalized[: -len(descriptor.slug_suffix)].strip("-")
            return (base, descriptor) if base else None
    return None


def comparison_identity_key(value: str) -> str:
    """Canonical comparison key that never replaces the persisted identity."""
    normalized = unicodedata.normalize("NFKC", value)
    parts = re.split(r"\s*/\s*", normalized)
    return " / ".join(
        re.sub(r"\s+", " ", part.strip()).casefold()
        for part in parts
    )


def load_identity_comparison_collision_groups(
    path: Path | None = None,
) -> tuple[frozenset[str], ...]:
    config_path, data = _load_identity_config(path)
    # Reuse the canonical alias validator so collision groups cannot bypass alias invariants.
    load_identity_aliases(path)
    raw_aliases = data.get("aliases")
    if not isinstance(raw_aliases, list):
        raise RuntimeError(f"aliases de identidade inválidos: {config_path}")
    canonical_keys = {
        str(item.get("canonicalIdentityKey") or "").strip()
        for item in raw_aliases
        if isinstance(item, dict)
    }
    canonical_keys.discard("")

    raw_groups = data.get("comparisonCollisionGroups", [])
    if not isinstance(raw_groups, list):
        raise RuntimeError(f"comparisonCollisionGroups inválido: {config_path}")

    groups: list[frozenset[str]] = []
    seen_canonicals: set[str] = set()
    for index, item in enumerate(raw_groups, 1):
        if not isinstance(item, dict):
            raise RuntimeError(f"grupo de colisão textual inválido na posição {index}: {config_path}")
        keys = item.get("canonicalIdentityKeys")
        reason = str(item.get("reason") or "").strip()
        if not isinstance(keys, list) or len(keys) < 2 or not reason:
            raise RuntimeError(f"grupo de colisão textual incompleto na posição {index}: {config_path}")
        normalized = [str(value or "").strip() for value in keys]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise RuntimeError(f"grupo de colisão textual possui chaves vazias/duplicadas: {config_path}")
        missing = sorted(set(normalized) - canonical_keys)
        if missing:
            raise RuntimeError(
                f"grupo de colisão textual referencia canônico não registrado: {missing[0]}"
            )
        comparison_keys = {comparison_identity_key(value) for value in normalized}
        if len(comparison_keys) != 1:
            raise RuntimeError(
                "grupo de colisão textual deve conter apenas identidades textualmente equivalentes: "
                + " | ".join(normalized)
            )
        reused = sorted(set(normalized) & seen_canonicals)
        if reused:
            raise RuntimeError(
                f"identidade canônica aparece em mais de um grupo de colisão textual: {reused[0]}"
            )
        seen_canonicals.update(normalized)
        groups.append(frozenset(normalized))
    return tuple(groups)


def validate_identity_comparison_collisions(
    identity_keys: Iterable[str],
    allowed_collision_groups: Iterable[frozenset[str]] | None = None,
) -> None:
    by_comparison: dict[str, set[str]] = {}
    for raw in identity_keys:
        identity = str(raw)
        by_comparison.setdefault(comparison_identity_key(identity), set()).add(identity)

    allowed = set(
        allowed_collision_groups
        if allowed_collision_groups is not None
        else load_identity_comparison_collision_groups()
    )
    collisions = [
        sorted(values, key=str.casefold)
        for values in by_comparison.values()
        if len(values) > 1 and frozenset(values) not in allowed
    ]
    if collisions:
        sample = " | ".join(collisions[0][:3])
        raise RuntimeError(
            "identidades públicas textualmente equivalentes gerariam IDs distintos; "
            f"normalize/revise o registro antes de publicar: {sample}"
        )

def taxonomy_name_comparison_key(value: str) -> str:
    """Normalize typography while preserving semantic distinctions such as accents."""
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    cleaned = "".join(
        " " if char.isspace() or char == "_" or unicodedata.category(char)[0] in {"P", "S"} else char
        for char in normalized
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def validate_taxonomy_slug_mappings(entries: Iterable[Mapping[str, object]]) -> None:
    """Reject one public slug mapping to genuinely different canonical names.

    Punctuation, whitespace and case variants are accepted because they are
    typographic. Accents/letters remain significant so a lossy slugification
    cannot silently merge two distinct taxonomy labels.
    """
    category_names: dict[str, str] = {}
    franchise_names: dict[tuple[str, str], str] = {}

    for entry in entries:
        category_slug = str(entry.get("categorySlug") or "").strip()
        category_name = str(entry.get("categoryName") or "").strip()
        franchise_slug = str(entry.get("franchiseSlug") or "").strip()
        franchise_name = str(entry.get("franchiseName") or "").strip()
        if not all((category_slug, category_name, franchise_slug, franchise_name)):
            raise RuntimeError(f"taxonomia incompleta no modelo: {dict(entry)!r}")

        previous_category = category_names.get(category_slug)
        if (
            previous_category is not None
            and taxonomy_name_comparison_key(previous_category) != taxonomy_name_comparison_key(category_name)
        ):
            raise RuntimeError(
                "slug de categoria representa nomes canônicos diferentes: "
                f"{category_slug}: {previous_category!r} | {category_name!r}"
            )
        category_names.setdefault(category_slug, category_name)

        franchise_key = (category_slug, franchise_slug)
        previous_franchise = franchise_names.get(franchise_key)
        if (
            previous_franchise is not None
            and taxonomy_name_comparison_key(previous_franchise) != taxonomy_name_comparison_key(franchise_name)
        ):
            raise RuntimeError(
                "slug de franquia representa nomes canônicos diferentes: "
                f"{category_slug}/{franchise_slug}: "
                f"{previous_franchise!r} | {franchise_name!r}"
            )
        franchise_names.setdefault(franchise_key, franchise_name)


def candidate_confidence(descriptors: Iterable[ViewDescriptor]) -> str:
    items = tuple(descriptors)
    directional = {item.stem_suffix for item in items if item.kind == "directional"}
    return "high" if len(directional) >= 2 else "review"
