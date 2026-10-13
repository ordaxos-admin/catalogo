#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from model_identity import candidate_confidence, slug_view_descriptor, taxonomy_name_comparison_key

DEFAULT_NUMBERED_REVIEW_CONFIG = (
    Path(__file__).resolve().parents[1] / "config" / "catalog-numbered-sibling-review.json"
)


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
            if not isinstance(row, dict):
                raise RuntimeError(f"registro inválido em {path}:{line_no}")
            rows.append(row)
    return rows


def split_view_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        try:
            image_count = int(row.get("imageCount") or 0)
        except (TypeError, ValueError):
            image_count = 0
        if image_count != 1:
            continue
        slug = str(row.get("slug") or "").strip()
        match = slug_view_descriptor(slug)
        if not match:
            continue
        family, descriptor = match
        key = (
            str(row.get("categorySlug") or "").strip(),
            str(row.get("franchiseSlug") or "").strip(),
            str(row.get("folderPathKey") or "").strip(),
            taxonomy_name_comparison_key(str(row.get("displayName") or "")),
            family,
        )
        groups[key].append({
            "id": str(row.get("id") or ""),
            "slug": slug,
            "code": str(row.get("code") or ""),
            "descriptor": descriptor.stem_suffix,
            "priority": descriptor.priority,
            "kind": descriptor.kind,
        })

    candidates: list[dict[str, Any]] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda item: (item["priority"], item["slug"]))
        descriptors = []
        for item in ordered:
            match = slug_view_descriptor(item["slug"])
            if match:
                descriptors.append(match[1])
        category, franchise, folder, display_name, family = key
        candidates.append({
            "categorySlug": category,
            "franchiseSlug": franchise,
            "folderPathKey": folder,
            "displayNameKey": display_name,
            "family": family,
            "confidence": candidate_confidence(descriptors),
            "canonicalId": ordered[0]["id"],
            "canonicalSlug": ordered[0]["slug"],
            "members": ordered,
        })

    return sorted(
        candidates,
        key=lambda item: (
            0 if item["confidence"] == "high" else 1,
            item["categorySlug"],
            item["franchiseSlug"],
            item["folderPathKey"],
            item["family"],
        ),
    )


def sibling_suffix_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find likely file/export suffix siblings without merging anything.

    Explicit copy markers are strong review signals. Pure numeric suffixes are
    review-only because numbered models are common legitimate products.
    A candidate exists only when the exact base slug is also present in the
    same category/franchise/folder/display-name scope.
    """
    scopes: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        key = (
            str(row.get("categorySlug") or "").strip(),
            str(row.get("franchiseSlug") or "").strip(),
            str(row.get("folderPathKey") or "").strip(),
            taxonomy_name_comparison_key(str(row.get("displayName") or "")),
        )
        scopes[key][slug] = row

    explicit_pattern = re.compile(r"-(?:copy|copia|duplicate|duplicado)(?:-\d+)?$", re.IGNORECASE)
    numeric_pattern = re.compile(r"-\d{1,6}$")
    grouped: dict[tuple[tuple[str, str, str, str], str, str], dict[str, Any]] = {}

    for scope, by_slug in scopes.items():
        for slug, row in by_slug.items():
            kind = ""
            match = explicit_pattern.search(slug)
            if match:
                kind = "explicit-copy-marker"
            else:
                match = numeric_pattern.search(slug)
                if match:
                    kind = "numbered-review"
            if not match:
                continue
            base_slug = slug[:match.start()]
            base = by_slug.get(base_slug)
            if not base:
                continue
            group_key = (scope, base_slug, kind)
            item = grouped.setdefault(group_key, {
                "kind": kind,
                "categorySlug": scope[0],
                "franchiseSlug": scope[1],
                "folderPathKey": scope[2],
                "displayNameKey": scope[3],
                "baseId": str(base.get("id") or ""),
                "baseSlug": base_slug,
                "siblings": [],
            })
            item["siblings"].append({
                "id": str(row.get("id") or ""),
                "slug": slug,
                "code": str(row.get("code") or ""),
            })

    results = list(grouped.values())
    for item in results:
        item["siblings"].sort(key=lambda sibling: sibling["slug"])
    return sorted(
        results,
        key=lambda item: (
            0 if item["kind"] == "explicit-copy-marker" else 1,
            item["categorySlug"],
            item["franchiseSlug"],
            item["folderPathKey"],
            item["baseSlug"],
        ),
    )


def load_numbered_sibling_review_registry(
    path: Path | None = None,
) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    config_path = path or DEFAULT_NUMBERED_REVIEW_CONFIG
    if not config_path.is_file():
        raise RuntimeError(f"fila de revisão numerada ausente: {config_path}")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"fila de revisão numerada inválida: {config_path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RuntimeError(f"versão inválida da fila de revisão numerada: {config_path}")
    groups = payload.get("groups")
    if not isinstance(groups, list):
        raise RuntimeError(f"groups inválido na fila de revisão numerada: {config_path}")

    registry: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    sibling_rows = 0
    for index, item in enumerate(groups, 1):
        if not isinstance(item, dict):
            raise RuntimeError(f"grupo inválido na fila numerada, posição {index}")
        category = str(item.get("categorySlug") or "").strip()
        franchise = str(item.get("franchiseSlug") or "").strip()
        folder = str(item.get("folderPathKey") or "").strip()
        display_name = str(item.get("displayName") or "").strip()
        base_id = str(item.get("baseId") or "").strip()
        base_slug = str(item.get("baseSlug") or "").strip()
        status = str(item.get("status") or "").strip()
        reason = str(item.get("reason") or "").strip()
        siblings = item.get("siblingSlugs")
        if (
            not category
            or not franchise
            or not display_name
            or not base_id
            or not base_slug
            or status not in {"pending", "distinct", "mixed", "gallery"}
            or not reason
            or not isinstance(siblings, list)
            or not siblings
        ):
            raise RuntimeError(f"grupo incompleto/inválido na fila numerada: {item!r}")
        normalized_siblings = [str(slug).strip() for slug in siblings]
        if (
            any(not slug for slug in normalized_siblings)
            or len(set(normalized_siblings)) != len(normalized_siblings)
            or base_slug in normalized_siblings
        ):
            raise RuntimeError(f"slugs inválidos na fila numerada: {base_slug}")
        key = (category, franchise, folder, taxonomy_name_comparison_key(display_name), base_slug)
        if key in registry:
            raise RuntimeError(f"grupo duplicado na fila numerada: {base_slug}")
        sibling_rows += len(normalized_siblings)
        registry[key] = {
            **item,
            "categorySlug": category,
            "franchiseSlug": franchise,
            "folderPathKey": folder,
            "displayName": display_name,
            "baseId": base_id,
            "baseSlug": base_slug,
            "siblingSlugs": normalized_siblings,
            "status": status,
            "reason": reason,
        }

    expected_groups = payload.get("baselineGroups")
    expected_rows = payload.get("baselineSiblingRows")
    if expected_groups != len(registry) or expected_rows != sibling_rows:
        raise RuntimeError(
            "baseline da fila numerada diverge do conteúdo: "
            f"groups={expected_groups}/{len(registry)}, siblings={expected_rows}/{sibling_rows}"
        )
    return registry


def validate_numbered_sibling_review(
    rows: list[dict[str, Any]],
    registry: dict[tuple[str, str, str, str, str], dict[str, Any]] | None = None,
    *,
    require_complete_registry: bool = False,
) -> dict[str, Any]:
    reviewed = registry if registry is not None else load_numbered_sibling_review_registry()
    candidates = [
        candidate
        for candidate in sibling_suffix_candidates(rows)
        if candidate["kind"] == "numbered-review"
    ]
    unregistered: list[str] = []
    changed: list[str] = []
    matched_keys: set[tuple[str, str, str, str, str]] = set()
    pending = 0
    distinct = 0
    mixed = 0
    gallery = 0

    for candidate in candidates:
        key = (
            candidate["categorySlug"],
            candidate["franchiseSlug"],
            candidate["folderPathKey"],
            candidate["displayNameKey"],
            candidate["baseSlug"],
        )
        item = reviewed.get(key)
        if item is None:
            unregistered.append(candidate["baseSlug"])
            continue
        matched_keys.add(key)
        candidate_siblings = sorted(member["slug"] for member in candidate["siblings"])
        registered_siblings = sorted(item["siblingSlugs"])
        membership_matches = (
            candidate_siblings == registered_siblings
            if require_complete_registry
            else set(candidate_siblings).issubset(registered_siblings)
        )
        if (
            str(candidate["baseId"]) != str(item["baseId"])
            or not membership_matches
        ):
            changed.append(candidate["baseSlug"])
            continue
        if item["status"] == "pending":
            pending += 1
        elif item["status"] == "distinct":
            distinct += 1
        elif item["status"] == "mixed":
            mixed += 1
        elif item["status"] == "gallery":
            gallery += 1

    if unregistered:
        raise RuntimeError(
            "grupo(s) numerado(s) sem revisão registrada: "
            f"{len(unregistered)}; exemplos={unregistered[:5]}"
        )
    if changed:
        raise RuntimeError(
            "grupo(s) numerado(s) mudaram desde a revisão registrada: "
            f"{len(changed)}; exemplos={changed[:5]}"
        )

    if require_complete_registry:
        stale_keys = sorted(set(reviewed).difference(matched_keys))
        if stale_keys:
            stale = [key[-1] for key in stale_keys]
            raise RuntimeError(
                "fila numerada contém grupo(s) sem candidato atual; remova a dívida resolvida: "
                f"{len(stale)}; exemplos={stale[:5]}"
            )

    return {
        "ready": True,
        "candidateGroups": len(candidates),
        "candidateSiblingRows": sum(len(item["siblings"]) for item in candidates),
        "pendingGroups": pending,
        "distinctGroups": distinct,
        "mixedGroups": mixed,
        "galleryGroups": gallery,
        "registeredBaselineGroups": len(reviewed),
        "registeredBaselineSiblingRows": sum(len(item["siblingSlugs"]) for item in reviewed.values()),
    }


def cross_model_sha_candidates(rows: list[dict[str, Any]], r2_root: Path) -> list[dict[str, Any]]:
    by_sha: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        model_id = str(row.get("id") or "")
        manifest_key = str(row.get("galleryManifestKey") or "")
        if not model_id or not manifest_key:
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
            if len(sha) != 64:
                continue
            by_sha[sha].append({
                "modelId": model_id,
                "slug": str(row.get("slug") or ""),
                "imageId": str(image.get("id") or ""),
            })

    results = []
    for sha, refs in by_sha.items():
        model_ids = sorted({ref["modelId"] for ref in refs})
        if len(model_ids) < 2:
            continue
        results.append({
            "sha256": sha,
            "modelCount": len(model_ids),
            "models": refs,
        })
    return sorted(results, key=lambda item: (-item["modelCount"], item["sha256"]))


def sibling_review_priority(candidate: dict[str, Any]) -> str:
    if candidate.get("kind") == "explicit-copy-marker":
        return "P0"
    count = len(candidate.get("siblings") or [])
    if count >= 10:
        return "P1"
    if count >= 4:
        return "P2"
    return "P3"


def write_report(
    output: Path,
    view_candidates: list[dict[str, Any]],
    sha_candidates: list[dict[str, Any]],
    sibling_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    sibling_candidates = [
        {
            **item,
            "reviewPriority": sibling_review_priority(item),
            "siblingCount": len(item.get("siblings") or []),
        }
        for item in (sibling_candidates or [])
    ]
    sibling_candidates.sort(
        key=lambda item: (
            item["reviewPriority"],
            -int(item["siblingCount"]),
            item["categorySlug"],
            item["franchiseSlug"],
            item["folderPathKey"],
            item["baseSlug"],
        )
    )
    explicit_copy = [item for item in sibling_candidates if item["kind"] == "explicit-copy-marker"]
    numbered_review = [item for item in sibling_candidates if item["kind"] == "numbered-review"]
    high = [item for item in view_candidates if item["confidence"] == "high"]
    review = [item for item in view_candidates if item["confidence"] != "high"]
    summary = {
        "version": 3,
        "splitViewCandidateGroups": len(view_candidates),
        "highConfidenceGroups": len(high),
        "reviewGroups": len(review),
        "splitViewRows": sum(len(item["members"]) for item in view_candidates),
        "potentialExtraCards": sum(len(item["members"]) - 1 for item in view_candidates),
        "crossModelExactShaGroups": len(sha_candidates),
        "explicitCopyMarkerGroups": len(explicit_copy),
        "numberedSiblingReviewGroups": len(numbered_review),
        "numberedSiblingRows": sum(len(item["siblings"]) for item in numbered_review),
    }
    (output / "identity-audit.json").write_text(
        json.dumps({
            "summary": summary,
            "splitViewCandidates": view_candidates,
            "crossModelShaCandidates": sha_candidates,
            "siblingSuffixCandidates": sibling_candidates,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output / "split-view-candidates.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["confidence", "category", "franchise", "folder", "family", "canonical_slug", "member_slugs"])
        for item in view_candidates:
            writer.writerow([
                item["confidence"], item["categorySlug"], item["franchiseSlug"],
                item["folderPathKey"], item["family"], item["canonicalSlug"],
                " | ".join(member["slug"] for member in item["members"]),
            ])
    with (output / "sibling-suffix-candidates.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["priority", "kind", "sibling_count", "category", "franchise", "folder", "base_slug", "sibling_slugs"])
        for item in sibling_candidates:
            writer.writerow([
                item["reviewPriority"], item["kind"], item["siblingCount"],
                item["categorySlug"], item["franchiseSlug"],
                item["folderPathKey"], item["baseSlug"],
                " | ".join(member["slug"] for member in item["siblings"]),
            ])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita identidade de produto e fichas fragmentadas por vistas.")
    parser.add_argument("models", type=Path, help="models.jsonl do bundle")
    parser.add_argument("--r2-root", type=Path, help="raiz r2 do bundle para detectar SHA exato entre modelos")
    parser.add_argument("--output", type=Path, default=Path(".identity-audit"))
    parser.add_argument("--fail-on-high", action="store_true", help="falha se houver grupo de vistas de alta confiança não consolidado")
    parser.add_argument(
        "--fail-on-explicit-copy",
        action="store_true",
        help="falha se houver sufixo copy/copia/duplicate com base correspondente no mesmo escopo",
    )
    args = parser.parse_args()

    rows = load_models(args.models)
    numbered_review = validate_numbered_sibling_review(rows, require_complete_registry=True)
    view_candidates = split_view_candidates(rows)
    sha_candidates = cross_model_sha_candidates(rows, args.r2_root) if args.r2_root else []
    sibling_candidates = sibling_suffix_candidates(rows)
    summary = write_report(args.output, view_candidates, sha_candidates, sibling_candidates)
    summary["numberedSiblingReview"] = numbered_review
    print(json.dumps(summary, ensure_ascii=False))
    if args.fail_on_high and summary["highConfidenceGroups"]:
        return 2
    if args.fail_on_explicit_copy and summary["explicitCopyMarkerGroups"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
