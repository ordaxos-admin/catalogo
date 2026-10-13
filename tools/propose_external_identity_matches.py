#!/usr/bin/env python3
"""Propose review-only identity matches for materialized external catalog media.

The tool never creates or applies an identity mapping. It combines resolved
category/franchise scope, exact display-name evidence and image evidence
(SHA-256/dHash) to order candidates for an operator review. Even exact SHA
matches remain review-only because one source image may legitimately belong to
multiple public models.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from model_identity import taxonomy_name_comparison_key
from plan_gallery_merge import (
    DEFAULT_VISUAL_THRESHOLD,
    hamming,
    image_dhash,
    image_sha,
    model_identity,
    public_candidate,
    read_jsonl,
)

EXTERNAL_PROVENANCE = "AUTHORIZED_EXTERNAL_MEDIA"


def required_text(row: dict[str, Any], field: str, label: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise RuntimeError(f"{label} sem {field}")
    return value


def name_key(value: str) -> str:
    return taxonomy_name_comparison_key(value)


def model_name_keys(model: dict[str, Any]) -> set[str]:
    display = required_text(model, "displayName", "modelo existente")
    variant = str(model.get("variantName") or "").strip()
    keys = {name_key(display)}
    if variant:
        keys.add(name_key(f"{display} {variant}"))
    return {key for key in keys if key}


def index_existing_models(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[tuple[str, str], list[str]]]:
    models: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    by_scope: dict[tuple[str, str], list[str]] = defaultdict(list)
    ids: dict[str, str] = {}

    for row in rows:
        identity = required_text(row, "identityKey", "modelo existente")
        model_id = required_text(row, "id", f"modelo {identity}")
        category = required_text(row, "categoryName", f"modelo {identity}")
        franchise = required_text(row, "franchiseName", f"modelo {identity}")
        model_name_keys(row)

        if identity in models:
            raise RuntimeError(f"identityKey duplicada em models.jsonl: {identity}")
        previous_id = ids.get(model_id)
        if previous_id and previous_id != identity:
            raise RuntimeError(
                f"id público pertence a múltiplas identidades: {model_id}: "
                f"{previous_id!r} | {identity!r}"
            )
        ids[model_id] = identity
        models[identity] = row

        source_identity = str(row.get("sourceIdentityKey") or "").strip()
        for alias in {identity, source_identity} - {""}:
            previous = aliases.get(alias)
            if previous and previous != identity:
                raise RuntimeError(
                    f"identidade/alias aponta para múltiplos modelos: {alias}: "
                    f"{previous!r} | {identity!r}"
                )
            aliases[alias] = identity

        scope = (name_key(category), name_key(franchise))
        by_scope[scope].append(identity)

    if not models:
        raise RuntimeError("models.jsonl existente está vazio")
    for identities in by_scope.values():
        identities.sort(key=str.casefold)
    return models, aliases, by_scope


def index_existing_images(
    rows: list[dict[str, Any]],
    aliases: dict[str, str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, set[str]]]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sha_owners: dict[str, set[str]] = defaultdict(set)
    unresolved: set[str] = set()

    for row in rows:
        if not public_candidate(row):
            continue
        source_identity = model_identity(row)
        target_identity = aliases.get(source_identity)
        if target_identity is None:
            unresolved.add(source_identity)
            continue
        digest = image_sha(row)
        image_dhash(row)
        by_model[target_identity].append(row)
        sha_owners[digest].add(target_identity)

    if unresolved:
        sample = sorted(unresolved, key=str.casefold)[:5]
        raise RuntimeError(
            "manifesto existente referencia identidades ausentes de models.jsonl: "
            f"{sample}"
        )
    return by_model, sha_owners


def group_external_images(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}

    for row in rows:
        if not public_candidate(row):
            continue
        provenance = row.get("source_provenance")
        if not isinstance(provenance, dict) or provenance.get("kind") != EXTERNAL_PROVENANCE:
            raise RuntimeError(
                f"manifesto de entrada contém mídia sem provenance externa autorizada: "
                f"{row.get('path', '<sem caminho>')}"
            )
        incoming = model_identity(row)
        source_product_key = required_text(
            provenance, "sourceProductKey", f"mídia externa {incoming}"
        )
        category = required_text(row, "category", f"mídia externa {incoming}")
        franchise = required_text(row, "franchise", f"mídia externa {incoming}")
        display_name = required_text(row, "identification", f"mídia externa {incoming}")
        image_sha(row)
        image_dhash(row)

        current = groups.get(incoming)
        metadata = {
            "sourceProductKey": source_product_key,
            "category": category,
            "franchise": franchise,
            "displayName": display_name,
        }
        if current is None:
            current = {**metadata, "images": []}
            groups[incoming] = current
        else:
            for field, expected in metadata.items():
                if current[field] != expected:
                    raise RuntimeError(
                        f"mídia do mesmo produto externo diverge em {field}: {incoming}: "
                        f"{current[field]!r} | {expected!r}"
                    )
        current["images"].append(row)

    if not groups:
        raise RuntimeError("manifesto externo não contém imagens públicas candidatas")
    return groups


def visual_evidence(
    incoming: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    *,
    threshold: int,
) -> dict[str, Any]:
    incoming_shas = {image_sha(row) for row in incoming}
    existing_shas = {image_sha(row) for row in existing}
    exact_shas = sorted(incoming_shas & existing_shas)

    visual_pairs: list[dict[str, Any]] = []
    matched_incoming: set[str] = set()
    minimum_distance: int | None = None
    for left in incoming:
        left_sha = image_sha(left)
        left_hash = image_dhash(left)
        if not left_hash:
            continue
        for right in existing:
            right_sha = image_sha(right)
            if left_sha == right_sha:
                continue
            right_hash = image_dhash(right)
            if not right_hash:
                continue
            distance = hamming(left_hash, right_hash)
            if distance > threshold:
                continue
            matched_incoming.add(left_sha)
            minimum_distance = distance if minimum_distance is None else min(minimum_distance, distance)
            if len(visual_pairs) < 5:
                visual_pairs.append(
                    {
                        "incomingSha256": left_sha,
                        "existingSha256": right_sha,
                        "dhashDistance": distance,
                    }
                )

    visual_pairs.sort(
        key=lambda item: (
            item["dhashDistance"],
            item["incomingSha256"],
            item["existingSha256"],
        )
    )
    return {
        "exactSha256": exact_shas,
        "exactShaMatchCount": len(exact_shas),
        "visualMatchImageCount": len(matched_incoming),
        "minimumDhashDistance": minimum_distance,
        "sampleVisualPairs": visual_pairs,
    }


def candidate_rank(*, exact_count: int, exact_name: bool, visual_count: int) -> tuple[int, str]:
    if exact_count:
        return 4, "strong_exact_sha_review"
    if exact_name and visual_count:
        return 3, "strong_name_plus_visual_review"
    if exact_name:
        return 2, "name_review"
    if visual_count:
        return 1, "visual_review"
    return 0, "none"


def propose_identity_matches(
    existing_models: list[dict[str, Any]],
    existing_images: list[dict[str, Any]],
    incoming_images: list[dict[str, Any]],
    *,
    visual_threshold: int = DEFAULT_VISUAL_THRESHOLD,
) -> dict[str, Any]:
    if not 0 <= visual_threshold <= 16:
        raise ValueError("visual_threshold deve ficar entre 0 e 16")

    models, aliases, by_scope = index_existing_models(existing_models)
    images_by_model, sha_owners = index_existing_images(existing_images, aliases)
    incoming_groups = group_external_images(incoming_images)

    products: list[dict[str, Any]] = []
    counts: defaultdict[str, int] = defaultdict(int)

    for incoming_identity in sorted(incoming_groups, key=str.casefold):
        group = incoming_groups[incoming_identity]
        scope = (name_key(group["category"]), name_key(group["franchise"]))
        incoming_name_key = name_key(group["displayName"])
        candidates: list[dict[str, Any]] = []

        for target_identity in by_scope.get(scope, []):
            model = models[target_identity]
            evidence = visual_evidence(
                group["images"],
                images_by_model.get(target_identity, []),
                threshold=visual_threshold,
            )
            exact_name = incoming_name_key in model_name_keys(model)
            rank, priority = candidate_rank(
                exact_count=evidence["exactShaMatchCount"],
                exact_name=exact_name,
                visual_count=evidence["visualMatchImageCount"],
            )
            if rank == 0:
                continue
            candidates.append(
                {
                    "_rank": rank,
                    "targetModel": target_identity,
                    "targetModelId": required_text(model, "id", f"modelo {target_identity}"),
                    "displayName": required_text(model, "displayName", f"modelo {target_identity}"),
                    "variantName": str(model.get("variantName") or "").strip() or None,
                    "reviewPriority": priority,
                    "evidence": {
                        "exactName": exact_name,
                        **evidence,
                    },
                }
            )

        candidates.sort(
            key=lambda item: (
                -int(item["_rank"]),
                -int(item["evidence"]["exactShaMatchCount"]),
                item["evidence"]["minimumDhashDistance"]
                if item["evidence"]["minimumDhashDistance"] is not None
                else 99,
                str(item["targetModel"]).casefold(),
            )
        )
        top_rank = int(candidates[0]["_rank"]) if candidates else 0
        top_tier_count = sum(int(item["_rank"]) == top_rank for item in candidates) if candidates else 0
        for item in candidates:
            item.pop("_rank", None)

        scope_conflicts: list[dict[str, str]] = []
        seen_conflicts: set[tuple[str, str]] = set()
        for row in group["images"]:
            digest = image_sha(row)
            for owner in sorted(sha_owners.get(digest, set()), key=str.casefold):
                model = models[owner]
                owner_scope = (
                    name_key(required_text(model, "categoryName", f"modelo {owner}")),
                    name_key(required_text(model, "franchiseName", f"modelo {owner}")),
                )
                if owner_scope == scope:
                    continue
                key = (digest, owner)
                if key in seen_conflicts:
                    continue
                seen_conflicts.add(key)
                scope_conflicts.append({"sha256": digest, "targetModel": owner})

        if scope_conflicts:
            counts["scope_conflict"] += 1
        if not candidates:
            review_state = "no_candidate"
            counts["no_candidate"] += 1
        elif top_tier_count > 1:
            review_state = "ambiguous_candidate"
            counts["ambiguous_candidate"] += 1
        else:
            review_state = "candidate"
            counts["candidate"] += 1

        products.append(
            {
                "incomingModel": incoming_identity,
                "sourceProductKey": group["sourceProductKey"],
                "displayName": group["displayName"],
                "category": group["category"],
                "franchise": group["franchise"],
                "reviewState": review_state,
                "topTierCount": top_tier_count,
                "candidates": candidates,
                "scopeConflicts": scope_conflicts,
            }
        )

    return {
        "version": 1,
        "policy": {
            "reviewOnly": True,
            "automaticMappings": 0,
            "sameCategoryAndFranchiseRequiredForCandidate": True,
            "exactNameDoesNotAuthorizeMerge": True,
            "perceptualSimilarityDoesNotAuthorizeMerge": True,
            "exactShaDoesNotAuthorizeMerge": True,
            "visualThreshold": visual_threshold,
        },
        "summary": {
            "incomingProducts": len(products),
            "candidate": counts["candidate"],
            "ambiguousCandidate": counts["ambiguous_candidate"],
            "noCandidate": counts["no_candidate"],
            "scopeConflict": counts["scope_conflict"],
            "automaticMappings": 0,
        },
        "products": products,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ordena candidatos de identidade entre mídia externa materializada e o catálogo atual; "
            "nunca cria/aplica mapa automaticamente."
        )
    )
    parser.add_argument("existing_models", type=Path, help="models.jsonl do bundle atual")
    parser.add_argument("existing_manifest", type=Path, help="manifest.jsonl do catálogo atual")
    parser.add_argument("incoming_manifest", type=Path, help="manifest externo materializado")
    parser.add_argument("--visual-threshold", type=int, default=DEFAULT_VISUAL_THRESHOLD)
    parser.add_argument("--output", type=Path, help="JSON de candidatos; stdout quando omitido")
    args = parser.parse_args()

    try:
        result = propose_identity_matches(
            read_jsonl(args.existing_models),
            read_jsonl(args.existing_manifest),
            read_jsonl(args.incoming_manifest),
            visual_threshold=args.visual_threshold,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
