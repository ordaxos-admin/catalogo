#!/usr/bin/env python3
"""Publish a small R2 delta with Wrangler and produce a verified proof.

Unlike publish_r2.py, this transport uses the existing Cloudflare API token used
by CI instead of introducing S3 credentials. It never deletes objects. Media is
uploaded before gallery manifests. Every object is then read back directly from
R2; public media objects are additionally read through the first-party media
gateway before a proof file is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from publish_r2 import DEFAULT_BUCKET, Candidate, default_state, discover, publication_phase

CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"variável obrigatória ausente: {name}")
    return value


def put_one(candidate: Candidate, bucket: str, cwd: Path) -> None:
    command = [
        "npx", "wrangler", "r2", "object", "put", f"{bucket}/{candidate.key}",
        "--file", str(candidate.path),
        "--content-type", candidate.content_type,
        "--cache-control", candidate.cache_control,
        "--remote", "--force",
    ]
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.splitlines()[-20:])
        raise RuntimeError(f"wrangler falhou para {candidate.key}:\n{tail}")


def fetch_r2(account_id: str, token: str, bucket: str, key: str) -> bytes:
    encoded_key = urllib.parse.quote(key, safe="/")
    url = (
        f"{CLOUDFLARE_API_BASE}/accounts/{urllib.parse.quote(account_id, safe='')}"
        f"/r2/buckets/{urllib.parse.quote(bucket, safe='')}/objects/{encoded_key}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "TonecosR2ReleaseVerifier/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 200:
                raise RuntimeError(f"R2 retornou HTTP {response.status}: {key}")
            return response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"falha ao reler objeto R2 {key}: {exc}") from exc


def _validate_full_gateway_response(status: int, headers: Any, data: bytes, key: str) -> None:
    if status == 200:
        return
    if status != 206:
        raise RuntimeError(f"gateway retornou HTTP {status}: {key}")

    content_range = str(headers.get("Content-Range", "")).strip()
    match = re.fullmatch(r"bytes\s+0-(\d+)/(\d+)", content_range, flags=re.IGNORECASE)
    if match is None:
        raise RuntimeError(f"gateway 206 sem Content-Range integral: {key}: {content_range!r}")
    end = int(match.group(1))
    total = int(match.group(2))
    if total < 1 or end != total - 1 or len(data) != total:
        raise RuntimeError(
            f"gateway 206 não cobre objeto inteiro: {key}: "
            f"range={content_range!r}, bytes={len(data)}"
        )


def fetch_gateway(api_base: str, key: str) -> bytes:
    if not key.startswith("media/"):
        raise RuntimeError(f"chave não pública não pode usar gateway /media: {key}")
    url = api_base.rstrip("/") + "/" + urllib.parse.quote(key, safe="/")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TonecosR2ReleaseVerifier/1.0",
            "Cache-Control": "no-cache, no-store",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
            _validate_full_gateway_response(int(response.status), response.headers, data, key)
            return data
    except urllib.error.URLError as exc:
        raise RuntimeError(f"falha ao verificar gateway para {key}: {exc}") from exc


def assert_bytes(candidate: Candidate, data: bytes, source: str) -> tuple[str, int]:
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != candidate.size or digest != candidate.sha256:
        raise RuntimeError(
            f"{source} divergiu para {candidate.key}: "
            f"sha/bytes={digest}/{len(data)}, esperado={candidate.sha256}/{candidate.size}"
        )
    return digest, len(data)


def verify_one(
    candidate: Candidate,
    api_base: str,
    account_id: str,
    token: str,
    bucket: str,
) -> dict[str, Any]:
    r2_data = fetch_r2(account_id, token, bucket, candidate.key)
    digest, size = assert_bytes(candidate, r2_data, "R2")
    result: dict[str, Any] = {
        "sha256": digest,
        "size": size,
        "verified": True,
        "r2Verified": True,
    }
    if candidate.key.startswith("media/"):
        gateway_data = fetch_gateway(api_base, candidate.key)
        assert_bytes(candidate, gateway_data, "R2/gateway")
        result["gatewayVerified"] = True
    return result


def publish(
    bundle: Path,
    bucket: str,
    api_base: str | None,
    proof_path: Path | None,
    apply: bool,
    workers: int,
    cwd: Path,
) -> dict[str, Any]:
    candidates = discover(bundle, default_state())
    summary: dict[str, Any] = {
        "bucket": bucket,
        "objects": len(candidates),
        "bytes": sum(item.size for item in candidates),
        "apply": apply,
        "uploaded": 0,
        "verified": 0,
        "gatewayVerified": 0,
        "deletes": 0,
    }
    if not apply:
        return summary
    if not api_base or proof_path is None:
        raise RuntimeError("--apply exige --api-base e --proof")
    account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
    token = require_env("CLOUDFLARE_API_TOKEN")

    for phase in range(3):
        phase_candidates = [item for item in candidates if publication_phase(item.key) == phase]
        if not phase_candidates:
            continue
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"r2-phase-{phase}") as executor:
            futures = {executor.submit(put_one, item, bucket, cwd): item for item in phase_candidates}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    raise RuntimeError(f"upload abortado em {item.key}: {exc}") from exc
                summary["uploaded"] += 1

    proof_objects: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="r2-verify") as executor:
        futures = {
            executor.submit(verify_one, item, api_base, account_id, token, bucket): item
            for item in candidates
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                proof_objects[item.key] = future.result()
            except Exception as exc:
                raise RuntimeError(f"verificação R2 abortada em {item.key}: {exc}") from exc
            summary["verified"] += 1
            if item.key.startswith("media/"):
                summary["gatewayVerified"] += 1

    proof = {
        "version": 1,
        "bucket": bucket,
        "objects": {key: proof_objects[key] for key in sorted(proof_objects)},
    }
    proof_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = proof_path.with_suffix(proof_path.suffix + ".tmp")
    temporary.write_text(json.dumps(proof, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, proof_path)
    summary["proof"] = str(proof_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica delta R2 via Wrangler e prova bytes no R2/gateway.")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--bucket", default=os.environ.get("R2_BUCKET", DEFAULT_BUCKET))
    parser.add_argument("--api-base")
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.bundle.is_dir():
        parser.error(f"bundle R2 ausente: {args.bundle}")
    if args.workers < 1 or args.workers > 12:
        parser.error("--workers deve ficar entre 1 e 12")
    try:
        summary = publish(
            args.bundle.resolve(),
            args.bucket,
            args.api_base,
            args.proof.resolve() if args.proof else None,
            args.apply,
            args.workers,
            Path.cwd(),
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
