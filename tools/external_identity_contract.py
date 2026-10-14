#!/usr/bin/env python3
"""Shared fail-closed contract for external identity review artifacts.

The candidate report is the evidence source of truth. A resolved identity mapping
is accepted only when every row is bound to the exact candidate report digest and
the selected target was present among that product's reviewed candidates.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

CANDIDATE_REPORT_VERSION = 1
MAPPING_FIELDS = [
    "incoming_model",
    "target_model",
    "candidate_report_sha256",
    "decision_reason",
]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_candidate_report(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("version") != CANDIDATE_REPORT_VERSION:
        raise RuntimeError("contrato de candidatos inválido")
    policy = payload.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("reviewOnly") is not True
        or policy.get("automaticMappings") != 0
    ):
        raise RuntimeError("relatório de candidatos não preserva política review-only")
    rows = payload.get("products")
    if not isinstance(rows, list):
        raise RuntimeError("relatório de candidatos sem products")

    products: dict[str, dict[str, Any]] = {}
    for product in rows:
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

        targets: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise RuntimeError(f"candidato inválido para {incoming}")
            target = str(candidate.get("targetModel") or "").strip()
            if not target:
                raise RuntimeError(f"candidato sem targetModel para {incoming}")
            if target in targets:
                raise RuntimeError(f"targetModel repetido nos candidatos de {incoming}: {target}")
            targets.add(target)
        products[incoming] = product

    if not products:
        raise RuntimeError("relatório de candidatos sem produtos")
    return products


def read_candidate_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"relatório de candidatos não encontrado: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"relatório de candidatos inválido: {path}: {exc}") from exc
    validate_candidate_report(payload)
    return payload



def _normalize_mapping_row(row: Any, *, context: str) -> dict[str, str]:
    if not isinstance(row, dict):
        raise RuntimeError(f"linha de mapping inválida em {context}")
    incoming = str(row.get("incoming_model") or "").strip()
    target = str(row.get("target_model") or "").strip()
    digest = str(row.get("candidate_report_sha256") or "").strip().lower()
    reason = str(row.get("decision_reason") or "").strip()
    if not incoming or not target or not digest or not reason:
        raise RuntimeError(f"mapping incompleto em {context}")
    if not SHA256_RE.fullmatch(digest):
        raise RuntimeError(f"candidate_report_sha256 inválido em {context}: {digest!r}")
    return {
        "incoming_model": incoming,
        "target_model": target,
        "candidate_report_sha256": digest,
        "decision_reason": reason,
    }


def validate_identity_resolution(
    payload: Any,
    candidate_report: dict[str, Any],
) -> dict[str, dict[str, str]]:
    products = validate_candidate_report(candidate_report)
    report_digest = canonical_sha256(candidate_report)
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("ready") is not True:
        raise RuntimeError("contrato de resolução de identidade inválido")
    if str(payload.get("candidateReportSha256") or "").strip().lower() != report_digest:
        raise RuntimeError("resolução de identidade pertence a outro relatório de candidatos")

    policy = payload.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("mapExistingMustSelectReportedCandidate") is not True
        or policy.get("newModelRequiresExplicitDecision") is not True
        or policy.get("oneIncomingPerExistingTarget") is not True
        or policy.get("automaticMappings") != 0
        or policy.get("catalogWrites") != 0
        or policy.get("mediaWrites") != 0
    ):
        raise RuntimeError("resolução de identidade não preserva política fail-closed")

    mapping_rows = payload.get("mappings")
    new_model_rows = payload.get("newModels")
    if not isinstance(mapping_rows, list) or not isinstance(new_model_rows, list):
        raise RuntimeError("resolução de identidade sem mappings/newModels válidos")

    mappings: dict[str, dict[str, str]] = {}
    targets: dict[str, str] = {}
    resolved: set[str] = set()
    for index, raw in enumerate(mapping_rows, 1):
        row = _normalize_mapping_row(raw, context=f"identity-resolution mappings[{index}]")
        incoming = row["incoming_model"]
        target = row["target_model"]
        if row["candidate_report_sha256"] != report_digest:
            raise RuntimeError(
                f"mapping da resolução está ligado a outro relatório de candidatos: {incoming}"
            )
        product = products.get(incoming)
        if product is None:
            raise RuntimeError(f"mapping da resolução possui origem desconhecida: {incoming}")
        candidate_targets = {
            str(candidate.get("targetModel") or "").strip()
            for candidate in product["candidates"]
        }
        if target not in candidate_targets:
            raise RuntimeError(
                f"target_model não pertence aos candidatos auditados de {incoming}: {target!r}"
            )
        if incoming in resolved:
            raise RuntimeError(f"identidade resolvida mais de uma vez: {incoming}")
        previous = targets.get(target)
        if previous and previous != incoming:
            raise RuntimeError(
                f"produtos diferentes apontam para o mesmo modelo público: "
                f"{previous!r}, {incoming!r} -> {target!r}"
            )
        mappings[incoming] = row
        targets[target] = incoming
        resolved.add(incoming)

    for index, row in enumerate(new_model_rows, 1):
        if not isinstance(row, dict):
            raise RuntimeError(f"newModels[{index}] inválido na resolução de identidade")
        incoming = str(row.get("incomingModel") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not incoming or not reason:
            raise RuntimeError(f"newModels[{index}] incompleto na resolução de identidade")
        if incoming not in products:
            raise RuntimeError(f"new_model da resolução possui origem desconhecida: {incoming}")
        if incoming in resolved:
            raise RuntimeError(f"identidade resolvida mais de uma vez: {incoming}")
        resolved.add(incoming)

    expected_products = set(products)
    if resolved != expected_products:
        missing = sorted(expected_products - resolved, key=str.casefold)
        extra = sorted(resolved - expected_products, key=str.casefold)
        raise RuntimeError(
            f"resolução não cobre exatamente o relatório: ausentes={missing[:5]} extras={extra[:5]}"
        )

    counts = payload.get("counts")
    if not isinstance(counts, dict):
        raise RuntimeError("resolução de identidade sem counts")
    expected_counts = {
        "incomingProducts": len(products),
        "mappedExisting": len(mappings),
        "newModels": len(new_model_rows),
        "automaticMappings": 0,
    }
    for field, expected in expected_counts.items():
        if counts.get(field) != expected:
            raise RuntimeError(
                f"counts divergente na resolução de identidade: {field}={counts.get(field)!r}, esperado={expected}"
            )
    return mappings


def read_identity_resolution(
    path: Path,
    candidate_report: dict[str, Any],
) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"resolução de identidade não encontrada: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"resolução de identidade inválida: {path}: {exc}") from exc
    validate_identity_resolution(payload, candidate_report)
    return payload


def read_identity_mapping(
    path: Path,
    candidate_report: dict[str, Any],
    identity_resolution: dict[str, Any],
) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"mapa de identidade não encontrado: {path}")

    expected_rows = validate_identity_resolution(identity_resolution, candidate_report)
    report_digest = canonical_sha256(candidate_report)
    mapping: dict[str, str] = {}
    seen_rows: set[str] = set()

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != MAPPING_FIELDS:
            raise RuntimeError(
                f"colunas do mapa de identidade divergentes: esperado {MAPPING_FIELDS}, "
                f"obtido {reader.fieldnames}"
            )
        for line_no, raw in enumerate(reader, 2):
            row = _normalize_mapping_row(raw, context=f"{path}, linha {line_no}")
            incoming = row["incoming_model"]
            if row["candidate_report_sha256"] != report_digest:
                raise RuntimeError(
                    f"mapa de identidade está ligado a outro relatório de candidatos: "
                    f"linha {line_no}: esperado {report_digest}, obtido {row['candidate_report_sha256']}"
                )
            expected = expected_rows.get(incoming)
            if expected is None:
                raise RuntimeError(
                    f"mapping CSV não pertence à resolução auditada: origem {incoming!r}"
                )
            if row != expected:
                raise RuntimeError(
                    f"mapping CSV diverge da resolução auditada para {incoming}: "
                    f"esperado target={expected['target_model']!r} reason={expected['decision_reason']!r}"
                )
            if incoming in seen_rows:
                raise RuntimeError(f"origem duplicada no mapa de identidade: {incoming}")
            seen_rows.add(incoming)
            mapping[incoming] = row["target_model"]

    expected_incoming = set(expected_rows)
    if seen_rows != expected_incoming:
        missing = sorted(expected_incoming - seen_rows, key=str.casefold)
        extra = sorted(seen_rows - expected_incoming, key=str.casefold)
        raise RuntimeError(
            f"mapping CSV não corresponde exatamente à resolução: ausentes={missing[:5]} extras={extra[:5]}"
        )
    if not mapping:
        raise RuntimeError(f"mapa de identidade vazio: {path}")
    return mapping
