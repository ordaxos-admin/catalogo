#!/usr/bin/env python3
"""Plan a safe gallery-level merge between an existing catalog and incoming images.

This planner is intentionally non-destructive. It distinguishes exact duplicates
from new gallery views and perceptual candidates. Perceptual similarity never
authorizes removal by itself; it only produces a quality-ranked review proposal.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from external_identity_contract import (
    read_candidate_report,
    read_identity_mapping,
    read_identity_resolution,
)
from media_quality_policy import (
    WATERMARK_CLEAR,
    WATERMARK_CREATOR_ATTRIBUTION,
    WATERMARK_LEGACY_ACCEPTED,
    WATERMARK_WATERMARKED,
    WATERMARK_UNKNOWN,
    manifest_watermark_status,
)

DEFAULT_VISUAL_THRESHOLD = 6


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RuntimeError(f"manifesto não encontrado: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"JSON inválido em {path}, linha {line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise RuntimeError(f"registro inválido em {path}, linha {line_no}")
            rows.append(row)
    return rows


def model_identity(row: dict[str, Any]) -> str:
    value = str(row.get("public_model_key") or row.get("model_key") or "").strip()
    if not value:
        raise RuntimeError(f"imagem sem identidade de modelo: {row.get('path', '<sem caminho>')}")
    return value


def image_sha(row: dict[str, Any]) -> str:
    digest = str(row.get("sha256") or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise RuntimeError(f"SHA-256 inválido: {row.get('path', '<sem caminho>')}")
    return digest


def image_dhash(row: dict[str, Any]) -> str | None:
    value = str(row.get("dhash") or "").strip().lower()
    if not value:
        return None
    if len(value) != 16 or any(char not in "0123456789abcdef" for char in value):
        raise RuntimeError(f"dHash inválido: {row.get('path', '<sem caminho>')}")
    return value


def quality_tuple(row: dict[str, Any]) -> tuple[float, int, int, int]:
    return (
        float(row.get("quality_score") or 0),
        int(row.get("width") or 0),
        int(row.get("height") or 0),
        int(row.get("size") or 0),
    )


def hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def public_candidate(row: dict[str, Any]) -> bool:
    return row.get("status") == "OK" and row.get("canonical") is True


def compact_image(row: dict[str, Any], source: str) -> dict[str, Any]:
    provenance = row.get("source_provenance")
    source_id = (
        str(provenance.get("sourceId") or "").strip()
        if isinstance(provenance, dict)
        else ""
    )
    return {
        "source": source,
        "sourceId": source_id or None,
        "path": str(row.get("path") or ""),
        "sha256": image_sha(row),
        "dhash": image_dhash(row),
        "qualityScore": float(row.get("quality_score") or 0),
        "width": int(row.get("width") or 0),
        "height": int(row.get("height") or 0),
        "bytes": int(row.get("size") or 0),
        "watermarkStatus": manifest_watermark_status(row),
    }


def best_quality(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=quality_tuple)


def plan_gallery_merge(
    existing_rows: list[dict[str, Any]],
    incoming_rows: list[dict[str, Any]],
    *,
    visual_threshold: int = DEFAULT_VISUAL_THRESHOLD,
    identity_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not 0 <= visual_threshold <= 16:
        raise ValueError("visual_threshold deve ficar entre 0 e 16")

    existing = [row for row in existing_rows if public_candidate(row)]
    incoming = [row for row in incoming_rows if public_candidate(row)]

    existing_identities = {model_identity(row) for row in existing}
    incoming_identities = {model_identity(row) for row in incoming}
    mapping = dict(identity_mapping or {})

    missing_sources = sorted(set(mapping).difference(incoming_identities), key=str.casefold)
    if missing_sources:
        raise RuntimeError(f"origens do mapa ausentes no manifesto de entrada: {missing_sources[:5]}")
    missing_targets = sorted(set(mapping.values()).difference(existing_identities), key=str.casefold)
    if missing_targets:
        raise RuntimeError(f"alvos do mapa ausentes no catálogo existente: {missing_targets[:5]}")
    reverse_targets: dict[str, str] = {}
    for source, target in mapping.items():
        previous = reverse_targets.get(target)
        if previous and previous != source:
            raise RuntimeError(
                f"produtos diferentes apontam para o mesmo modelo público: {previous!r}, {source!r} -> {target!r}"
            )
        reverse_targets[target] = source

    by_model: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"existing": [], "incoming": []})
    for row in existing:
        by_model[model_identity(row)]["existing"].append(row)
    for row in incoming:
        source_identity = model_identity(row)
        resolved_identity = mapping.get(source_identity, source_identity)
        planned_row = dict(row)
        planned_row["_source_model_identity"] = source_identity
        by_model[resolved_identity]["incoming"].append(planned_row)

    actions: list[dict[str, Any]] = []
    counts = defaultdict(int)

    for identity in sorted(by_model, key=str.casefold):
        current = by_model[identity]["existing"]
        candidates = sorted(
            by_model[identity]["incoming"],
            key=lambda row: (str(row.get("path") or "").casefold(), image_sha(row)),
        )

        # The evolving accepted set lets the planner deduplicate duplicates inside
        # the incoming batch without mutating either manifest.
        accepted = list(current)

        for row in candidates:
            digest = image_sha(row)
            exact = [item for item in accepted if image_sha(item) == digest]
            if exact:
                actions.append({
                    "model": identity,
                    "sourceModel": str(row.get("_source_model_identity") or identity),
                    "action": "skip_exact",
                    "incoming": compact_image(row, "incoming"),
                    "matches": [compact_image(item, "existing_or_planned") for item in exact],
                    "recommendedSource": "existing_or_planned",
                    "reason": "sha256_equal",
                })
                counts["skip_exact"] += 1
                continue

            dhash = image_dhash(row)
            visual_matches: list[tuple[int, dict[str, Any]]] = []
            if dhash:
                for item in accepted:
                    other_hash = image_dhash(item)
                    if not other_hash:
                        continue
                    distance = hamming(dhash, other_hash)
                    if distance <= visual_threshold:
                        visual_matches.append((distance, item))
            visual_matches.sort(
                key=lambda pair: (pair[0], tuple(-value for value in quality_tuple(pair[1])))
            )
            match_payload = [
                {**compact_image(item, "existing_or_planned"), "dhashDistance": distance}
                for distance, item in visual_matches
            ]

            incoming_watermark = manifest_watermark_status(row)
            if incoming_watermark == WATERMARK_WATERMARKED:
                actions.append({
                    "model": identity,
                    "sourceModel": str(row.get("_source_model_identity") or identity),
                    "action": "reject_watermarked",
                    "incoming": compact_image(row, "incoming"),
                    "matches": match_payload,
                    "recommendedSource": "existing_or_planned" if visual_matches else "none",
                    "reason": "incoming_blocking_watermark",
                })
                counts["reject_watermarked"] += 1
                continue

            if incoming_watermark == WATERMARK_UNKNOWN:
                actions.append({
                    "model": identity,
                    "sourceModel": str(row.get("_source_model_identity") or identity),
                    "action": "review_watermark_required",
                    "incoming": compact_image(row, "incoming"),
                    "matches": match_payload,
                    "recommendedSource": "audit_required",
                    "reason": "watermark_status_unknown",
                })
                counts["review_watermark_required"] += 1
                continue

            if visual_matches:
                best_existing = best_quality(item for _, item in visual_matches)
                best_existing_watermark = manifest_watermark_status(best_existing)
                incoming_is_explicitly_audited = incoming_watermark in {
                    WATERMARK_CLEAR,
                    WATERMARK_CREATOR_ATTRIBUTION,
                }
                if (
                    incoming_is_explicitly_audited
                    and best_existing_watermark == WATERMARK_WATERMARKED
                ):
                    incoming_better = True
                    recommendation_reason = "audited_incoming_beats_blocking_watermark"
                elif (
                    incoming_is_explicitly_audited
                    and best_existing_watermark == WATERMARK_LEGACY_ACCEPTED
                ):
                    incoming_better = True
                    recommendation_reason = "audited_incoming_beats_unverified_legacy_match"
                else:
                    incoming_better = quality_tuple(row) > quality_tuple(best_existing)
                    recommendation_reason = "perceptual_similarity_requires_review"
                actions.append({
                    "model": identity,
                    "sourceModel": str(row.get("_source_model_identity") or identity),
                    "action": "review_visual_candidate",
                    "incoming": compact_image(row, "incoming"),
                    "matches": match_payload,
                    "recommendedSource": "incoming" if incoming_better else "existing_or_planned",
                    "reason": recommendation_reason,
                })
                counts["review_visual_candidate"] += 1
                if incoming_better:
                    counts["review_prefers_incoming"] += 1
                else:
                    counts["review_prefers_existing"] += 1
                # A perceptual candidate is never promoted automatically. The
                # reviewer confirms same-view replacement versus a distinct angle.
                continue

            actions.append({
                "model": identity,
                "sourceModel": str(row.get("_source_model_identity") or identity),
                "action": "add_view",
                "incoming": compact_image(row, "incoming"),
                "matches": [],
                "recommendedSource": "incoming",
                "reason": "no_exact_or_perceptual_match_and_watermark_eligible",
            })
            counts["add_view"] += 1
            accepted.append(row)

    models_touched = len({action["model"] for action in actions})
    return {
        "version": 1,
        "policy": {
            "exactDuplicate": "skip",
            "distinctView": "add",
            "perceptualCandidate": "review_only",
            "watermarkedIncoming": "reject",
            "creatorAttribution": "eligible_when_explicitly_audited",
            "unknownWatermark": "block_until_audited",
            "qualityPreference": "audited_eligibility_then_technical_quality",
            "sourcePreference": "source_alone_never_overrides_identity_or_watermark_gates",
            "visualThreshold": visual_threshold,
            "destructiveDeletes": 0,
        },
        "existingImages": len(existing),
        "incomingImages": len(incoming),
        "identityMappings": len(mapping),
        "modelsTouched": models_touched,
        "counts": dict(sorted(counts.items())),
        "actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Planeja mescla de galerias sem alterar catálogo, fonte externa ou R2."
    )
    parser.add_argument("existing", type=Path, help="manifest.jsonl do catálogo atual")
    parser.add_argument("incoming", type=Path, help="manifest.jsonl das imagens a integrar")
    parser.add_argument("--output", type=Path, help="arquivo JSON do plano; stdout quando omitido")
    parser.add_argument(
        "--mapping",
        type=Path,
        help="model-identity-map.csv produzido pelo resolver de identidade",
    )
    parser.add_argument(
        "--candidate-report",
        type=Path,
        help="relatório JSON de candidatos que originou --mapping",
    )
    parser.add_argument(
        "--identity-resolution",
        type=Path,
        help="summary JSON produzido pelo resolver; obrigatório junto com --mapping",
    )
    parser.add_argument("--visual-threshold", type=int, default=DEFAULT_VISUAL_THRESHOLD)
    args = parser.parse_args()

    try:
        identity_inputs = (args.mapping, args.candidate_report, args.identity_resolution)
        if any(identity_inputs) and not all(identity_inputs):
            raise RuntimeError(
                "--mapping, --candidate-report e --identity-resolution devem ser informados juntos"
            )
        if args.mapping:
            candidate_report = read_candidate_report(args.candidate_report)
            identity_resolution = read_identity_resolution(
                args.identity_resolution, candidate_report
            )
            mapping = read_identity_mapping(
                args.mapping, candidate_report, identity_resolution
            )
        else:
            mapping = None
        plan = plan_gallery_merge(
            read_jsonl(args.existing),
            read_jsonl(args.incoming),
            visual_threshold=args.visual_threshold,
            identity_mapping=mapping,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    encoded = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
