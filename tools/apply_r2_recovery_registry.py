#!/usr/bin/env python3
"""Reconcile verified R2 recoveries into the physical audit registry atomically."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
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
    "historical_caminho",
    "historical_sha256",
    "provenance",
    "source_bucket",
    "source_r2_key",
    "recovery_model_id",
    "recovery_size",
)
AUDIT_ROW_COLUMNS = ("codigo", "identificacao", "modelo_publico")
HEX = set("0123456789abcdef")


class RecoveryRegistryError(RuntimeError):
    pass


def normalize_relative_path(value: str) -> str:
    return unicodedata.normalize("NFKC", value.replace("\\", "/").strip()).casefold()


def validate_relative_text(value: str, label: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise RecoveryRegistryError(f"{label} inválido/absoluto: {value!r}")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RecoveryRegistryError(f"{label} inseguro: {value!r}")
    return normalized


def valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in HEX for char in value)


def sha256_path(path: Path) -> str:
    return sha256_file(path)


def load_verified_provenance(path: Path, *, manifest_payload: Any, root: Path) -> list[dict[str, Any]]:
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
            raise RecoveryRegistryError(f"JSONL de provenance inválido na linha {line_no}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RecoveryRegistryError(f"provenance linha {line_no} não é objeto")
        model_id = str(raw.get("modelId") or "").strip()
        if not model_id or model_id in seen:
            raise RecoveryRegistryError(f"modelId ausente/duplicado na provenance: {model_id!r}")
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
        if source_bucket != bucket or source_r2_key != entry["r2Key"]:
            raise RecoveryRegistryError(f"origem R2 divergente na provenance para {model_id}")
        if provenance != EXPECTED_PROVENANCE:
            raise RecoveryRegistryError(f"provenance divergente para {model_id}: {provenance!r}")
        if digest != entry["sha256"] or size != entry["expectedSize"]:
            raise RecoveryRegistryError(f"SHA/tamanho divergente na provenance para {model_id}")

        physical, canonical_relative = safe_local_path(root, relative_path)
        if not physical.is_file():
            raise RecoveryRegistryError(f"arquivo recuperado ausente para {model_id}: {canonical_relative}")
        stat = physical.stat()
        actual_digest = sha256_path(physical)
        if stat.st_size != entry["expectedSize"] or actual_digest != entry["sha256"]:
            raise RecoveryRegistryError(f"arquivo físico divergiu após verifier para {model_id}: {canonical_relative}")
        rows.append({
            "modelId": model_id,
            "relativePath": canonical_relative,
            "sha256": actual_digest,
            "size": stat.st_size,
            "sourceBucket": bucket,
            "sourceR2Key": entry["r2Key"],
            "provenance": EXPECTED_PROVENANCE,
        })

    missing = sorted(set(expected) - seen)
    extra = sorted(seen - set(expected))
    if missing or extra or len(rows) != len(expected):
        raise RecoveryRegistryError(f"cobertura da provenance divergente: missing={missing} extra={extra}")
    return sorted(rows, key=lambda row: row["modelId"])


def load_recovery_mapping(payload: Any | None, *, expected_models: set[str]) -> dict[str, dict[str, Any]]:
    if payload is None:
        return {}
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RecoveryRegistryError("mapping de recuperação inválido: version deve ser 1")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise RecoveryRegistryError("mapping de recuperação sem entries")

    result: dict[str, dict[str, Any]] = {}
    seen_paths: set[str] = set()
    for index, raw in enumerate(entries, 1):
        if not isinstance(raw, dict):
            raise RecoveryRegistryError(f"mapping entry {index} não é objeto")
        model_id = str(raw.get("modelId") or "").strip()
        relative_path = str(raw.get("relativePath") or "").strip()
        if not model_id or not relative_path:
            raise RecoveryRegistryError(f"mapping entry {index} sem modelId/relativePath")
        validate_relative_text(relative_path, "relativePath")
        if Path(relative_path.replace("\\", "/")).suffix.lower() != ".webp":
            raise RecoveryRegistryError(f"destino recuperado deve ser .webp: {model_id}")
        if model_id in result:
            raise RecoveryRegistryError(f"modelId duplicado no mapping: {model_id}")
        normalized_path = normalize_relative_path(relative_path)
        if normalized_path in seen_paths:
            raise RecoveryRegistryError(f"relativePath duplicado no mapping: {relative_path}")
        seen_paths.add(normalized_path)

        audit_row = raw.get("auditRow")
        normalized_audit: dict[str, str] | None = None
        if audit_row is not None:
            if not isinstance(audit_row, dict):
                raise RecoveryRegistryError(f"auditRow inválido no mapping para {model_id}")
            historical_path = str(audit_row.get("historicalPath") or "").strip()
            historical = str(audit_row.get("historicalSha256") or "").strip().lower()
            codigo = str(audit_row.get("codigo") or "").strip()
            identificacao = str(audit_row.get("identificacao") or "").strip()
            modelo_publico = str(audit_row.get("modelo_publico") or "").strip()
            status = str(audit_row.get("status") or "").strip()
            historical_path = validate_relative_text(historical_path, "historicalPath")
            if not valid_sha256(historical):
                raise RecoveryRegistryError(f"historicalSha256 inválido no mapping para {model_id}")
            if not codigo:
                raise RecoveryRegistryError(f"codigo auditado ausente no mapping para {model_id}")
            if not modelo_publico:
                raise RecoveryRegistryError(f"modelo_publico auditado ausente no mapping para {model_id}")
            if not PUBLISHABLE_AUDIT_STATUS_RE.fullmatch(status):
                raise RecoveryRegistryError(f"status auditado não publicável no mapping para {model_id}: {status!r}")
            normalized_audit = {
                "historical_caminho": historical_path,
                "historical_sha256": historical,
                "codigo": codigo,
                "identificacao": identificacao,
                "modelo_publico": modelo_publico,
                "status": status,
            }
        result[model_id] = {"relativePath": relative_path, "auditRow": normalized_audit}

    found = set(result)
    missing = sorted(expected_models - found)
    extra = sorted(found - expected_models)
    if missing or extra:
        raise RecoveryRegistryError(f"cobertura do mapping divergente: missing={missing} extra={extra}")
    return result


def read_registry(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise RecoveryRegistryError(f"registro de auditoria não encontrado: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        required = {"caminho", "sha256", "status"}
        if not required.issubset(fieldnames):
            raise RecoveryRegistryError(f"registro sem colunas obrigatórias {sorted(required)}: {path}")
        rows = [dict(row) for row in reader]
    if not rows:
        raise RecoveryRegistryError(f"registro de auditoria vazio: {path}")
    return fieldnames, rows


def verify_existing_row_against_mapping(row: dict[str, str], audit_row: dict[str, str] | None, *, relative_path: str) -> None:
    if audit_row is None:
        return
    for column in ("codigo", "identificacao", "modelo_publico", "status"):
        actual = str(row.get(column) or "").strip()
        expected = audit_row[column]
        if actual != expected:
            raise RecoveryRegistryError(
                f"metadata histórica diverge do mapping em {relative_path}: {column}={actual!r} esperado={expected!r}"
            )


def plan_registry_patch(
    fieldnames: list[str],
    rows: list[dict[str, str]],
    provenance_rows: list[dict[str, Any]],
    *,
    mapping: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[str], list[dict[str, str]], dict[str, Any]]:
    mapping = mapping or {}
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
    for column in (*AUDIT_ROW_COLUMNS, *RECOVERY_COLUMNS):
        if column not in output_fields:
            output_fields.append(column)
    patched = [dict(row) for row in rows]
    pending = already_applied = inserted = updated = moved = 0
    changes: list[dict[str, Any]] = []

    for recovery in provenance_rows:
        model_id = recovery["modelId"]
        target_key = normalize_relative_path(recovery["relativePath"])
        mapping_entry = mapping.get(model_id)
        if mapping_entry is not None and normalize_relative_path(str(mapping_entry["relativePath"])) != target_key:
            raise RecoveryRegistryError(f"caminho da provenance diverge do mapping para {model_id}")
        audit_row = mapping_entry.get("auditRow") if mapping_entry else None
        historical_key = normalize_relative_path(audit_row["historical_caminho"]) if audit_row else None
        target_index = path_index.get(target_key)
        historical_index = path_index.get(historical_key) if historical_key else None
        if target_index is not None and historical_index is not None and target_index != historical_index:
            raise RecoveryRegistryError(f"registro contém simultaneamente caminho histórico e recuperado para {model_id}")
        row_index = target_index if target_index is not None else historical_index
        found_via_historical = target_index is None and historical_index is not None

        expected_metadata = {
            "provenance": recovery["provenance"],
            "source_bucket": recovery["sourceBucket"],
            "source_r2_key": recovery["sourceR2Key"],
            "recovery_model_id": model_id,
            "recovery_size": str(recovery["size"]),
        }
        recovered = recovery["sha256"]

        if row_index is None:
            if audit_row is None:
                raise RecoveryRegistryError(
                    f"destino recuperado não existe no registry e mapping não fornece auditRow: {recovery['relativePath']}"
                )
            if audit_row["historical_sha256"] == recovered:
                raise RecoveryRegistryError(f"historicalSha256 não pode ser o SHA recuperado: {model_id}")
            new_row = {column: "" for column in output_fields}
            new_row.update({
                "codigo": audit_row["codigo"],
                "identificacao": audit_row["identificacao"],
                "modelo_publico": audit_row["modelo_publico"],
                "status": audit_row["status"],
                "caminho": recovery["relativePath"],
                "sha256": recovered,
                "historical_caminho": audit_row["historical_caminho"],
                "historical_sha256": audit_row["historical_sha256"],
                **expected_metadata,
            })
            patched.append(new_row)
            pending += 1
            inserted += 1
            changes.append({
                "modelId": model_id,
                "path": recovery["relativePath"],
                "state": "pending_insert",
                "historicalPath": audit_row["historical_caminho"],
                "historicalSha256": audit_row["historical_sha256"],
                "currentSha256": recovered,
            })
            continue

        row = patched[row_index]
        verify_existing_row_against_mapping(row, audit_row, relative_path=recovery["relativePath"])
        status = str(row.get("status") or "").strip()
        if not PUBLISHABLE_AUDIT_STATUS_RE.fullmatch(status):
            raise RecoveryRegistryError(f"linha recuperada não está publicável: {recovery['relativePath']} ({status!r})")
        current = str(row.get("sha256") or "").strip().lower()
        if not valid_sha256(current):
            raise RecoveryRegistryError(f"SHA atual inválido no registro: {recovery['relativePath']}")
        existing_historical_sha = str(row.get("historical_sha256") or "").strip().lower()
        existing_historical_path = str(row.get("historical_caminho") or "").strip()
        existing_metadata = {
            "provenance": str(row.get("provenance") or "").strip(),
            "source_bucket": str(row.get("source_bucket") or "").strip(),
            "source_r2_key": str(row.get("source_r2_key") or "").strip(),
            "recovery_model_id": str(row.get("recovery_model_id") or "").strip(),
            "recovery_size": str(row.get("recovery_size") or "").strip(),
        }
        for column, value in existing_metadata.items():
            if value and value != expected_metadata[column]:
                raise RecoveryRegistryError(f"metadata de recuperação conflitante em {recovery['relativePath']}: {column}")

        if current == recovered:
            missing_metadata = [column for column, value in existing_metadata.items() if not value]
            if missing_metadata:
                raise RecoveryRegistryError(
                    f"registro já contém SHA recuperado com provenance incompleta em {recovery['relativePath']}: {missing_metadata}"
                )
            if not valid_sha256(existing_historical_sha) or existing_historical_sha == recovered or not existing_historical_path:
                raise RecoveryRegistryError(f"registro recuperado sem lineage histórica completa: {recovery['relativePath']}")
            if audit_row and (
                existing_historical_sha != audit_row["historical_sha256"]
                or normalize_relative_path(existing_historical_path) != historical_key
            ):
                raise RecoveryRegistryError(f"lineage histórica diverge do mapping em {recovery['relativePath']}")
            state = "already_applied"
            already_applied += 1
            historical_sha = existing_historical_sha
            historical_path = existing_historical_path
        else:
            if audit_row and current != audit_row["historical_sha256"]:
                raise RecoveryRegistryError(f"SHA histórico atual diverge do mapping em {recovery['relativePath']}")
            if existing_historical_sha and existing_historical_sha != current:
                raise RecoveryRegistryError(f"lineage histórica diverge do SHA atual em {recovery['relativePath']}")
            historical_sha = existing_historical_sha or current
            historical_path = existing_historical_path or str(row.get("caminho") or "").strip()
            if audit_row and normalize_relative_path(historical_path) != historical_key:
                raise RecoveryRegistryError(f"caminho histórico diverge do mapping em {recovery['relativePath']}")
            state = "pending_move_update" if found_via_historical else "pending_update"
            pending += 1
            updated += 1
            moved += int(found_via_historical or normalize_relative_path(str(row.get("caminho") or "")) != target_key)

        row["caminho"] = recovery["relativePath"]
        row["sha256"] = recovered
        row["historical_caminho"] = historical_path
        row["historical_sha256"] = historical_sha
        for column, value in expected_metadata.items():
            row[column] = value
        changes.append({
            "modelId": model_id,
            "path": recovery["relativePath"],
            "state": state,
            "historicalPath": historical_path,
            "historicalSha256": historical_sha,
            "currentSha256": recovered,
        })

    return output_fields, patched, {
        "version": 1,
        "recoveryRows": len(provenance_rows),
        "pendingUpdates": pending,
        "pendingInserts": inserted,
        "pendingExistingRowUpdates": updated,
        "pendingPathMoves": moved,
        "alreadyApplied": already_applied,
        "completeCoverage": len(changes) == len(provenance_rows),
        "changes": changes,
    }


def write_registry_atomic(path: Path, *, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
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
    *, registry: Path, manifest_payload: Any, provenance_path: Path, root: Path,
    apply: bool, mapping_payload: Any | None = None,
) -> dict[str, Any]:
    provenance_rows = load_verified_provenance(provenance_path, manifest_payload=manifest_payload, root=root)
    mapping = load_recovery_mapping(mapping_payload, expected_models={row["modelId"] for row in provenance_rows})
    fieldnames, rows = read_registry(registry)
    output_fields, patched, summary = plan_registry_patch(fieldnames, rows, provenance_rows, mapping=mapping)
    before_sha = sha256_path(registry)
    result = {**summary, "applied": False, "registryBeforeSha256": before_sha,
              "registryAfterSha256": before_sha, "registryWritten": False}
    if not apply:
        return result
    if summary["pendingUpdates"] == 0:
        return {**result, "applied": True}

    write_registry_atomic(registry, fieldnames=output_fields, rows=patched)
    after_sha = sha256_path(registry)
    verify_fields, verify_rows = read_registry(registry)
    _fields, _rows, verify_summary = plan_registry_patch(verify_fields, verify_rows, provenance_rows, mapping=mapping)
    if verify_summary["pendingUpdates"] != 0 or verify_summary["alreadyApplied"] != len(provenance_rows):
        raise RecoveryRegistryError("registro não ficou idempotente após aplicação")
    return {**verify_summary, "applied": True, "registryBeforeSha256": before_sha,
            "registryAfterSha256": after_sha, "registryWritten": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Atualiza atomicamente o registro auditado após recuperação R2 verificada.")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, help="mapping local auditado; auditRow suporta historicalPath/lineage")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = apply_registry_recovery(
            registry=args.registry.resolve(), manifest_payload=load_json(args.manifest),
            provenance_path=args.provenance.resolve(), root=args.root.resolve(), apply=args.apply,
            mapping_payload=load_json(args.mapping) if args.mapping else None,
        )
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (RecoveryRegistryError, RecoveryVerificationError, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
