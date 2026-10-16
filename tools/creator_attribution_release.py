from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CREATOR_ATTRIBUTION = "creator_attribution"


def _identity_distinctness_is_proven(identity: dict[str, Any]) -> bool:
    if identity.get("allCandidatesVisuallyDistinct") is True:
        return True
    if identity.get("allCandidatesSemanticallyDistinct") is not True:
        return False
    return str(identity.get("semanticDistinctnessReason") or "").strip() == "different_franchise"


def validate_creator_attribution_proof(
    data: dict[str, Any],
    descriptor: dict[str, Any],
    creator_rows: list[dict[str, Any]],
) -> dict[int, set[str]]:
    if not creator_rows:
        return {}
    if data.get("version") != 1:
        raise RuntimeError("versão da prova de creator_attribution inválida")
    if str(data.get("sourceId") or "").strip() != str(descriptor.get("sourceId") or "").strip():
        raise RuntimeError("sourceId da prova de creator_attribution diverge do batch")
    product_id = int(data.get("sourceProductId") or 0)
    row_product_ids = {int(row.get("productId") or 0) for row in creator_rows}
    if product_id < 1 or row_product_ids != {product_id}:
        raise RuntimeError("prova de creator_attribution não cobre exatamente o produto revisado")
    creator = str(data.get("creator") or "").strip()
    if not creator or data.get("creatorCorroborated") is not True or data.get("visualAuditComplete") is not True:
        raise RuntimeError("creator_attribution sem autoria corroborada/auditoria visual completa")
    if str(data.get("watermarkStatus") or "").strip() != CREATOR_ATTRIBUTION:
        raise RuntimeError("watermarkStatus da prova não é creator_attribution")
    identity = data.get("identityReview") or {}
    if (
        identity.get("decision") != "new_model"
        or int(identity.get("exactProductionShaOwners") or 0) != 0
        or int(identity.get("productionCandidatesReviewed") or 0) < 1
        or not _identity_distinctness_is_proven(identity)
    ):
        raise RuntimeError("prova de identidade não autoriza novo modelo")
    decision = data.get("decision") or {}
    if (
        decision.get("newModelAuthorized") is not True
        or decision.get("creatorAttributionPromotable") is not True
        or int(decision.get("productionWrites") or 0) != 0
    ):
        raise RuntimeError("decisão de creator_attribution não autoriza promoção segura")
    proof_shas = {str(value).strip().lower() for value in (data.get("sourceSha256") or []) if str(value).strip()}
    row_shas = {str(row.get("sha256") or "").strip().lower() for row in creator_rows}
    if not proof_shas or proof_shas != row_shas:
        raise RuntimeError("SHAs da prova de creator_attribution divergem da evidência do batch")
    if len(proof_shas) != len(creator_rows):
        raise RuntimeError("prova de creator_attribution contém SHA duplicado")
    return {product_id: proof_shas}


def load_creator_attribution_authorizations(
    repo_root: Path,
    descriptor: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[int, set[str]]:
    creator_rows = [row for row in rows if str(row.get("review") or "").strip() == CREATOR_ATTRIBUTION]
    if not creator_rows:
        return {}
    relative = str(descriptor.get("creatorAttributionEvidence") or "").strip()
    if not relative:
        raise RuntimeError("batch com creator_attribution exige creatorAttributionEvidence")
    proof_path = (repo_root / relative).resolve()
    if repo_root not in proof_path.parents or not proof_path.is_file():
        raise RuntimeError("creatorAttributionEvidence ausente ou fora do repositório")
    try:
        data = json.loads(proof_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"creatorAttributionEvidence inválida: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("creatorAttributionEvidence deve ser objeto JSON")
    return validate_creator_attribution_proof(data, descriptor, creator_rows)
