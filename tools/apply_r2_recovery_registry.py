#!/usr/bin/env python3
"""Atomically reconcile verified R2 recoveries into the physical audit registry.

The audit registry remains the SSOT for the bytes currently materialized in the
catalog. Recovered R2 derivatives are never allowed to impersonate the missing
historical originals: the current ``sha256`` is replaced only after the physical
file re-verifies against the canonical recovery manifest, while the previous
registry hash is preserved as ``historical_sha256`` lineage.

Dry-run is the default. ``--apply`` performs one atomic CSV replacement after
all recovery rows and physical files pass validation. The operation is
idempotent and fails closed on partial/ambiguous state.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from ingest_catalog import PUBLISHABLE_AUDIT_STATUS_RE
from verify_r2_recovery import (
    EXPECTED_PROVENANCE,
    RecoveryVerificationError,
    load_json,
    safe_local_path,
    sha256_file,
    validate_manifest,
)


RECOVERY_COLUMNS = (
    "historical_sha256",
    "provenance",
    "source_bucket",
    "source_r2_key",
    "recovery_model_id",
    "recovery_size",
)


class RecoveryRegistryError(RuntimeError):
    pass


def normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    return unicodedata.normalize("NFKC", normalized).casefold()


def sha256_path(path: Path) -> str:
    return sha256_file(path)


def load_verified_provenance(
    path: Path,
    *,
    manifest_payload: Any,
    root: Path,
) -> list[dict[str, Any]]:
    bucket, manifest_entries = validate_manifest(manifest_payload)
    expected = {entry["modelId"]: entry for entry in manifest_entries}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise RecoveryRegistryError(f"provenance não encontrada: {path}") from exc

    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecoveryRegistryError(
                f"JSONL de provenance inválido na linha {line_no}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise RecoveryRegistryError(f"provenance linha {line_no} não é objeto")
        model_id = str(raw.get("modelId") or "").strip()
        if not model_id or model_id in seen:
            raise RecoveryRegistryError(
                f"modelId ausente/duplicado na provenance: {model_id!r}"
            )
        entry = expected.get(model_id)
        if entry is None:
            raise RecoveryRegistryError(f"modelId fora do manifesto canônico: {model_id}")
        seen.add(model_id)

        relative_path = str(raw.get("relativePath") or "").strip()
        source_bucket = str(raw.get("sourceBucket") or "").strip()
        source_r2_key = str(raw.get("sourceR2Key") or "").strip()
        provenance = str(raw.get("provenance") or "").strip()
        digest = str(raw.get("sha256") or "").strip().lower()
        size = raw.get("size")
        if source_bucket != bucket:
            raise RecoveryRegistryError(
                f"bucket divergente na provenance para {model_id}: {source_bucket!r}"
            )
        if source_r2_key != entry["r2Key"]:
            raise RecoveryRegistryError(
                f"r2Key divergente na provenance para {model_id}"
            )
        if provenance != EXPECTED_PROVENANCE:
            raise RecoveryRegistryError(
                f"provenance divergente para {model_id}: {provenance!r}"
            )
        if digest != entry["sha256"] or size != entry["expectedSize"]:
            raise RecoveryRegistryError(
                f"SHA/tamanho divergente na provenance para {model_id}"
            )

        physical, canonical_relative = safe_local_path(root, relative_path)
        if not physical.is_file():
            raise RecoveryRegistryError(
                f"arquivo recuperado ausente para {model_id}: {canonical_relative}"
            )
        stat = physical.stat()
        actual_digest = sha256_path(physical)
        if stat.st_size != entry["expectedSize"] or actual_digest != entry["sha256"]:
            raise RecoveryRegistryError(
                f"arquivo físico divergiu após verifier para {model_id}: {canonical_relative}"
            )

        rows.append(
            {
                "modelId": model_id,
                "relativePath": canonical_relative,
                "sha256": actual_digest,
                "size": stat.st_size,
                "sourceBucket": bucket,
                "sourceR2Key": entry["r2Key"],
                "provenance": EXPECTED_PROVENANCE,
            }
        )

    missing = sorted(set(expected) - seen)
    extra = sorted(seen - set(expected))
    if missing or extra or len(rows) != len(expected):
        raise RecoveryRegistryError(
            f"cobertura da provenance divergente: missing={missing} extra={extra}"
        )
    return sorted(rows, key=lambda row: row["modelId"])


def read_registry(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise RecoveryRegistryError(f"registro de auditoria não encontrado: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        required = {"caminho", "sha256", "status"}
        if not required.issubset(fieldnames):
            raise RecoveryRegistryError(
                f"registro sem colunas obrigatórias {sorted(required)}: {path}"
            )
        rows = [dict(row) for row in reader]
    if not rows:
        raise RecoveryRegistryError(f"registro de auditoria vazio: {path}")
    return fieldnames, rows


def plan_registry_patch(
    fieldnames: list[str],
    rows: list[dict[str, str]],
    provenance_rows: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, str]], dict[str, Any]]:
    path_index: dict[str, int] = {}
    for index, row in enumerate(rows):
        rel = str(row.get("caminho") or "").strip()
        if not rel:
            raise RecoveryRegistryError(f"caminho vazio no registro, linha {index + 2}")
        key = normalize_relative_path(rel)
        if key in path_index:
            raise RecoveryRegistryError(f"caminho duplicado no registro: {rel}")
        path_index[key] = index

    output_fields = list(fieldnames)
    for column in RECOVERY_COLUMNS:
        if column not in output_fields:
            output_fields.append(column)

    patched = [dict(row) for row in rows]
    pending = 0
    already_applied = 0
    changes: list[dict[str, Any]] = []

    for recovery in provenance_rows:
        model_id = recovery["modelId"]
        key = normalize_relative_path(recovery["relativePath"])
        row_index = path_index.get(key)
        if row_index is None:
            raise RecoveryRegistryError(
                f"destino recuperado não existe no registro auditado: {recovery['relativePath']}"
            )
        row = patched[row_index]
        status = str(row.get("status") or "").strip()
        if not PUBLISHABLE_AUDIT_STATUS_RE.fullmatch(status):
            raise RecoveryRegistryError(
                f"linha recuperada não está publicável no registro: {recovery['relativePath']} ({status!r})"
            )

        current = str(row.get("sha256") or "").strip().lower()
        if len(current) != 64 or any(char not in "0123456789abcdef" for char in current):
            raise RecoveryRegistryError(
                f"SHA atual inválido no registro: {recovery['relativePath']}"
            )
        recovered = recovery["sha256"]
        existing_historical = str(row.get("historical_sha256") or "").strip().lower()
        existing_provenance = str(row.get("provenance") or "").strip()
        existing_bucket = str(row.get("source_bucket") or "").strip()
        existing_key = str(row.get("source_r2_key") or "").strip()
        existing_model = str(row.get("recovery_model_id") or "").strip()
        existing_size = str(row.get("recovery_size") or "").strip()

        expected_metadata = {
            "provenance": recovery["provenance"],
            "source_bucket": recovery["sourceBucket"],
            "source_r2_key": recovery["sourceR2Key"],
            "recovery_model_id": model_id,
            "recovery_size": str(recovery["size"]),
        }
        existing_metadata = {
            "provenance": existing_provenance,
            "source_bucket": existing_bucket,
            "source_r2_key": existing_key,
            "recovery_model_id": existing_model,
            "recovery_size": existing_size,
        }
        for column, value in existing_metadata.items():
            if value and value != expected_metadata[column]:
                raise RecoveryRegistryError(
                    f"metadata de recuperação conflitante em {recovery['relativePath']}: {column}"
                )

        if current == recovered:
            if (
                len(existing_historical) != 64
                or any(char not in "0123456789abcdef" for char in existing_historical)
                or existing_historical == recovered
            ):
                raise RecoveryRegistryError(
                    f"registro já contém SHA recuperado sem lineage histórica válida: {recovery['relativePath']}"
                )
            historical = existing_historical
            state = "already_applied"
            already_applied += 1
        else:
            if existing_historical and existing_historical != current:
                raise RecoveryRegistryError(
                    f"lineage histórica diverge do SHA atual em {recovery['relativePath']}"
                )
            historical = existing_historical or current
            state = "pending"
            pending += 1

        row["sha256"] = recovered
        row["historical_sha256"] = historical
        for column, value in expected_metadata.items():
            row[column] = value
        changes.append(
            {
                "modelId": model_id,
                "path": recovery["relativePath"],
                "state": state,
                "historicalSha256": historical,
                "currentSha256": recovered,
            }
        )

    summary = {
        "version": 1,
        "recoveryRows": len(provenance_rows),
        "pendingUpdates": pending,
        "alreadyApplied": already_applied,
        "completeCoverage": len(changes) == len(provenance_rows),
        "changes": changes,
    }
    return output_fields, patched, summary


def write_registry_atomic(
    path: Path,
    *,
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> None:
    temp = path.parent / f".{path.name}.recovery-{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def apply_registry_recovery(
    *,
    registry: Path,
    manifest_payload: Any,
    provenance_path: Path,
    root: Path,
    apply: bool,
) -> dict[str, Any]:
    provenance_rows = load_verified_provenance(
        provenance_path,
        manifest_payload=manifest_payload,
        root=root,
    )
    fieldnames, rows = read_registry(registry)
    output_fields, patched, summary = plan_registry_patch(
        fieldnames, rows, provenance_rows
    )
    before_sha = sha256_path(registry)
    result = {
        **summary,
        "applied": False,
        "registryBeforeSha256": before_sha,
        "registryAfterSha256": before_sha,
        "registryWritten": False,
    }
    if not apply:
        return result
    if summary["pendingUpdates"] == 0:
        return {**result, "applied": True}

    write_registry_atomic(registry, fieldnames=output_fields, rows=patched)
    after_sha = sha256_path(registry)

    # Re-read and re-plan to prove the atomic replacement is complete and idempotent.
    verify_fields, verify_rows = read_registry(registry)
    _fields, _rows, verify_summary = plan_registry_patch(
        verify_fields, verify_rows, provenance_rows
    )
    if verify_summary["pendingUpdates"] != 0 or verify_summary["alreadyApplied"] != len(provenance_rows):
        raise RecoveryRegistryError("registro não ficou idempotente após aplicação")

    return {
        **verify_summary,
        "applied": True,
        "registryBeforeSha256": before_sha,
        "registryAfterSha256": after_sha,
        "registryWritten": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Atualiza atomicamente o registro auditado após recuperação R2 verificada."
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="efetiva a troca atômica do CSV; sem esta flag o comando é dry-run",
    )
    args = parser.parse_args()

    try:
        result = apply_registry_recovery(
            registry=args.registry.resolve(),
            manifest_payload=load_json(args.manifest),
            provenance_path=args.provenance.resolve(),
            root=args.root.resolve(),
            apply=args.apply,
        )
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except (RecoveryRegistryError, RecoveryVerificationError, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
