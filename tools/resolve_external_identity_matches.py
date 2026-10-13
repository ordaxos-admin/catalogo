#!/usr/bin/env python3
"""Resolve review-only external identity candidates into an explicit mapping CSV.

Every incoming product needs an operator decision. Existing-model mappings are
accepted only when the selected target appeared in the candidate report. A
`new_model` decision is explicit and produces no mapping row. This tool never
mutates catalog data or media.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ALLOWED_DECISIONS = {"map_existing", "new_model"}
DECISION_FIELDS = ["incoming_model", "decision", "target_model", "reason"]
MAPPING_FIELDS = [
    "incoming_model",
    "target_model",
    "candidate_report_sha256",
    "decision_reason",
]


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_candidate_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"relatório de candidatos não encontrado: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"relatório de candidatos inválido: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RuntimeError(f"contrato de candidatos inválido: {path}")
    policy = payload.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("reviewOnly") is not True
        or policy.get("automaticMappings") != 0
    ):
        raise RuntimeError("relatório de candidatos não preserva política review-only")
    if not isinstance(payload.get("products"), list):
        raise RuntimeError("relatório de candidatos sem products")
    return payload


def read_decisions(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        raise RuntimeError(f"decisões de identidade não encontradas: {path}")
    decisions: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != DECISION_FIELDS:
            raise RuntimeError(
                f"colunas de decisões divergentes: esperado {DECISION_FIELDS}, obtido {reader.fieldnames}"
            )
        for line_no, row in enumerate(reader, 2):
            incoming = str(row.get("incoming_model") or "").strip()
            decision = str(row.get("decision") or "").strip()
            target = str(row.get("target_model") or "").strip()
            reason = str(row.get("reason") or "").strip()
            if not incoming:
                raise RuntimeError(f"decisão sem incoming_model na linha {line_no}")
            if decision not in ALLOWED_DECISIONS:
                raise RuntimeError(
                    f"decisão inválida na linha {line_no}: {decision!r}; "
                    f"permitidas={sorted(ALLOWED_DECISIONS)}"
                )
            if not reason:
                raise RuntimeError(f"decisão sem justificativa na linha {line_no}: {incoming}")
            if decision == "map_existing" and not target:
                raise RuntimeError(f"map_existing exige target_model na linha {line_no}: {incoming}")
            if decision == "new_model" and target:
                raise RuntimeError(f"new_model não aceita target_model na linha {line_no}: {incoming}")
            if incoming in decisions:
                raise RuntimeError(f"decisão duplicada para incoming_model: {incoming}")
            decisions[incoming] = {
                "decision": decision,
                "target_model": target,
                "reason": reason,
            }
    if not decisions:
        raise RuntimeError(f"arquivo de decisões vazio: {path}")
    return decisions


def resolve_identity_matches(
    report: dict[str, Any],
    decisions: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if report.get("version") != 1 or not isinstance(report.get("products"), list):
        raise RuntimeError("contrato de candidatos inválido")
    policy = report.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("reviewOnly") is not True
        or policy.get("automaticMappings") != 0
    ):
        raise RuntimeError("relatório de candidatos não preserva política review-only")

    report_digest = canonical_sha256(report)
    products: dict[str, dict[str, Any]] = {}
    for product in report["products"]:
        if not isinstance(product, dict):
            raise RuntimeError("produto inválido no relatório de candidatos")
        incoming = str(product.get("incomingModel") or "").strip()
        if not incoming:
            raise RuntimeError("produto sem incomingModel no relatório de candidatos")
        if incoming in products:
            raise RuntimeError(f"incomingModel duplicado no relatório de candidatos: {incoming}")
        candidates = product.get("candidates")
        conflicts = product.get("scopeConflicts")
        if not isinstance(candidates, list) or not isinstance(conflicts, list):
            raise RuntimeError(f"produto com candidatos/conflitos inválidos: {incoming}")
        products[incoming] = product

    missing = sorted(set(products).difference(decisions), key=str.casefold)
    extra = sorted(set(decisions).difference(products), key=str.casefold)
    if missing or extra:
        raise RuntimeError(
            f"decisões não cobrem exatamente o relatório: ausentes={missing[:5]} extras={extra[:5]}"
        )

    mappings: list[dict[str, str]] = []
    new_models: list[dict[str, Any]] = []
    target_owners: dict[str, str] = {}
    reviewed_scope_conflicts = 0

    for incoming in sorted(products, key=str.casefold):
        product = products[incoming]
        decision = decisions[incoming]
        candidate_targets: set[str] = set()
        for candidate in product["candidates"]:
            if not isinstance(candidate, dict):
                raise RuntimeError(f"candidato inválido para {incoming}")
            target = str(candidate.get("targetModel") or "").strip()
            if not target:
                raise RuntimeError(f"candidato sem targetModel para {incoming}")
            if target in candidate_targets:
                raise RuntimeError(f"targetModel repetido nos candidatos de {incoming}: {target}")
            candidate_targets.add(target)

        scope_conflicts = product["scopeConflicts"]
        if scope_conflicts:
            reviewed_scope_conflicts += 1

        choice = decision["decision"]
        target = decision["target_model"]
        reason = decision["reason"]
        if choice == "map_existing":
            if target not in candidate_targets:
                raise RuntimeError(
                    f"target_model não pertence aos candidatos auditados de {incoming}: {target!r}"
                )
            previous = target_owners.get(target)
            if previous and previous != incoming:
                raise RuntimeError(
                    f"produtos diferentes tentam mapear para o mesmo modelo público: "
                    f"{previous!r}, {incoming!r} -> {target!r}"
                )
            target_owners[target] = incoming
            mappings.append(
                {
                    "incoming_model": incoming,
                    "target_model": target,
                    "candidate_report_sha256": report_digest,
                    "decision_reason": reason,
                }
            )
            continue

        new_models.append(
            {
                "incomingModel": incoming,
                "sourceProductKey": str(product.get("sourceProductKey") or "").strip() or None,
                "displayName": str(product.get("displayName") or "").strip() or None,
                "reason": reason,
                "hadCandidates": bool(candidate_targets),
                "scopeConflictCount": len(scope_conflicts),
            }
        )

    return {
        "version": 1,
        "ready": True,
        "candidateReportSha256": report_digest,
        "policy": {
            "allIncomingProductsRequireDecision": True,
            "mapExistingMustSelectReportedCandidate": True,
            "newModelRequiresExplicitDecision": True,
            "oneIncomingPerExistingTarget": True,
            "automaticMappings": 0,
            "catalogWrites": 0,
            "mediaWrites": 0,
        },
        "counts": {
            "incomingProducts": len(products),
            "mappedExisting": len(mappings),
            "newModels": len(new_models),
            "productsWithScopeConflictReviewed": reviewed_scope_conflicts,
            "automaticMappings": 0,
        },
        "mappings": mappings,
        "newModels": new_models,
    }


def write_mapping_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MAPPING_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve decisões explícitas sobre candidatos externos e gera model-identity-map.csv; "
            "não altera catálogo, R2 ou D1."
        )
    )
    parser.add_argument("candidate_report", type=Path)
    parser.add_argument("decisions", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="model-identity-map.csv")
    parser.add_argument("--summary", type=Path, help="JSON auditável da resolução")
    args = parser.parse_args()

    try:
        report = read_candidate_report(args.candidate_report)
        result = resolve_identity_matches(report, read_decisions(args.decisions))
        write_mapping_csv(args.output, result["mappings"])
        if args.summary:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
