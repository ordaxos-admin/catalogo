#!/usr/bin/env python3
"""Acquire an exact, reviewed external-media selection into private R2 staging.

The selection is bounded by explicit SHA-256 values already present in versioned evidence.
Dry-run is the default. Apply downloads one source object at a time, verifies SHA/bytes,
writes only the content-addressed private staging key, reads it back, verifies again, then
appends a durable checkpoint journal event before deleting the temporary bytes.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from staging_checkpoint import acquire_checkpoint_lease, append_verified_object

ALLOWED_REVIEW = {
    "clear",
    "creator_attribution",
    "unknown",
    "legacy_accepted",
    "legacy_accepted_exact_production_cover",
}
ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}
USER_AGENT = "catalog-private-staging/1.0"
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def decode_b64_text(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise RuntimeError(f"{field} ausente")
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{field} inválido") from exc
    if not decoded:
        raise RuntimeError(f"{field} vazio")
    return decoded


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"objeto JSON esperado: {path}")
    return data


def extract_evidence_rows(path: Path) -> list[dict[str, Any]]:
    def flatten(value: dict[str, Any]) -> list[dict[str, Any]]:
        for field in ("images", "candidates"):
            rows = value.get(field)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        if value.get("sha256"):
            return [value]
        return []

    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"linha JSONL inválida: {path}:{line_no}")
            rows.extend(flatten(value))
        return rows
    return flatten(load_json(path))


def validate_source_contract(data: dict[str, Any]) -> tuple[str, str, str, str]:
    if data.get("version") != 1:
        raise RuntimeError("versão do contrato da fonte incompatível")
    source_id = str(data.get("sourceId") or "").strip()
    origin = decode_b64_text(data.get("sourceOriginB64"), "sourceOriginB64").rstrip("/")
    prefix = decode_b64_text(data.get("stagingPrefixB64"), "stagingPrefixB64").strip("/")
    bucket = str(data.get("stagingBucket") or "").strip()
    parsed = urllib.parse.urlsplit(origin)
    if not source_id or parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise RuntimeError("origem/sourceId inválidos")
    if not prefix or any(part in {"", ".", ".."} for part in prefix.split("/")) or "\\" in prefix:
        raise RuntimeError("prefixo staging inválido")
    if not bucket:
        raise RuntimeError("stagingBucket ausente")
    return source_id, origin, prefix, bucket


def source_path(row: dict[str, Any]) -> str:
    path = str(row.get("sourceObjectPath") or row.get("sourcePath") or "").strip()
    if not path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise RuntimeError("evidência selecionada não possui sourcePath seguro")
    return path


def extension_for(path: str) -> str:
    ext = Path(urllib.parse.unquote(path)).suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if ext not in ALLOWED_EXTS:
        raise RuntimeError(f"extensão não permitida: {ext!r}")
    return ext


def staging_key(prefix: str, sha: str, ext: str) -> str:
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
        raise RuntimeError("SHA-256 inválido")
    return f"{prefix}/sha256/{sha[:2]}/{sha}.{ext}"


def select_rows(rows: list[dict[str, Any]], selected_shas: list[str]) -> list[dict[str, Any]]:
    by_sha: dict[str, dict[str, Any]] = {}
    for row in rows:
        sha = str(row.get("sha256") or "").strip().lower()
        if not sha:
            continue
        if sha in by_sha:
            raise RuntimeError(f"SHA duplicado na evidência: {sha}")
        by_sha[sha] = row
    selected: list[dict[str, Any]] = []
    for sha in selected_shas:
        row = by_sha.get(sha)
        if row is None:
            raise RuntimeError(f"SHA selecionado ausente da evidência: {sha}")
        size = int(row.get("bytes") or 0)
        if size < 1 or size > MAX_IMAGE_BYTES:
            raise RuntimeError(f"bytes inválidos para {sha}")
        review = str(row.get("watermarkStatus") or row.get("review") or "").strip()
        if review == "watermarked":
            raise RuntimeError(f"mídia já bloqueada por watermark: {sha}")
        if review not in ALLOWED_REVIEW:
            raise RuntimeError(f"estado de revisão não reconhecido para staging privado: {sha}: {review!r}")
        source_path(row)
        selected.append(row)
    return selected


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def download_source(origin: str, row: dict[str, Any], destination: Path) -> None:
    path = source_path(row)
    base = urllib.parse.urlsplit(origin)
    url = origin + path
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.casefold() != base.netloc.casefold():
        raise RuntimeError("sourcePath escapou da origem autorizada")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise RuntimeError("download excedeu limite individual")
            handle.write(chunk)
    actual_sha, actual_size = sha256_file(destination)
    expected_sha = str(row["sha256"]).lower()
    expected_size = int(row["bytes"])
    if actual_sha != expected_sha or actual_size != expected_size:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"fonte divergiu da evidência para {expected_sha}: "
            f"sha={actual_sha} bytes={actual_size} esperado={expected_size}"
        )


def r2_url(account_id: str, bucket: str, key: str) -> str:
    encoded = urllib.parse.quote(key, safe="/")
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{encoded}"


def read_remote(account_id: str, token: str, bucket: str, key: str, destination: Path) -> bool:
    request = urllib.request.Request(
        r2_url(account_id, bucket, key),
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise
    destination.parent.mkdir(parents=True, exist_ok=True)
    with response, destination.open("wb") as handle:
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise RuntimeError("objeto staging remoto excede limite")
            handle.write(chunk)
    return True


def put_remote(account_id: str, token: str, bucket: str, key: str, source: Path, content_type: str) -> None:
    payload = source.read_bytes()
    request = urllib.request.Request(
        r2_url(account_id, bucket, key),
        data=payload,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()


def verify_file(path: Path, expected_sha: str, expected_size: int, label: str) -> None:
    actual_sha, actual_size = sha256_file(path)
    if actual_sha != expected_sha or actual_size != expected_size:
        raise RuntimeError(
            f"{label} divergiu: sha={actual_sha} bytes={actual_size}; "
            f"esperado sha={expected_sha} bytes={expected_size}"
        )


def write_summary(path: Path | None, data: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(args: argparse.Namespace) -> dict[str, Any]:
    evidence_path = Path(args.evidence)
    contract = load_json(Path(args.source_contract))
    source_id, origin, prefix, bucket = validate_source_contract(contract)
    selected_shas = [str(value).strip().lower() for value in args.sha]
    if not selected_shas or len(set(selected_shas)) != len(selected_shas):
        raise RuntimeError("seleção SHA vazia ou duplicada")
    rows = select_rows(extract_evidence_rows(evidence_path), selected_shas)

    plan = []
    for row in rows:
        sha = str(row["sha256"]).lower()
        ext = extension_for(source_path(row))
        plan.append(
            {
                "sha256": sha,
                "bytes": int(row["bytes"]),
                "sourceObjectPath": source_path(row),
                "watermarkStatus": str(row.get("watermarkStatus") or row.get("review")),
                "stagingKey": staging_key(prefix, sha, ext),
            }
        )
    summary: dict[str, Any] = {
        "version": 1,
        "sourceId": source_id,
        "evidence": str(evidence_path).replace("\\", "/"),
        "selected": len(plan),
        "apply": bool(args.apply),
        "stagingBucket": bucket,
        "productionWrites": 0,
        "objects": plan,
    }
    if not args.apply:
        summary["stagingWrites"] = 0
        summary["state"] = "dry_run"
        write_summary(Path(args.summary) if args.summary else None, summary)
        print(json.dumps(summary, ensure_ascii=False))
        return summary

    account_id = str(os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = str(os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if not account_id or not token:
        raise RuntimeError("credenciais Cloudflare ausentes")
    checkpoint_root = Path(args.checkpoint_root)
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    writes = 0
    reused = 0

    with acquire_checkpoint_lease(checkpoint_root):
        for row, planned in zip(rows, plan, strict=True):
            sha = planned["sha256"]
            expected_size = int(planned["bytes"])
            ext = extension_for(planned["sourceObjectPath"])
            local = checkpoint_root / "tmp" / f"source-{sha}.{ext}"
            remote = checkpoint_root / "tmp" / f"remote-{sha}.{ext}"
            download_source(origin, row, local)
            key = str(planned["stagingKey"])
            existed = read_remote(account_id, token, bucket, key, remote)
            if existed:
                verify_file(remote, sha, expected_size, "objeto staging pré-existente")
                reused += 1
            else:
                content_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
                put_remote(account_id, token, bucket, key, local, content_type)
                writes += 1
                if not read_remote(account_id, token, bucket, key, remote):
                    raise RuntimeError(f"read-back ausente após upload: {key}")
                verify_file(remote, sha, expected_size, "read-back staging")
            append_verified_object(
                checkpoint_root,
                key,
                {
                    "sourceId": source_id,
                    "sha256": sha,
                    "bytes": expected_size,
                    "sourceObjectPath": planned["sourceObjectPath"],
                    "remoteVerified": True,
                },
            )
            local.unlink(missing_ok=True)
            remote.unlink(missing_ok=True)

    summary.update(
        {
            "state": "applied_verified",
            "stagingWrites": writes,
            "stagingObjectsReused": reused,
            "remoteVerified": len(plan),
        }
    )
    write_summary(Path(args.summary) if args.summary else None, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence")
    parser.add_argument("--source-contract", required=True)
    parser.add_argument("--sha", action="append", required=True)
    parser.add_argument("--checkpoint-root", default=".tmp/external-staging")
    parser.add_argument("--summary")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_args())
