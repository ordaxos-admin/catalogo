#!/usr/bin/env python3
"""Incrementally publish a generated media bundle to Cloudflare R2.

The publisher never mutates the source catalog and never deletes remote objects.
It keeps a local checkpoint so subsequent runs skip unchanged files without a
remote HEAD request for every object. Use --verify-remote when an explicit
remote integrity pass is desired.

Both long-lived S3 credentials and Cloudflare temporary R2 credentials are
supported. Temporary credentials additionally provide R2_SESSION_TOKEN.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATE_VERSION = 1
DEFAULT_BUCKET = "tonecos-catalogo-media"
DEFAULT_WORKERS = 12
CHECKPOINT_INTERVAL = 25


@dataclass(frozen=True)
class Candidate:
    key: str
    path: Path
    size: int
    mtime_ns: int
    sha256: str
    content_type: str
    cache_control: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_type_for(path: Path) -> str:
    if path.suffix.lower() == ".webp":
        return "image/webp"
    if path.suffix.lower() == ".json":
        return "application/json; charset=utf-8"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def cache_control_for(key: str) -> str:
    if key.startswith("gallery/"):
        # Gallery manifests are content-addressed by build_media_bundle.py.
        return "public, max-age=31536000, immutable"
    if key.startswith("media/"):
        # Media keys are source-addressed. Keep a long cache, but not immutable,
        # so a future renderer revision can safely replace a variant if needed.
        return "public, max-age=2592000, stale-while-revalidate=86400"
    return "public, max-age=300"


def publication_phase(key: str) -> int:
    if key.startswith("media/"):
        return 0
    if key.startswith("gallery/"):
        return 1
    return 2


def default_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "objects": {}}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"checkpoint inválido: {path}: {exc}") from exc
    if state.get("version") != STATE_VERSION or not isinstance(state.get("objects"), dict):
        raise RuntimeError(f"versão de checkpoint incompatível: {path}")
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def discover(bundle_root: Path, state: dict[str, Any]) -> list[Candidate]:
    candidates: list[Candidate] = []
    objects = state["objects"]
    paths: list[Path] = []
    for directory, dirnames, filenames in os.walk(bundle_root, topdown=True, followlinks=False):
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        base = Path(directory)
        paths.extend(base / filename for filename in filenames)
    for path in sorted(
        paths,
        key=lambda item: (
            publication_phase(item.relative_to(bundle_root).as_posix()),
            item.relative_to(bundle_root).as_posix().casefold(),
        ),
    ):
        key = path.relative_to(bundle_root).as_posix()
        stat = path.stat()
        previous = objects.get(key, {})
        if (
            previous.get("size") == stat.st_size
            and previous.get("mtime_ns") == stat.st_mtime_ns
            and isinstance(previous.get("sha256"), str)
        ):
            digest = previous["sha256"]
        else:
            digest = sha256_file(path)
        candidates.append(Candidate(
            key=key,
            path=path,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            sha256=digest,
            content_type=content_type_for(path),
            cache_control=cache_control_for(key),
        ))
    return candidates


def make_r2_client(
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    session_token: str | None = None,
):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - guarded by requirements.txt in production
        raise RuntimeError("boto3 não instalado; execute pip install -r tools/requirements.txt") from exc

    return boto3.client(
        service_name="s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        aws_session_token=session_token or None,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 8, "mode": "adaptive"},
            connect_timeout=10,
            read_timeout=120,
        ),
    )


def remote_matches(client, bucket: str, candidate: Candidate) -> bool:
    try:
        response = client.head_object(Bucket=bucket, Key=candidate.key)
    except Exception as exc:  # botocore exceptions are optional in unit tests
        response = getattr(exc, "response", {})
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = response.get("Error", {}).get("Code")
        if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    metadata = response.get("Metadata", {})
    return metadata.get("sha256") == candidate.sha256 and int(response.get("ContentLength", -1)) == candidate.size


def upload_one(client, bucket: str, candidate: Candidate) -> None:
    client.upload_file(
        Filename=str(candidate.path),
        Bucket=bucket,
        Key=candidate.key,
        ExtraArgs={
            "ContentType": candidate.content_type,
            "CacheControl": candidate.cache_control,
            "Metadata": {"sha256": candidate.sha256},
        },
    )


def publish(
    bundle_root: Path,
    state_path: Path,
    bucket: str,
    client=None,
    workers: int = DEFAULT_WORKERS,
    verify_remote: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    bundle_root = bundle_root.resolve()
    if not bundle_root.is_dir():
        raise FileNotFoundError(f"bundle R2 não encontrado: {bundle_root}")
    if workers < 1 or workers > 64:
        raise ValueError("workers deve ficar entre 1 e 64")

    state = load_state(state_path)
    candidates = discover(bundle_root, state)
    objects = state["objects"]
    pending: list[Candidate] = []
    skipped = 0
    verified = 0

    for candidate in candidates:
        previous = objects.get(candidate.key, {})
        locally_published = previous.get("published_sha256") == candidate.sha256
        if locally_published and not verify_remote:
            skipped += 1
            objects[candidate.key] = {
                **previous,
                "size": candidate.size,
                "mtime_ns": candidate.mtime_ns,
                "sha256": candidate.sha256,
            }
            continue
        if locally_published and verify_remote:
            if client is None:
                raise RuntimeError("client R2 obrigatório para --verify-remote")
            if remote_matches(client, bucket, candidate):
                skipped += 1
                verified += 1
                objects[candidate.key] = {
                    **previous,
                    "size": candidate.size,
                    "mtime_ns": candidate.mtime_ns,
                    "sha256": candidate.sha256,
                    "remote_verified": True,
                }
                continue
        pending.append(candidate)

    summary: dict[str, Any] = {
        "bucket": bucket,
        "objects": len(candidates),
        "pending": len(pending),
        "uploaded": 0,
        "skipped": skipped,
        "verified": verified,
        "uploadedBytes": 0,
        "deferred": 0,
        "errors": [],
        "dryRun": dry_run,
    }

    if dry_run:
        summary["pendingBytes"] = sum(item.size for item in pending)
        return summary
    if pending and client is None:
        raise RuntimeError("client R2 obrigatório para publicar")

    completed_since_checkpoint = 0
    phases = [
        [candidate for candidate in pending if publication_phase(candidate.key) == phase]
        for phase in range(3)
    ]

    for phase_index, phase_candidates in enumerate(phases):
        if not phase_candidates:
            continue
        if summary["errors"]:
            summary["deferred"] += sum(len(items) for items in phases[phase_index:])
            break

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="r2-upload") as executor:
            futures = {executor.submit(upload_one, client, bucket, candidate): candidate for candidate in phase_candidates}
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    future.result()
                except Exception as exc:  # keep successful progress resumable
                    summary["errors"].append({"key": candidate.key, "error": f"{type(exc).__name__}: {exc}"})
                else:
                    summary["uploaded"] += 1
                    summary["uploadedBytes"] += candidate.size
                    objects[candidate.key] = {
                        "size": candidate.size,
                        "mtime_ns": candidate.mtime_ns,
                        "sha256": candidate.sha256,
                        "published_sha256": candidate.sha256,
                        "content_type": candidate.content_type,
                        "cache_control": candidate.cache_control,
                    }
                    completed_since_checkpoint += 1
                    if completed_since_checkpoint >= CHECKPOINT_INTERVAL:
                        save_state(state_path, state)
                        completed_since_checkpoint = 0

        save_state(state_path, state)

    save_state(state_path, state)
    return summary


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"variável obrigatória ausente: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica incrementalmente um bundle no Cloudflare R2.")
    parser.add_argument("bundle", type=Path, help="pasta .publish-bundle/r2")
    parser.add_argument("--state", type=Path, help="checkpoint local; padrão: ao lado da pasta r2")
    parser.add_argument("--bucket", default=os.environ.get("R2_BUCKET", DEFAULT_BUCKET))
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--verify-remote", action="store_true", help="confere objetos pulados via HEAD no R2")
    parser.add_argument("--dry-run", action="store_true", help="calcula o delta sem enviar arquivos")
    args = parser.parse_args()

    bundle_root = args.bundle.resolve()
    state_path = args.state.resolve() if args.state else bundle_root.parent / "r2-publish-state.json"

    client = None
    if not args.dry_run:
        client = make_r2_client(
            account_id=env_required("CLOUDFLARE_ACCOUNT_ID"),
            access_key_id=env_required("R2_ACCESS_KEY_ID"),
            secret_access_key=env_required("R2_SECRET_ACCESS_KEY"),
            session_token=os.environ.get("R2_SESSION_TOKEN", "").strip() or None,
        )

    try:
        summary = publish(
            bundle_root=bundle_root,
            state_path=state_path,
            bucket=args.bucket,
            client=client,
            workers=args.workers,
            verify_remote=args.verify_remote,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, ensure_ascii=False))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
