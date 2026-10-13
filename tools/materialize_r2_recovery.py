#!/usr/bin/env python3
"""Materialize audited R2 recovery bytes into the physical catalog SSOT.

This tool performs no network access. The operator first downloads each audited
R2 ``detail.webp`` into an incoming directory using the filename
``<modelId>.webp``. This command verifies all incoming bytes against the
versioned recovery manifest, validates the exact target mapping, then (only with
``--apply``) writes missing targets monotonically without overwriting an
existing different file.

The operation is deliberately idempotent: a target that already matches the
audited size/SHA is accepted; a conflicting target fails closed. Provenance is
emitted only after every target verifies against the canonical manifest.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from verify_r2_recovery import (
    RecoveryVerificationError,
    load_json,
    safe_local_path,
    sha256_file,
    validate_manifest,
    validate_mapping,
    verify_materialization,
    write_jsonl,
)


class RecoveryMaterializationError(RuntimeError):
    pass


def verify_incoming_file(source: Path, entry: dict[str, Any]) -> None:
    model_id = entry["modelId"]
    if not source.is_file():
        raise RecoveryMaterializationError(
            f"arquivo de entrada ausente para {model_id}: {source.name}"
        )
    stat = source.stat()
    if stat.st_size != entry["expectedSize"]:
        raise RecoveryMaterializationError(
            f"tamanho de entrada divergente para {model_id}: "
            f"esperado={entry['expectedSize']} atual={stat.st_size}"
        )
    digest = sha256_file(source)
    if digest != entry["sha256"]:
        raise RecoveryMaterializationError(
            f"SHA-256 de entrada divergente para {model_id}: "
            f"esperado={entry['sha256']} atual={digest}"
        )


def prepare_materialization(
    manifest_payload: Any,
    mapping_payload: Any,
    *,
    incoming_root: Path,
    target_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _bucket, entries = validate_manifest(manifest_payload)
    expected_models = {entry["modelId"] for entry in entries}
    mapping = validate_mapping(mapping_payload, expected_models)

    plan: list[dict[str, Any]] = []
    for entry in entries:
        model_id = entry["modelId"]
        source, source_relative = safe_local_path(
            incoming_root, f"{model_id}.webp"
        )
        verify_incoming_file(source, entry)

        target, target_relative = safe_local_path(target_root, mapping[model_id])
        state = "missing"
        if target.exists():
            if not target.is_file():
                raise RecoveryMaterializationError(
                    f"destino existente não é arquivo para {model_id}: {target_relative}"
                )
            stat = target.stat()
            digest = sha256_file(target)
            if stat.st_size != entry["expectedSize"] or digest != entry["sha256"]:
                raise RecoveryMaterializationError(
                    f"destino conflitante para {model_id}: {target_relative}"
                )
            state = "already_verified"
        else:
            parent = target.parent
            cursor = parent
            root_resolved = target_root.resolve()
            while cursor != root_resolved:
                if cursor.exists() and not cursor.is_dir():
                    raise RecoveryMaterializationError(
                        f"ancestral de destino não é diretório para {model_id}: {cursor}"
                    )
                cursor = cursor.parent

        plan.append(
            {
                "modelId": model_id,
                "source": source,
                "sourceRelativePath": source_relative,
                "target": target,
                "targetRelativePath": target_relative,
                "state": state,
                "expectedSize": entry["expectedSize"],
                "sha256": entry["sha256"],
            }
        )

    plan.sort(key=lambda item: item["modelId"])
    summary = {
        "version": 1,
        "expected": len(entries),
        "incomingVerified": len(plan),
        "alreadyVerified": sum(1 for item in plan if item["state"] == "already_verified"),
        "pendingCopies": sum(1 for item in plan if item["state"] == "missing"),
        "networkReads": 0,
        "writesToTargetRoot": 0,
    }
    return plan, summary


def copy_without_overwrite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{target.name}.recovery-{uuid.uuid4().hex}.tmp"
    try:
        with source.open("rb") as src, temp.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())

        # os.link is atomic with respect to target creation and refuses to
        # overwrite an existing destination. Some virtual/cloud filesystems
        # (for example DriveFS on Windows) do not support hard links. In that
        # specific capability case, fall back to exclusive target creation;
        # the caller validates size/SHA immediately after this function.
        try:
            os.link(temp, target)
        except FileExistsError:
            raise
        except OSError as exc:
            unsupported_link = (
                exc.errno in {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP}
                or getattr(exc, "winerror", None) == 1
            )
            if not unsupported_link:
                raise
            try:
                with temp.open("rb") as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
            except BaseException:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
                raise
    except FileExistsError as exc:
        raise RecoveryMaterializationError(
            f"destino surgiu durante materialização e não foi sobrescrito: {target}"
        ) from exc
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def materialize(
    manifest_payload: Any,
    mapping_payload: Any,
    *,
    incoming_root: Path,
    target_root: Path,
    apply: bool,
    provenance_output: Path | None = None,
) -> dict[str, Any]:
    plan, summary = prepare_materialization(
        manifest_payload,
        mapping_payload,
        incoming_root=incoming_root,
        target_root=target_root,
    )

    if not apply:
        return {**summary, "applied": False}

    copied = 0
    for item in plan:
        if item["state"] == "already_verified":
            continue
        copy_without_overwrite(item["source"], item["target"])
        stat = item["target"].stat()
        digest = sha256_file(item["target"])
        if stat.st_size != item["expectedSize"] or digest != item["sha256"]:
            try:
                item["target"].unlink()
            except OSError:
                pass
            raise RecoveryMaterializationError(
                f"destino não validou após cópia para {item['modelId']}"
            )
        copied += 1

    rows, verified_summary = verify_materialization(
        manifest_payload,
        mapping_payload,
        root=target_root,
    )
    if not verified_summary.get("completeCoverage"):
        raise RecoveryMaterializationError("cobertura física final incompleta")
    if provenance_output:
        write_jsonl(provenance_output, rows)

    return {
        **summary,
        "applied": True,
        "copied": copied,
        "writesToTargetRoot": copied,
        "completeCoverage": True,
        "verified": verified_summary["verified"],
        "provenance": verified_summary["provenance"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materializa recuperações R2 auditadas no SSOT físico sem rede nem overwrite."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--incoming-root", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--provenance-output", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="efetiva cópias após preflight; sem esta flag o comando é dry-run",
    )
    args = parser.parse_args()

    try:
        summary = materialize(
            load_json(args.manifest),
            load_json(args.mapping),
            incoming_root=args.incoming_root,
            target_root=args.root,
            apply=args.apply,
            provenance_output=args.provenance_output,
        )
    except (RecoveryMaterializationError, RecoveryVerificationError, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
