from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_LEGACY_GALLERY_CONFIG = (
    Path(__file__).resolve().parents[1] / "config" / "catalog-legacy-gallery-overrides.json"
)


def load_legacy_gallery_registry(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_LEGACY_GALLERY_CONFIG
    if not config_path.is_file():
        raise RuntimeError(f"registro de reparos legados não encontrado: {config_path}")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"registro de reparos legados inválido: {config_path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise RuntimeError(f"versão do registro de reparos legados não suportada: {config_path}")
    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        raise RuntimeError(f"registro de reparos legados vazio: {config_path}")

    audited_at = str(payload.get("auditedAt") or "").strip()
    audited_groups = payload.get("auditedGroups")
    audited_member_cards = payload.get("auditedMemberCards")
    audited_extra_cards = payload.get("auditedExtraCards")
    if not audited_at:
        raise RuntimeError(f"registro de reparos legados sem auditedAt: {config_path}")
    for label, value in (
        ("auditedGroups", audited_groups),
        ("auditedMemberCards", audited_member_cards),
        ("auditedExtraCards", audited_extra_cards),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RuntimeError(f"{label} inválido no registro de reparos legados: {config_path}")

    seen_members: set[str] = set()
    normalized_groups: list[dict[str, Any]] = []
    member_cards = 0
    for index, raw_group in enumerate(groups, 1):
        if not isinstance(raw_group, dict):
            raise RuntimeError(f"grupo de reparo inválido na posição {index}: {config_path}")
        group = dict(raw_group)
        required = (
            "categorySlug", "franchiseSlug", "folderPathKey", "family",
            "canonicalSlug", "memberSlugs", "reason",
        )
        normalized: dict[str, str] = {}
        for field in required[:-2]:
            value = str(group.get(field) or "").strip()
            if not value:
                raise RuntimeError(f"{field} vazio no grupo {index}: {config_path}")
            normalized[field] = value
        reason = str(group.get("reason") or "").strip()
        if not reason:
            raise RuntimeError(f"reason vazio no grupo {index}: {config_path}")
        members = group.get("memberSlugs")
        if not isinstance(members, list) or len(members) < 2:
            raise RuntimeError(f"grupo de reparo sem membros suficientes: {group.get('family')}")
        normalized_members = [str(value or "").strip() for value in members]
        if any(not value for value in normalized_members):
            raise RuntimeError(f"memberSlugs vazio no grupo {index}: {config_path}")
        if len(set(normalized_members)) != len(normalized_members):
            raise RuntimeError(f"membro repetido no grupo: {normalized['family']}")
        if normalized["canonicalSlug"] not in normalized_members:
            raise RuntimeError(f"canônico fora dos membros: {normalized['family']}")
        match_mode = str(group.get("matchMode") or "descriptor-family").strip()
        if match_mode not in {"descriptor-family", "explicit-members"}:
            raise RuntimeError(f"matchMode inválido no grupo {normalized['family']}: {match_mode}")
        overlap = seen_members.intersection(normalized_members)
        if overlap:
            raise RuntimeError(f"slug aparece em múltiplos grupos: {sorted(overlap)[:5]}")
        seen_members.update(normalized_members)
        member_cards += len(normalized_members)
        normalized_groups.append({
            **group,
            **normalized,
            "memberSlugs": normalized_members,
            "reason": reason,
            "matchMode": match_mode,
        })

    extra_cards = member_cards - len(normalized_groups)
    actual = {
        "auditedGroups": len(normalized_groups),
        "auditedMemberCards": member_cards,
        "auditedExtraCards": extra_cards,
    }
    expected = {
        "auditedGroups": audited_groups,
        "auditedMemberCards": audited_member_cards,
        "auditedExtraCards": audited_extra_cards,
    }
    if actual != expected:
        raise RuntimeError(
            "resumo auditado dos reparos legados diverge do registro: "
            f"atual={actual} esperado={expected}"
        )

    return {**payload, "groups": normalized_groups}
