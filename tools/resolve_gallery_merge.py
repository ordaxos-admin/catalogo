#!/usr/bin/env python3
"""Resolve a gallery merge plan into an explicit, non-destructive promotion gate.

Automatic actions (exact duplicate and clearly distinct new view) are resolved
without human input. Perceptual candidates always require an explicit decision.
This tool never moves, deletes or rewrites source images.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from media_quality_policy import (
    PROMOTABLE_WATERMARK_STATUSES,
    WATERMARK_UNKNOWN,
    WATERMARK_WATERMARKED,
    compact_watermark_status,
)

ALLOWED_DECISIONS = {"keep_existing", "add_view", "replace_existing"}


def read_plan(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"plano de galeria não encontrado: {path}")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"plano de galeria inválido: {path}: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("version") != 1 or not isinstance(plan.get("actions"), list):
        raise RuntimeError(f"contrato de plano de galeria inválido: {path}")
    return plan


def action_key(action: dict[str, Any]) -> tuple[str, str]:
    incoming = action.get("incoming")
    if not isinstance(incoming, dict):
        raise RuntimeError("ação sem imagem de entrada")
    path = str(incoming.get("path") or "").strip()
    digest = str(incoming.get("sha256") or "").strip().lower()
    if not path or len(digest) != 64:
        raise RuntimeError(f"ação com identidade de imagem inválida: {incoming!r}")
    return path, digest


def read_decisions(path: Path | None) -> dict[tuple[str, str], dict[str, str]]:
    if path is None:
        return {}
    if not path.is_file():
        raise RuntimeError(f"decisões de galeria não encontradas: {path}")

    decisions: dict[tuple[str, str], dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"incoming_path", "incoming_sha256", "decision", "replace_sha256"}
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError(
                f"decisões sem colunas obrigatórias {sorted(required)}: {path}"
            )
        for line_no, row in enumerate(reader, 2):
            incoming_path = str(row.get("incoming_path") or "").strip()
            incoming_sha = str(row.get("incoming_sha256") or "").strip().lower()
            decision = str(row.get("decision") or "").strip()
            replace_sha = str(row.get("replace_sha256") or "").strip().lower()
            if not incoming_path or len(incoming_sha) != 64:
                raise RuntimeError(f"decisão com imagem inválida em {path}, linha {line_no}")
            if decision not in ALLOWED_DECISIONS:
                raise RuntimeError(f"decisão inválida em {path}, linha {line_no}: {decision!r}")
            if replace_sha and (len(replace_sha) != 64 or any(ch not in "0123456789abcdef" for ch in replace_sha)):
                raise RuntimeError(f"replace_sha256 inválido em {path}, linha {line_no}")
            key = (incoming_path, incoming_sha)
            if key in decisions:
                raise RuntimeError(f"decisão duplicada para imagem: {incoming_path}")
            decisions[key] = {
                "decision": decision,
                "replace_sha256": replace_sha,
            }
    return decisions


def resolve_gallery_merge(
    plan: dict[str, Any],
    decisions: dict[tuple[str, str], dict[str, str]] | None = None,
) -> dict[str, Any]:
    if plan.get("version") != 1 or not isinstance(plan.get("actions"), list):
        raise RuntimeError("contrato de plano de galeria inválido")

    decision_map = dict(decisions or {})
    seen_actions: set[tuple[str, str]] = set()
    used_decisions: set[tuple[str, str]] = set()
    promotions: list[dict[str, Any]] = []
    kept_existing: list[dict[str, Any]] = []
    skipped_exact: list[dict[str, Any]] = []
    rejected_watermarked: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for action in plan["actions"]:
        if not isinstance(action, dict):
            raise RuntimeError("ação inválida no plano de galeria")
        key = action_key(action)
        if key in seen_actions:
            raise RuntimeError(f"imagem de entrada aparece em múltiplas ações: {key[0]}")
        seen_actions.add(key)

        kind = str(action.get("action") or "")
        model = str(action.get("model") or "").strip()
        source_model = str(action.get("sourceModel") or model).strip()
        incoming = action["incoming"]
        watermark = compact_watermark_status(incoming.get("watermarkStatus"))
        if not model:
            raise RuntimeError(f"ação sem modelo público: {key[0]}")

        if kind == "skip_exact":
            skipped_exact.append({
                "model": model,
                "sourceModel": source_model,
                "incoming": incoming,
                "reason": "sha256_equal",
            })
            counts["skip_exact"] += 1
            continue

        if kind == "reject_watermarked":
            if watermark != WATERMARK_WATERMARKED:
                raise RuntimeError(
                    f"ação reject_watermarked sem evidência watermarked: {key[0]} ({watermark})"
                )
            rejected_watermarked.append({
                "model": model,
                "sourceModel": source_model,
                "incoming": incoming,
                "reason": "incoming_watermarked",
            })
            counts["reject_watermarked"] += 1
            continue

        if kind == "review_watermark_required":
            if watermark != WATERMARK_UNKNOWN:
                raise RuntimeError(
                    f"ação review_watermark_required com status divergente: {key[0]} ({watermark})"
                )
            raise RuntimeError(
                "imagem externa sem auditoria conclusiva de marca d'água; "
                f"reexecute o manifesto com --media-audit: {key[0]}"
            )

        if kind == "add_view":
            if watermark not in PROMOTABLE_WATERMARK_STATUSES:
                raise RuntimeError(
                    f"add_view bloqueado por watermarkStatus={watermark}: {key[0]}"
                )
            promotions.append({
                "model": model,
                "sourceModel": source_model,
                "incoming": incoming,
                "mode": "add_view",
                "replaceSha256": None,
            })
            counts["promote_add_view"] += 1
            continue

        if kind != "review_visual_candidate":
            raise RuntimeError(f"ação desconhecida no plano de galeria: {kind!r}")
        if watermark not in PROMOTABLE_WATERMARK_STATUSES:
            raise RuntimeError(
                f"candidato visual bloqueado por watermarkStatus={watermark}: {key[0]}"
            )

        decision = decision_map.get(key)
        if decision is None:
            raise RuntimeError(
                f"candidato visual sem decisão explícita: {key[0]} ({key[1]})"
            )
        used_decisions.add(key)
        choice = decision["decision"]
        replace_sha = decision["replace_sha256"]
        matches = action.get("matches")
        if not isinstance(matches, list) or not matches:
            raise RuntimeError(f"candidato visual sem correspondências: {key[0]}")
        match_shas = {
            str(match.get("sha256") or "").strip().lower()
            for match in matches
            if isinstance(match, dict)
        }

        if choice == "keep_existing":
            if replace_sha:
                raise RuntimeError(f"keep_existing não aceita replace_sha256: {key[0]}")
            kept_existing.append({
                "model": model,
                "sourceModel": source_model,
                "incoming": incoming,
                "decision": choice,
            })
            counts["keep_existing"] += 1
            continue

        if choice == "add_view":
            if replace_sha:
                raise RuntimeError(f"add_view não aceita replace_sha256: {key[0]}")
            promotions.append({
                "model": model,
                "sourceModel": source_model,
                "incoming": incoming,
                "mode": "add_view",
                "replaceSha256": None,
            })
            counts["promote_add_view"] += 1
            continue

        if not replace_sha:
            raise RuntimeError(f"replace_existing exige replace_sha256: {key[0]}")
        if replace_sha not in match_shas:
            raise RuntimeError(
                f"replace_sha256 não pertence aos candidatos visuais de {key[0]}: {replace_sha}"
            )
        promotions.append({
            "model": model,
            "sourceModel": source_model,
            "incoming": incoming,
            "mode": "replace_existing",
            "replaceSha256": replace_sha,
        })
        counts["promote_replace_existing"] += 1

    unused = sorted(set(decision_map).difference(used_decisions))
    if unused:
        raise RuntimeError(f"decisões sem candidato visual correspondente: {unused[:5]}")

    return {
        "version": 1,
        "planVersion": 1,
        "ready": True,
        "policy": {
            "perceptualCandidatesRequireExplicitDecision": True,
            "unknownWatermarkRequiresUpstreamAudit": True,
            "watermarkedIncomingPromoted": 0,
            "sourceFilesModified": 0,
            "destructiveDeletes": 0,
        },
        "counts": dict(sorted(counts.items())),
        "promotions": promotions,
        "keptExisting": kept_existing,
        "skippedExact": skipped_exact,
        "rejectedWatermarked": rejected_watermarked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve plano de galeria em uma lista explícita e não destrutiva de promoções."
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument(
        "--decisions",
        type=Path,
        help="CSV incoming_path,incoming_sha256,decision,replace_sha256 para candidatos visuais",
    )
    parser.add_argument("--output", type=Path, help="JSON de resolução; stdout quando omitido")
    args = parser.parse_args()

    try:
        resolved = resolve_gallery_merge(
            read_plan(args.plan),
            read_decisions(args.decisions),
        )
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    encoded = json.dumps(resolved, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
