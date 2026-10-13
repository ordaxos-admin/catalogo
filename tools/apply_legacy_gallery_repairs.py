#!/usr/bin/env python3
"""Plan/apply reviewed legacy gallery repairs against production D1.

The operation is intentionally monotonic:
- inserting canonical -> source is the only write;
- database triggers retire the source card in the same statement;
- no DELETE is issued;
- partial progress is safe to rerun because already-correct relations are skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from legacy_gallery_registry import load_legacy_gallery_registry

from catalog_schema_contract import (
    load_schema_contract,
    schema_migrations_statement,
    schema_structure_statement,
    validate_applied_migrations,
    validate_schema_structures,
)


def load_config(path: Path) -> list[dict[str, Any]]:
    return load_legacy_gallery_registry(path)["groups"]


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"variável obrigatória ausente: {name}")
    return value


def d1_request(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not statements:
        return []
    account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
    database_id = require_env("CLOUDFLARE_D1_DATABASE_ID")
    token = require_env("CLOUDFLARE_API_TOKEN")
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/d1/database/{database_id}/query"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps({"batch": statements}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "tonecos-catalog-legacy-gallery-repair/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"D1 HTTP {exc.code}: {detail}") from exc
    if body.get("success") is not True:
        raise RuntimeError(f"D1 recusou operação: {body.get('errors')}")
    result = body.get("result")
    if not isinstance(result, list):
        raise RuntimeError("resposta D1 sem lista result")
    for item in result:
        if not isinstance(item, dict) or item.get("success") is not True:
            raise RuntimeError(f"statement D1 falhou: {item!r}")
    return result


def _chunks(values: list[str], size: int = 50):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def require_schema_ready() -> dict[str, Any]:
    contract = load_schema_contract()
    result = d1_request([
        schema_migrations_statement(contract),
        schema_structure_statement(contract),
    ])
    if len(result) != 2:
        raise RuntimeError("preflight de schema retornou quantidade inesperada de resultados")
    migration_rows = result[0].get("results")
    structure_rows = result[1].get("results")
    if not isinstance(migration_rows, list) or not isinstance(structure_rows, list):
        raise RuntimeError("preflight de schema retornou results inválido")
    migrations = validate_applied_migrations(
        contract,
        [row for row in migration_rows if isinstance(row, dict)],
    )
    structures = validate_schema_structures(
        contract,
        [row for row in structure_rows if isinstance(row, dict)],
    )
    return {**migrations, **structures}


def fetch_state(groups: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    slugs = sorted({slug for group in groups for slug in group["memberSlugs"]})
    model_rows: list[dict[str, Any]] = []
    relation_rows: list[dict[str, Any]] = []

    for chunk in _chunks(slugs):
        placeholders = ",".join("?" for _ in chunk)
        result = d1_request([{
            "sql": f"""SELECT
m.id,m.slug,m.code,m.name,m.published,m.image_count,m.gallery_version,
m.gallery_manifest_key,m.cover_storage_key,
c.slug AS category_slug,f.slug AS franchise_slug,COALESCE(cf.path,'') AS folder_path
FROM models m
JOIN franchises f ON f.id=m.franchise_id
JOIN categories c ON c.id=f.category_id
LEFT JOIN catalog_folders cf ON cf.id=m.folder_id
WHERE m.slug IN ({placeholders})
ORDER BY m.slug""",
            "params": chunk,
        }])
        rows = result[0].get("results")
        if not isinstance(rows, list):
            raise RuntimeError("consulta de modelos legados retornou results inválido")
        model_rows.extend(row for row in rows if isinstance(row, dict))

        result = d1_request([{
            "sql": f"""SELECT
canonical.slug AS canonical_slug,
source.slug AS source_slug,
member.position
FROM model_gallery_members member
JOIN models canonical ON canonical.id=member.canonical_model_id
JOIN models source ON source.id=member.source_model_id
WHERE canonical.slug IN ({placeholders})
   OR source.slug IN ({placeholders})
ORDER BY canonical.slug,member.position,source.slug""",
            "params": [*chunk, *chunk],
        }])
        rows = result[0].get("results")
        if not isinstance(rows, list):
            raise RuntimeError("consulta de relações legadas retornou results inválido")
        relation_rows.extend(row for row in rows if isinstance(row, dict))

    unique_relations = {
        (str(row.get("canonical_slug")), str(row.get("source_slug"))): row
        for row in relation_rows
    }
    return model_rows, list(unique_relations.values())


def fetch_approval_state(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slugs = sorted({slug for group in groups for slug in group["memberSlugs"]})
    rows: list[dict[str, Any]] = []
    for chunk in _chunks(slugs):
        placeholders = ",".join("?" for _ in chunk)
        result = d1_request([{
            "sql": f"""SELECT
canonical.slug AS canonical_slug,
source.slug AS source_slug,
approval.registry_family,
approval.reason
FROM model_gallery_member_approvals approval
JOIN models canonical ON canonical.id=approval.canonical_model_id
JOIN models source ON source.id=approval.source_model_id
WHERE canonical.slug IN ({placeholders})
   OR source.slug IN ({placeholders})
ORDER BY canonical.slug,source.slug""",
            "params": [*chunk, *chunk],
        }])
        page = result[0].get("results")
        if not isinstance(page, list):
            raise RuntimeError("consulta de aprovações de galeria retornou results inválido")
        rows.extend(row for row in page if isinstance(row, dict))
    unique = {str(row.get("source_slug") or ""): row for row in rows}
    return list(unique.values())


def fetch_image_source_state(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slugs = sorted({slug for group in groups for slug in group["memberSlugs"]})
    rows: list[dict[str, Any]] = []
    for chunk in _chunks(slugs):
        placeholders = ",".join("?" for _ in chunk)
        result = d1_request([{
            "sql": f"""SELECT
m.slug,m.image_count,m.gallery_version,s.image_id,s.source_sha256
FROM models m
LEFT JOIN model_image_sources s
  ON s.model_id=m.id
 AND s.gallery_version=m.gallery_version
WHERE m.slug IN ({placeholders})
ORDER BY m.slug,s.position,s.image_id""",
            "params": chunk,
        }])
        page = result[0].get("results")
        if not isinstance(page, list):
            raise RuntimeError("consulta do índice SHA retornou results inválido")
        rows.extend(row for row in page if isinstance(row, dict))
    return rows


def verify_repair_image_sources(
    groups: list[dict[str, Any]],
    model_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    models = {str(row.get("slug") or ""): row for row in model_rows}
    by_slug: dict[str, list[str]] = {}
    for row in source_rows:
        slug = str(row.get("slug") or "").strip()
        sha = str(row.get("source_sha256") or "").strip().lower()
        if not slug:
            continue
        if not sha:
            by_slug.setdefault(slug, [])
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise RuntimeError(f"SHA-256 inválido no índice de imagem: {slug}")
        by_slug.setdefault(slug, []).append(sha)

    expected_slugs = {slug for group in groups for slug in group["memberSlugs"]}
    missing_models = sorted(expected_slugs.difference(models))
    if missing_models:
        raise RuntimeError(f"modelos ausentes no preflight SHA: {missing_models[:5]}")

    for slug in sorted(expected_slugs):
        expected_count = int(models[slug].get("image_count") or 0)
        shas = by_slug.get(slug, [])
        if len(shas) != expected_count:
            raise RuntimeError(
                f"índice SHA incompleto para {slug}: {len(shas)} != {expected_count}; "
                "execute o backfill antes de aplicar reparos"
            )
        if len(set(shas)) != len(shas):
            raise RuntimeError(f"SHA repetido dentro da fonte legada: {slug}")

    checked_images = 0
    for group in groups:
        seen: dict[str, str] = {}
        for slug in group["memberSlugs"]:
            for sha in by_slug.get(slug, []):
                previous = seen.get(sha)
                if previous is not None and previous != slug:
                    raise RuntimeError(
                        "imagem exata repetida entre fontes da mesma galeria: "
                        f"{group['family']}: {previous} | {slug}"
                    )
                seen[sha] = slug
                checked_images += 1

    return {
        "ready": True,
        "models": len(expected_slugs),
        "images": checked_images,
        "duplicateShaWithinRepairGroups": 0,
    }


def plan_repairs(
    groups: list[dict[str, Any]],
    model_rows: list[dict[str, Any]],
    relation_rows: list[dict[str, Any]],
    approval_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    models = {str(row.get("slug") or ""): row for row in model_rows}
    relations_by_source = {
        str(row.get("source_slug") or ""): row
        for row in relation_rows
    }
    approvals_by_source = {
        str(row.get("source_slug") or ""): row
        for row in (approval_rows or [])
    }
    expected_members = {slug for group in groups for slug in group["memberSlugs"]}
    if set(models) != expected_members:
        missing = sorted(expected_members.difference(models))
        extra = sorted(set(models).difference(expected_members))
        raise RuntimeError(f"estado D1 divergente do registro: ausentes={missing[:5]} extras={extra[:5]}")

    statements: list[dict[str, Any]] = []
    existing_relations = 0
    pending_relations = 0
    existing_approvals = 0
    pending_approvals = 0
    sources_to_retire = 0
    expected_relations: list[dict[str, Any]] = []
    expected_approval_sources: set[str] = set()

    for group in groups:
        members = [models[slug] for slug in group["memberSlugs"]]
        canonical_slug = str(group["canonicalSlug"])
        canonical = models[canonical_slug]

        scopes = {
            (
                str(row.get("category_slug") or ""),
                str(row.get("franchise_slug") or ""),
                str(row.get("folder_path") or ""),
            )
            for row in members
        }
        expected_scope = (
            str(group["categorySlug"]),
            str(group["franchiseSlug"]),
            str(group["folderPathKey"]),
        )
        if len(scopes) != 1:
            raise RuntimeError(f"membros divergem de escopo: {group['family']}")
        scope = next(iter(scopes))
        if scope != expected_scope:
            raise RuntimeError(
                f"escopo D1 diverge do registro {group['family']}: {scope} != {expected_scope}"
            )
        if int(canonical.get("published") or 0) != 1:
            raise RuntimeError(f"modelo canônico não está publicado: {canonical_slug}")

        for row in members:
            slug = str(row["slug"])
            if int(row.get("image_count") or 0) != 1:
                raise RuntimeError(f"ficha legada deixou de ter image_count=1: {slug}")
            if int(row.get("gallery_version") or 0) < 1:
                raise RuntimeError(f"gallery_version inválida: {slug}")
            if not str(row.get("gallery_manifest_key") or "").strip():
                raise RuntimeError(f"manifesto ausente: {slug}")
            if not str(row.get("cover_storage_key") or "").strip():
                raise RuntimeError(f"capa ausente: {slug}")

        canonical_name = str(canonical.get("name") or "").strip().casefold()
        source_slugs = [slug for slug in group["memberSlugs"] if slug != canonical_slug]
        for position, source_slug in enumerate(source_slugs, 1):
            source = models[source_slug]
            relation = relations_by_source.get(source_slug)
            approval = approvals_by_source.get(source_slug)
            requires_approval = str(source.get("name") or "").strip().casefold() != canonical_name
            expected_relations.append({
                "canonicalSlug": canonical_slug,
                "sourceSlug": source_slug,
                "position": position,
            })

            if approval is not None:
                if str(approval.get("canonical_slug") or "") != canonical_slug:
                    raise RuntimeError(
                        f"aprovação aponta para outro canônico: {source_slug} -> "
                        f"{approval.get('canonical_slug')}"
                    )
                if str(approval.get("registry_family") or "") != str(group["family"]):
                    raise RuntimeError(f"aprovação diverge da família auditada: {source_slug}")
                if str(approval.get("reason") or "") != str(group["reason"]):
                    raise RuntimeError(f"aprovação diverge da razão auditada: {source_slug}")
                existing_approvals += 1
                expected_approval_sources.add(source_slug)

            if relation is not None:
                if str(relation.get("canonical_slug")) != canonical_slug:
                    raise RuntimeError(
                        f"fonte já pertence a outro canônico: {source_slug} -> "
                        f"{relation.get('canonical_slug')}"
                    )
                if int(relation.get("position") or -1) != position:
                    raise RuntimeError(
                        f"posição existente diverge do registro: {source_slug}: "
                        f"{relation.get('position')} != {position}"
                    )
                if int(source.get("published") or 0) != 0:
                    raise RuntimeError(f"fonte anexada continua publicada: {source_slug}")
                if requires_approval and approval is None:
                    raise RuntimeError(
                        f"relação com nome público divergente não possui aprovação: {source_slug}"
                    )
                existing_relations += 1
                continue

            if int(source.get("published") or 0) != 1:
                raise RuntimeError(
                    f"fonte não publicada sem relação canônica registrada: {source_slug}"
                )

            if requires_approval and approval is None:
                pending_approvals += 1
                expected_approval_sources.add(source_slug)
                statements.append({
                    "sql": """INSERT INTO model_gallery_member_approvals(
source_model_id,canonical_model_id,registry_family,reason)
SELECT source.id,canonical.id,?,?
FROM models canonical
JOIN models source ON source.slug=?
WHERE canonical.slug=?
  AND NOT EXISTS (
    SELECT 1 FROM model_gallery_member_approvals approval
    WHERE approval.source_model_id=source.id
  )""",
                    "params": [str(group["family"]), str(group["reason"]), source_slug, canonical_slug],
                })

            sources_to_retire += 1
            pending_relations += 1
            statements.append({
                "sql": """INSERT INTO model_gallery_members(
canonical_model_id,source_model_id,position)
SELECT canonical.id,source.id,?
FROM models canonical
JOIN models source ON source.slug=?
WHERE canonical.slug=?""",
                "params": [position, source_slug, canonical_slug],
            })

    expected_sources = {item["sourceSlug"] for item in expected_relations}
    unexpected_relations = [
        row for row in relation_rows
        if str(row.get("source_slug") or "") in expected_members
        and str(row.get("source_slug") or "") not in expected_sources
    ]
    if unexpected_relations:
        raise RuntimeError(
            f"relações inesperadas envolvendo o registro legado: {unexpected_relations[:3]}"
        )
    unexpected_approvals = [
        row for row in (approval_rows or [])
        if str(row.get("source_slug") or "") in expected_members
        and str(row.get("source_slug") or "") not in expected_approval_sources
    ]
    if unexpected_approvals:
        raise RuntimeError(
            f"aprovações inesperadas envolvendo o registro legado: {unexpected_approvals[:3]}"
        )

    return {
        "ready": True,
        "groups": len(groups),
        "memberCards": len(expected_members),
        "expectedRelations": len(expected_relations),
        "existingRelations": existing_relations,
        "pendingRelations": pending_relations,
        "existingApprovals": existing_approvals,
        "pendingApprovals": pending_approvals,
        "sourcesToRetire": sources_to_retire,
        "destructiveDeletes": 0,
        "statements": statements,
        "expected": expected_relations,
    }


def apply_statement_batches(
    statements: list[dict[str, Any]],
    batch_size: int = 100,
) -> int:
    if batch_size < 1 or batch_size > 250:
        raise ValueError("batch_size deve ficar entre 1 e 250")
    batches = 0
    for start in range(0, len(statements), batch_size):
        d1_request(statements[start:start + batch_size])
        batches += 1
    return batches


def published_model_count() -> int:
    result = d1_request([{
        "sql": "SELECT COUNT(*) AS total FROM models WHERE published=1",
        "params": [],
    }])
    rows = result[0].get("results")
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError("contagem de modelos públicos retornou formato inválido")
    return int(rows[0].get("total") or 0)


def count_integrity_statements() -> list[dict[str, Any]]:
    return [
        {
            "name": "categories",
            "sql": """SELECT COUNT(*) AS mismatches
FROM categories c
WHERE c.model_count != (
  SELECT COUNT(*)
  FROM models m
  JOIN franchises f ON f.id=m.franchise_id
  WHERE m.published=1 AND f.category_id=c.id
)""",
            "params": [],
        },
        {
            "name": "franchises",
            "sql": """SELECT COUNT(*) AS mismatches
FROM franchises f
WHERE f.model_count != (
  SELECT COUNT(*)
  FROM models m
  WHERE m.published=1 AND m.franchise_id=f.id
)""",
            "params": [],
        },
        {
            "name": "folders_direct",
            "sql": """SELECT COUNT(*) AS mismatches
FROM catalog_folders cf
WHERE cf.direct_model_count != (
  SELECT COUNT(*)
  FROM models m
  WHERE m.published=1 AND m.folder_id=cf.id
)""",
            "params": [],
        },
        {
            "name": "folders_subtree",
            "sql": """WITH RECURSIVE tree(root_id,id) AS (
  SELECT id,id FROM catalog_folders
  UNION ALL
  SELECT tree.root_id,child.id
  FROM tree
  JOIN catalog_folders child ON child.parent_id=tree.id
),
actual AS (
  SELECT tree.root_id,COUNT(m.id) AS model_count
  FROM tree
  LEFT JOIN models m ON m.folder_id=tree.id AND m.published=1
  GROUP BY tree.root_id
)
SELECT COUNT(*) AS mismatches
FROM catalog_folders cf
LEFT JOIN actual ON actual.root_id=cf.id
WHERE cf.subtree_model_count != COALESCE(actual.model_count,0)""",
            "params": [],
        },
    ]


def verify_materialized_counts() -> dict[str, Any]:
    specs = count_integrity_statements()
    result = d1_request([
        {"sql": item["sql"], "params": item["params"]}
        for item in specs
    ])
    if len(result) != len(specs):
        raise RuntimeError("verificação de contadores retornou quantidade inesperada de resultados")
    summary: dict[str, int] = {}
    for spec, item in zip(specs, result):
        rows = item.get("results")
        if not isinstance(rows, list) or len(rows) != 1:
            raise RuntimeError(f"verificação de contador inválida: {spec['name']}")
        mismatches = int(rows[0].get("mismatches") or 0)
        summary[str(spec["name"])] = mismatches
        if mismatches:
            raise RuntimeError(
                f"contador materializado divergente após reparo: {spec['name']}={mismatches}"
            )
    return {
        "ready": True,
        "mismatches": summary,
    }


def verify_applied(
    groups: list[dict[str, Any]],
    model_rows: list[dict[str, Any]],
    relation_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    plan = plan_repairs(groups, model_rows, relation_rows)
    if plan["pendingRelations"] != 0:
        raise RuntimeError(f"reparo incompleto após aplicação: {plan['pendingRelations']} relação(ões) pendente(s)")
    expected_canonicals = {group["canonicalSlug"] for group in groups}
    models = {str(row["slug"]): row for row in model_rows}
    for group in groups:
        for slug in group["memberSlugs"]:
            expected = 1 if slug in expected_canonicals else 0
            if int(models[slug].get("published") or 0) != expected:
                raise RuntimeError(f"estado published pós-reparo inválido: {slug}")
    return {
        "ready": True,
        "groups": len(groups),
        "relations": plan["expectedRelations"],
        "publishedCanonicals": len(expected_canonicals),
        "retiredSources": plan["expectedRelations"],
        "destructiveDeletes": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Planeja/aplica reparos legados de galeria no D1.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "catalog-legacy-gallery-overrides.json",
    )
    parser.add_argument("--apply", action="store_true", help="aplica as relações pendentes; padrão é somente leitura")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 250:
        parser.error("--batch-size deve ficar entre 1 e 250")

    try:
        groups = load_config(args.config)
        schema = require_schema_ready()
        models, relations = fetch_state(groups)
        approvals = fetch_approval_state(groups)
        plan = plan_repairs(groups, models, relations, approvals)
        public_models_before = published_model_count()
        public_summary = {key: value for key, value in plan.items() if key not in {"statements", "expected"}}
        public_summary["schema"] = schema
        public_summary["publicModelsBefore"] = public_models_before
        public_summary["expectedPublicModelsAfter"] = public_models_before - int(plan["sourcesToRetire"])
        public_summary["apply"] = args.apply
        if not args.apply:
            print(json.dumps(public_summary, ensure_ascii=False))
            return 0

        image_source_rows = fetch_image_source_state(groups)
        sha_preflight = verify_repair_image_sources(groups, models, image_source_rows)
        batches = apply_statement_batches(plan["statements"], args.batch_size)
        models, relations = fetch_state(groups)
        approvals = fetch_approval_state(groups)
        result = verify_applied(groups, models, relations, approvals)
        public_models_after = published_model_count()
        expected_public_models_after = public_models_before - int(plan["sourcesToRetire"])
        if public_models_after != expected_public_models_after:
            raise RuntimeError(
                "quantidade pública mudou além das fontes aposentadas: "
                f"{public_models_before} -> {public_models_after}; "
                f"esperado={expected_public_models_after}"
            )
        result["schema"] = schema
        result["imageSourcePreflight"] = sha_preflight
        result["publicModelsBefore"] = public_models_before
        result["publicModelsAfter"] = public_models_after
        result["expectedPublicModelsAfter"] = expected_public_models_after
        result["materializedCounts"] = verify_materialized_counts()
        result["batches"] = batches
        result["apply"] = True
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
