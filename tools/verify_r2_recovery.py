#!/usr/bin/env python3
"""Verify materialized R2 recovery files before they enter the physical SSOT.

The tool is deliberately network-free and copy-free. It validates an operator-provided
mapping from model IDs to files already placed under an authorized local root against
a versioned recovery manifest. Only exact model coverage, bounded relative paths,
byte size and SHA-256 matches are accepted. A provenance JSONL can then be emitted for
the ingestion audit trail.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_PROVENANCE = "RECOVERED_FROM_R2_DETAIL"


class RecoveryVerificationError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RecoveryVerificationError(f"arquivo não encontrado: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RecoveryVerificationError(f"JSON inválido em {path}: {exc}") from exc


def validate_manifest(payload: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RecoveryVerificationError("manifesto de recuperação inválido: version deve ser 1")
    bucket = str(payload.get("bucket") or "").strip()
    provenance = str(payload.get("provenance") or "").strip()
    entries = payload.get("entries")
    if not bucket:
        raise RecoveryVerificationError("manifesto de recuperação sem bucket")
    if provenance != EXPECTED_PROVENANCE:
        raise RecoveryVerificationError(
            f"provenance inválida: esperado {EXPECTED_PROVENANCE}, recebido {provenance!r}"
        )
    if not isinstance(entries, list) or not entries:
        raise RecoveryVerificationError("manifesto de recuperação sem entries")

    seen_models: set[str] = set()
    seen_keys: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(entries, 1):
        if not isinstance(raw, dict):
            raise RecoveryVerificationError(f"entry {index} não é objeto")
        model_id = str(raw.get("modelId") or "").strip()
        r2_key = str(raw.get("r2Key") or "").strip()
        sha256 = str(raw.get("sha256") or "").strip().lower()
        expected_size = raw.get("expectedSize")
        if not model_id or not r2_key:
            raise RecoveryVerificationError(f"entry {index} sem modelId/r2Key")
        if model_id in seen_models:
            raise RecoveryVerificationError(f"modelId duplicado no manifesto: {model_id}")
        if r2_key in seen_keys:
            raise RecoveryVerificationError(f"r2Key duplicada no manifesto: {r2_key}")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size <= 0:
            raise RecoveryVerificationError(f"expectedSize inválido para {model_id}")
        if not SHA256_RE.fullmatch(sha256):
            raise RecoveryVerificationError(f"sha256 inválido para {model_id}")
        if not r2_key.endswith("/detail.webp"):
            raise RecoveryVerificationError(f"recuperação deve usar detail.webp: {model_id}")
        seen_models.add(model_id)
        seen_keys.add(r2_key)
        normalized.append(
            {
                "modelId": model_id,
                "r2Key": r2_key,
                "expectedSize": expected_size,
                "sha256": sha256,
            }
        )
    return bucket, normalized


def validate_mapping(payload: Any, expected_models: set[str]) -> dict[str, str]:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RecoveryVerificationError("mapping local inválido: version deve ser 1")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise RecoveryVerificationError("mapping local sem entries")

    mapped: dict[str, str] = {}
    for index, raw in enumerate(entries, 1):
        if not isinstance(raw, dict):
            raise RecoveryVerificationError(f"mapping entry {index} não é objeto")
        model_id = str(raw.get("modelId") or "").strip()
        relative_path = str(raw.get("relativePath") or "").strip()
        if not model_id or not relative_path:
            raise RecoveryVerificationError(f"mapping entry {index} sem modelId/relativePath")
        if model_id in mapped:
            raise RecoveryVerificationError(f"modelId duplicado no mapping: {model_id}")
        mapped[model_id] = relative_path

    found_models = set(mapped)
    missing = sorted(expected_models - found_models)
    extra = sorted(found_models - expected_models)
    if missing or extra:
        raise RecoveryVerificationError(
            f"cobertura do mapping divergente: missing={missing} extra={extra}"
        )
    return mapped


def safe_local_path(root: Path, relative_path: str) -> tuple[Path, str]:
    normalized = relative_path.replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        raise RecoveryVerificationError(f"relativePath inválido: {relative_path!r}")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RecoveryVerificationError(f"relativePath inseguro: {relative_path!r}")
    # Reject drive-qualified Windows paths even when running tests on POSIX.
    if re.match(r"^[A-Za-z]:", normalized):
        raise RecoveryVerificationError(f"relativePath absoluto não permitido: {relative_path!r}")

    root_resolved = root.resolve()
    target = root.joinpath(*parts).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise RecoveryVerificationError(
            f"relativePath escapa da raiz autorizada: {relative_path!r}"
        ) from exc
    canonical_relative = target.relative_to(root_resolved).as_posix()
    return target, canonical_relative


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_materialization(
    manifest_payload: Any,
    mapping_payload: Any,
    *,
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bucket, entries = validate_manifest(manifest_payload)
    expected = {entry["modelId"] for entry in entries}
    mapping = validate_mapping(mapping_payload, expected)

    verified: list[dict[str, Any]] = []
    for entry in entries:
        model_id = entry["modelId"]
        path, canonical_relative = safe_local_path(root, mapping[model_id])
        if not path.is_file():
            raise RecoveryVerificationError(
                f"arquivo recuperado ausente para {model_id}: {canonical_relative}"
            )
        stat = path.stat()
        if stat.st_size != entry["expectedSize"]:
            raise RecoveryVerificationError(
                f"tamanho divergente para {model_id}: esperado={entry['expectedSize']} atual={stat.st_size}"
            )
        digest = sha256_file(path)
        if digest != entry["sha256"]:
            raise RecoveryVerificationError(
                f"SHA-256 divergente para {model_id}: esperado={entry['sha256']} atual={digest}"
            )
        verified.append(
            {
                "version": 1,
                "modelId": model_id,
                "relativePath": canonical_relative,
                "size": stat.st_size,
                "sha256": digest,
                "sourceBucket": bucket,
                "sourceR2Key": entry["r2Key"],
                "provenance": EXPECTED_PROVENANCE,
            }
        )

    verified.sort(key=lambda row: row["modelId"])
    summary = {
        "version": 1,
        "verified": len(verified),
        "expected": len(entries),
        "completeCoverage": len(verified) == len(entries),
        "provenance": EXPECTED_PROVENANCE,
        "networkReads": 0,
        "writesToSourceRoot": 0,
    }
    return verified, summary


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verifica recuperações R2 materializadas no SSOT físico sem rede nem cópia."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        rows, summary = verify_materialization(
            load_json(args.manifest),
            load_json(args.mapping),
            root=args.root,
        )
        if args.output:
            write_jsonl(args.output, rows)
    except (RecoveryVerificationError, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
