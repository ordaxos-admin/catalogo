#!/usr/bin/env python3
"""Run the catalog recovery closeout in one fail-closed sequence.

This orchestrator does not replace any canonical pipeline component. It invokes
the existing recovery, ingestion, bundle and production-read preflights in the
required order and writes one machine-readable report.

The only writes it can authorize are local SSOT recovery writes behind
``--apply-local``. R2 remains dry-run, D1 remains ``--check-production`` and
legacy gallery repairs remain plan-only. There is intentionally no flag here
that can publish or mutate Cloudflare production.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from build_media_bundle import MEDIA_METADATA_VERSION


class CloseoutError(RuntimeError):
    pass


def parse_last_json(stdout: str, step: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise CloseoutError(f"{step} não emitiu resumo JSON")


def run_step(
    repo_root: Path,
    name: str,
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.time()
    child_env = (env or os.environ.copy()).copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, *argv],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        env=child_env,
        check=False,
    )
    duration_ms = round((time.time() - started) * 1000)
    if completed.returncode != 0:
        stdout_tail = "\n".join(completed.stdout.splitlines()[-30:])
        stderr_tail = "\n".join(completed.stderr.splitlines()[-30:])
        raise CloseoutError(
            f"{name} falhou com exit={completed.returncode}\n"
            f"stdout:\n{stdout_tail}\nstderr:\n{stderr_tail}"
        )
    summary = parse_last_json(completed.stdout, name)
    return {
        "name": name,
        "durationMs": duration_ms,
        "summary": summary,
    }


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def ensure_ingest_summary(summary: dict[str, Any]) -> None:
    images = summary.get("images")
    ok = summary.get("ok")
    errors = summary.get("errors")
    if not isinstance(images, int) or images <= 0:
        raise CloseoutError(f"ingestão sem contagem válida de imagens: {summary}")
    if ok != images or errors != 0:
        raise CloseoutError(
            f"ingestão não ficou integralmente OK: images={images} ok={ok} errors={errors}"
        )


def ensure_bundle_summary(summary: dict[str, Any]) -> None:
    models = summary.get("models")
    canonical = summary.get("canonicalImages")
    variants = summary.get("variantFiles")
    if not isinstance(models, int) or models <= 0:
        raise CloseoutError(f"bundle sem modelos: {summary}")
    if not isinstance(canonical, int) or canonical <= 0:
        raise CloseoutError(f"bundle sem imagens canônicas: {summary}")
    if not isinstance(variants, int) or variants < canonical * 3:
        raise CloseoutError(
            f"bundle com variantes incompletas: canonical={canonical} variantFiles={variants}"
        )


def closeout(
    *,
    repo_root: Path,
    catalog_root: Path,
    registry: Path,
    mapping: Path,
    incoming_root: Path,
    manifest: Path,
    ingest_output: Path,
    bundle_output: Path,
    report_path: Path,
    apply_local: bool,
) -> dict[str, Any]:
    if MEDIA_METADATA_VERSION != 8:
        raise CloseoutError(
            f"metadata version inesperada: {MEDIA_METADATA_VERSION}; esperado=8"
        )
    for label, path in (
        ("repo", repo_root),
        ("catálogo", catalog_root),
        ("incoming", incoming_root),
    ):
        if not path.is_dir():
            raise CloseoutError(f"{label} não é diretório: {path}")
    for label, path in (
        ("registro", registry),
        ("mapping", mapping),
        ("manifesto", manifest),
    ):
        if not path.is_file():
            raise CloseoutError(f"{label} não é arquivo: {path}")

    provenance = ingest_output / "recovery-provenance.jsonl"
    registry_report = ingest_output / "recovery-registry-report.json"
    steps: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "version": 1,
        "status": "running",
        "applyLocal": apply_local,
        "metadataVersion": MEDIA_METADATA_VERSION,
        "catalogRoot": str(catalog_root),
        "registry": str(registry),
        "steps": steps,
        "productionWrites": 0,
    }
    write_report(report_path, report)

    def execute(name: str, argv: list[str]) -> dict[str, Any]:
        result = run_step(repo_root, name, argv)
        steps.append(result)
        write_report(report_path, report)
        return result["summary"]

    materialize_base = [
        "tools/materialize_r2_recovery.py",
        "--manifest", str(manifest),
        "--mapping", str(mapping),
        "--incoming-root", str(incoming_root),
        "--root", str(catalog_root),
    ]
    dry_materialize = execute("recovery-materialize-dry-run", materialize_base)
    pending_copies = int(dry_materialize.get("pendingCopies") or 0)
    if pending_copies and not apply_local:
        report["status"] = "local_changes_required"
        report["pendingLocalCopies"] = pending_copies
        write_report(report_path, report)
        return report

    if apply_local:
        execute(
            "recovery-materialize-apply",
            [*materialize_base, "--provenance-output", str(provenance), "--apply"],
        )

    verify = execute(
        "recovery-verify",
        [
            "tools/verify_r2_recovery.py",
            "--manifest", str(manifest),
            "--mapping", str(mapping),
            "--root", str(catalog_root),
            "--output", str(provenance),
        ],
    )
    if verify.get("completeCoverage") is not True or verify.get("verified") != 3:
        raise CloseoutError(f"recuperação física não ficou 3/3: {verify}")

    registry_base = [
        "tools/apply_r2_recovery_registry.py",
        "--registry", str(registry),
        "--manifest", str(manifest),
        "--provenance", str(provenance),
        "--root", str(catalog_root),
        "--mapping", str(mapping),
    ]
    registry_dry = execute("recovery-registry-dry-run", registry_base)
    pending_registry = int(registry_dry.get("pendingUpdates") or 0)
    if pending_registry and not apply_local:
        report["status"] = "local_changes_required"
        report["pendingRegistryUpdates"] = pending_registry
        report["pendingRegistryInserts"] = int(registry_dry.get("pendingInserts") or 0)
        report["pendingRegistryExistingRowUpdates"] = int(
            registry_dry.get("pendingExistingRowUpdates") or 0
        )
        write_report(report_path, report)
        return report

    if apply_local:
        execute(
            "recovery-registry-apply",
            [*registry_base, "--report", str(registry_report), "--apply"],
        )

    ingest = execute(
        "ingest-fresh",
        [
            "tools/ingest_catalog.py",
            str(catalog_root),
            "--output", str(ingest_output),
            "--audit-registry", str(registry),
            "--no-resume",
        ],
    )
    ensure_ingest_summary(ingest)

    bundle = execute(
        "bundle-v8-fresh",
        [
            "tools/build_media_bundle.py",
            str(catalog_root),
            str(ingest_output / "manifest.jsonl"),
            "--output", str(bundle_output),
            "--no-resume",
        ],
    )
    ensure_bundle_summary(bundle)

    execute(
        "r2-dry-run",
        ["tools/publish_r2.py", str(bundle_output / "r2"), "--dry-run"],
    )
    execute(
        "d1-check-production",
        [
            "tools/publish_d1.py",
            str(bundle_output / "models.jsonl"),
            "--check-production",
            "--retire-absent-approvals",
            "config/catalog-model-retirement-approvals.csv",
        ],
    )
    execute(
        "legacy-gallery-repairs-preflight",
        ["tools/apply_legacy_gallery_repairs.py"],
    )

    report["status"] = "preflight_green"
    report["productionWrites"] = 0
    report["nextAction"] = (
        "review the final report, then apply the two monotonic gallery repairs separately"
    )
    write_report(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Executa o closeout local + preflights cloud do catálogo sem writes de produção."
    )
    parser.add_argument("--root", type=Path, required=True, help="raiz física do Catálogo")
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--incoming-root", type=Path, required=True)
    parser.add_argument(
        "--registry",
        type=Path,
        help="REGISTRO-AUDITORIA-IMAGENS.csv; padrão sob 00 - ESTATISTICAS - CATALOGO",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("config/catalog-r2-recovery-manifest.json"),
    )
    parser.add_argument("--ingest-output", type=Path, default=Path(".catalog-ingest"))
    parser.add_argument("--bundle-output", type=Path, default=Path(".publish-bundle"))
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(".catalog-ingest/closeout-preflight.json"),
    )
    parser.add_argument(
        "--apply-local",
        action="store_true",
        help="autoriza somente materialização local + patch atômico do audit registry",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    catalog_root = args.root.resolve()
    registry = (
        args.registry.resolve()
        if args.registry
        else catalog_root
        / "00 - ESTATISTICAS - CATALOGO"
        / "REGISTRO-AUDITORIA-IMAGENS.csv"
    )
    manifest = args.manifest if args.manifest.is_absolute() else repo_root / args.manifest
    ingest_output = args.ingest_output if args.ingest_output.is_absolute() else repo_root / args.ingest_output
    bundle_output = args.bundle_output if args.bundle_output.is_absolute() else repo_root / args.bundle_output
    report = args.report if args.report.is_absolute() else repo_root / args.report

    try:
        result = closeout(
            repo_root=repo_root,
            catalog_root=catalog_root,
            registry=registry,
            mapping=args.mapping.resolve(),
            incoming_root=args.incoming_root.resolve(),
            manifest=manifest.resolve(),
            ingest_output=ingest_output.resolve(),
            bundle_output=bundle_output.resolve(),
            report_path=report.resolve(),
            apply_local=args.apply_local,
        )
    except (CloseoutError, OSError, ValueError) as exc:
        failure = {
            "version": 1,
            "status": "failed",
            "error": str(exc),
            "productionWrites": 0,
        }
        try:
            write_report(report.resolve(), failure)
        except OSError:
            pass
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "preflight_green" else 3


if __name__ == "__main__":
    raise SystemExit(main())
