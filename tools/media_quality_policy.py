#!/usr/bin/env python3
"""Shared media-quality policy used by catalog reconciliation gates.

Watermark state is intentionally explicit. External media without a conclusive
visual audit remains unknown and cannot be promoted. Legacy/internal manifests
created before this contract keep their previous accepted-baseline behavior.
"""
from __future__ import annotations

from typing import Any

AUTHORIZED_EXTERNAL_PROVENANCE = "AUTHORIZED_EXTERNAL_MEDIA"
WATERMARK_CLEAR = "clear"
WATERMARK_WATERMARKED = "watermarked"
WATERMARK_UNKNOWN = "unknown"
WATERMARK_LEGACY_ACCEPTED = "legacy_accepted"

AUDITED_WATERMARK_STATUSES = frozenset({WATERMARK_CLEAR, WATERMARK_WATERMARKED})
MANIFEST_WATERMARK_STATUSES = frozenset(
    {WATERMARK_CLEAR, WATERMARK_WATERMARKED, WATERMARK_UNKNOWN}
)
PROMOTABLE_WATERMARK_STATUSES = frozenset(
    {WATERMARK_CLEAR, WATERMARK_LEGACY_ACCEPTED}
)
COMPACT_WATERMARK_STATUSES = MANIFEST_WATERMARK_STATUSES | {
    WATERMARK_LEGACY_ACCEPTED
}


class MediaQualityPolicyError(RuntimeError):
    pass


def audited_watermark_status(value: Any) -> str:
    """Validate a conclusive visual-audit result."""
    status = str(value or "").strip().lower()
    if status not in AUDITED_WATERMARK_STATUSES:
        raise MediaQualityPolicyError(
            "watermarkStatus inválido; use 'clear' ou 'watermarked'"
        )
    return status


def manifest_watermark_status(row: dict[str, Any]) -> str:
    """Resolve watermark state from a manifest row without weakening legacy flows.

    External provenance is fail-closed: absence of the explicit field means
    `unknown`. Older canonical/internal manifests lack the field entirely and
    retain the prior accepted-baseline semantics as `legacy_accepted`.
    """
    raw = row.get("watermark_status")
    if raw is None or str(raw).strip() == "":
        provenance = row.get("source_provenance")
        if (
            isinstance(provenance, dict)
            and str(provenance.get("kind") or "").strip()
            == AUTHORIZED_EXTERNAL_PROVENANCE
        ):
            return WATERMARK_UNKNOWN
        return WATERMARK_LEGACY_ACCEPTED

    status = str(raw).strip().lower()
    if status not in MANIFEST_WATERMARK_STATUSES:
        raise MediaQualityPolicyError(
            f"watermark_status inválido: {row.get('path', '<sem caminho>')} ({status!r})"
        )
    return status


def compact_watermark_status(value: Any) -> str:
    """Validate watermark state carried by a compact gallery-plan image."""
    status = str(value or WATERMARK_LEGACY_ACCEPTED).strip().lower()
    if status not in COMPACT_WATERMARK_STATUSES:
        raise MediaQualityPolicyError(
            f"watermarkStatus inválido na resolução: {status!r}"
        )
    return status
