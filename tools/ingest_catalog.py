#!/usr/bin/env python3
"""Tonecos catalog ingestion analyzer.

Scans an existing folder hierarchy without mutating source files. It produces a
resumable manifest containing integrity, hashes, dimensions and a quality score,
then groups exact/visual duplicate candidates and recommends the best source.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from catalog_scope import (
    PUBLIC_TOP_LEVEL_CATEGORIES,
    PUBLIC_TOP_LEVEL_KEYS,
    clean_folder,
    is_public_top_level_category,
    load_public_top_level_categories,
)
from model_identity import VIEW_ANCHOR_PRIORITY, candidate_confidence, stem_view_descriptor

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".jfif"}
PUBLISHABLE_AUDIT_STATUS_RE = re.compile(r"^OK_VISUAL(?:\|[A-Z0-9]+(?:[._-][A-Z0-9]+)*)?$")
CHECKPOINT_INTERVAL = 25


@dataclass
class ImageRecord:
    path: str
    size: int
    mtime_ns: int
    status: str
    error: str | None
    sha256: str | None
    dhash: str | None
    width: int | None
    height: int | None
    megapixels: float | None
    sharpness: float | None
    quality_score: float | None
    category: str | None
    franchise: str | None
    model_key: str
    public_model_key: str | None = None
    audit_code: str | None = None
    identification: str | None = None
    audit_model_group: str | None = None
    duplicate_group: str | None = None
    canonical: bool = True


def discover_catalog_roots(root: Path) -> list[Path]:
    """Return only audited top-level catalog categories.

    Operational folders such as statistics, incoming batches and consolidated
    audit material live beside the public catalog. Production ingestion is
    explicit and fail-closed: only the six canonical public categories are
    eligible. The optional `OK - ` audit prefix and numeric count suffix do not
    change category identity.
    """
    roots = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and is_public_top_level_category(path.name)
        ),
        key=lambda path: path.name.casefold(),
    )
    if not roots:
        allowed = ", ".join(PUBLIC_TOP_LEVEL_CATEGORIES)
        raise RuntimeError(
            f"nenhuma categoria pública canônica encontrada em: {root}; permitidas: {allowed}"
        )
    return roots


def iter_images(root: Path) -> Iterable[Path]:
    for catalog_root in discover_catalog_roots(root):
        for path in catalog_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                yield path


def load_audit_registry(root: Path, registry: Path) -> tuple[list[Path], dict[str, str], dict[str, dict[str, str]]]:
    """Load the audited source-of-truth list without recursively crawling Drive.

    Every row must point under an audited top-level category. Duplicate
    paths, invalid hashes and unsupported extensions are rejected here; file
    existence is checked once in the processing loop to avoid duplicate remote
    metadata calls on mounted cloud drives.
    """
    paths: list[Path] = []
    expected_hashes: dict[str, str] = {}
    metadata: dict[str, dict[str, str]] = {}
    seen: set[str] = set()

    with registry.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"caminho", "sha256", "status"}
        if not required.issubset(reader.fieldnames or []):
            raise RuntimeError(
                f"registro de auditoria sem colunas obrigatórias {sorted(required)}: {registry}"
            )
        for line_no, row in enumerate(reader, 2):
            rel = (row.get("caminho") or "").strip()
            digest = (row.get("sha256") or "").strip().lower()
            status = (row.get("status") or "").strip()
            if not rel:
                raise RuntimeError(f"caminho vazio no registro de auditoria, linha {line_no}")
            if not PUBLISHABLE_AUDIT_STATUS_RE.fullmatch(status):
                raise RuntimeError(
                    f"entrada de auditoria não aprovada para publicação na linha {line_no}: "
                    f"{rel} ({status or 'sem status'})"
                )
            normalized = rel.replace("\\", "/")
            first = normalized.split("/", 1)[0]
            if not is_public_top_level_category(first):
                raise RuntimeError(
                    f"caminho fora de categoria auditada na linha {line_no}: {rel}"
                )
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise RuntimeError(f"SHA-256 inválido na linha {line_no}: {rel}")
            key = unicodedata.normalize("NFKC", normalized).casefold()
            if key in seen:
                raise RuntimeError(f"caminho duplicado no registro de auditoria: {rel}")
            seen.add(key)
            path = root.joinpath(*normalized.split("/"))
            if path.suffix.lower() not in IMAGE_EXTS:
                raise RuntimeError(f"extensão não suportada no registro de auditoria: {rel}")
            canonical_rel = str(path.relative_to(root))
            paths.append(path)
            expected_hashes[canonical_rel] = digest
            metadata[canonical_rel] = {
                "codigo": (row.get("codigo") or "").strip(),
                "identificacao": (row.get("identificacao") or "").strip(),
                "modelo_publico": (row.get("modelo_publico") or "").strip(),
            }

    if not paths:
        raise RuntimeError(f"registro de auditoria vazio: {registry}")
    return paths, expected_hashes, metadata


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dhash_image(image: Image.Image) -> str:
    sample = ImageOps.grayscale(image).resize((9, 8), Image.Resampling.LANCZOS)
    pixels = sample.tobytes()
    value = 0
    bit = 0
    for row in range(8):
        offset = row * 9
        for col in range(8):
            if pixels[offset + col] > pixels[offset + col + 1]:
                value |= 1 << bit
            bit += 1
    return f"{value:016x}"


def sharpness_score(image: Image.Image) -> float:
    sample = ImageOps.grayscale(image)
    sample.thumbnail((900, 900), Image.Resampling.LANCZOS)
    edges = sample.filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    return round(float(stat.var[0]) ** 0.5, 3)


def quality_score(width: int, height: int, sharpness: float, suffix: str) -> float:
    mp = (width * height) / 1_000_000
    resolution = min(55.0, 55.0 * math.log1p(mp) / math.log1p(12.0))
    detail = min(35.0, sharpness * 1.2)
    format_bonus = {".png": 10.0, ".tif": 10.0, ".tiff": 10.0, ".webp": 8.0, ".avif": 8.0, ".jpg": 7.0, ".jpeg": 7.0}.get(suffix.lower(), 5.0)
    return round(resolution + detail + format_bonus, 3)


def taxonomy(root: Path, path: Path) -> tuple[str | None, str | None, str]:
    rel = path.relative_to(root)
    folders = [clean_folder(part) for part in rel.parts[:-1]]
    category = folders[0] if folders else None
    franchise = folders[1] if len(folders) > 1 else None
    # Preserve the entire parent hierarchy: same character names in distinct
    # franchises must never be merged just because their leaf folder matches.
    model_key = " / ".join(folders) if folders else path.stem
    return category, franchise, model_key


def analyze(root: Path, path: Path) -> ImageRecord:
    stat = path.stat()
    rel = str(path.relative_to(root))
    category, franchise, model_key = taxonomy(root, path)
    try:
        if stat.st_size <= 0:
            raise ValueError("arquivo vazio")
        digest = sha256_file(path)
        with Image.open(path) as verify_image:
            verify_image.verify()
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = image.size
            perceptual = dhash_image(image)
            sharpness = sharpness_score(image)
        return ImageRecord(
            path=rel, size=stat.st_size, mtime_ns=stat.st_mtime_ns,
            status="OK", error=None, sha256=digest, dhash=perceptual,
            width=width, height=height, megapixels=round(width * height / 1_000_000, 3),
            sharpness=sharpness, quality_score=quality_score(width, height, sharpness, path.suffix),
            category=category, franchise=franchise, model_key=model_key,
        )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return ImageRecord(
            path=rel, size=stat.st_size, mtime_ns=stat.st_mtime_ns,
            status="ERRO", error=f"{type(exc).__name__}: {exc}", sha256=None, dhash=None,
            width=None, height=None, megapixels=None, sharpness=None, quality_score=None,
            category=category, franchise=franchise, model_key=model_key,
        )


def hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def choose_canonical(records: list[ImageRecord]) -> ImageRecord:
    return max(records, key=lambda item: (item.quality_score or -1, item.width or 0, item.height or 0, item.size))


def audited_view_descriptor(source_stem: str) -> tuple[str, str] | None:
    """Return the shared product family and trailing view descriptor."""
    match = stem_view_descriptor(source_stem)
    if not match:
        return None
    family, descriptor = match
    return family, descriptor.stem_suffix


def audited_view_family(source_stem: str) -> str | None:
    descriptor = audited_view_descriptor(source_stem)
    return descriptor[0] if descriptor else None


def infer_audited_gallery_groups(records: list[ImageRecord]) -> int:
    """Group only high-confidence legacy directional views.

    Framing descriptors such as corpo inteiro or em pe are ambiguous and
    remain distinct unless the audit registry provides an explicit
    modelo_publico. This keeps heuristic inference fail-closed.
    """
    candidates: dict[
        tuple[str, str],
        list[tuple[ImageRecord, str, str, str, object]],
    ] = defaultdict(list)
    for record in records:
        if record.status != "OK" or record.audit_model_group:
            continue
        source_stem = Path(record.path).stem
        match = stem_view_descriptor(source_stem)
        if not match:
            continue
        family, descriptor = match
        candidates[(record.model_key, family.casefold())].append(
            (record, family, descriptor.stem_suffix, source_stem, descriptor)
        )

    grouped = 0
    for members in candidates.values():
        if len(members) < 2:
            continue
        if candidate_confidence(member[4] for member in members) != "high":
            continue
        anchor = min(
            members,
            key=lambda member: (
                VIEW_ANCHOR_PRIORITY.get(member[2], 100),
                member[3].casefold(),
                member[0].path.casefold(),
            ),
        )
        group_identity = anchor[3]
        for record, _family, _suffix, _source_stem, _descriptor in members:
            record.audit_model_group = group_identity
            record.public_model_key = f"{record.model_key} / {group_identity}"
            grouped += 1
    return grouped


def disambiguate_public_model_keys(records: list[ImageRecord]) -> None:
    """Keep stable stem-based identities unless audited entries genuinely collide."""
    by_key: dict[str, list[ImageRecord]] = defaultdict(list)
    for record in records:
        if record.public_model_key:
            by_key[record.public_model_key].append(record)

    for public_key, members in by_key.items():
        if len(members) < 2:
            continue
        intentional_groups = {record.audit_model_group for record in members if record.audit_model_group}
        if len(intentional_groups) == 1 and all(record.audit_model_group for record in members):
            continue
        for record in members:
            discriminator = record.audit_code or (record.sha256[:16] if record.sha256 else None)
            if not discriminator:
                raise RuntimeError(f"identidade pública ambígua sem discriminador auditado: {public_key}")
            record.public_model_key = f"{public_key} / {discriminator}"


def mark_duplicates(records: list[ImageRecord], visual_threshold: int) -> list[dict]:
    groups: list[dict] = []
    valid = [record for record in records if record.status == "OK" and record.sha256 and record.dhash]

    # Exact duplicates are scoped to a model gallery. The same binary image may
    # legitimately be referenced by two different models (for example a shared
    # diorama). Removing it globally would make one model lose its image.
    exact: dict[tuple[str, str], list[ImageRecord]] = defaultdict(list)
    for record in valid:
        exact[(record.public_model_key or record.model_key, record.sha256 or "")].append(record)
    exact_counter = 0
    for (model_key, digest), members in exact.items():
        if len(members) < 2:
            continue
        exact_counter += 1
        group_id = f"exact-{exact_counter:06d}"
        canonical = choose_canonical(members)
        for member in members:
            member.duplicate_group = group_id
            member.canonical = member is canonical
        groups.append({
            "id": group_id,
            "kind": "exact",
            "model_key": model_key,
            "sha256": digest,
            "canonical": canonical.path,
            "members": [m.path for m in members],
        })

    # Visual comparison is intentionally scoped to one model hierarchy. This
    # avoids merging unrelated characters that happen to have similar silhouettes.
    by_model: dict[str, list[ImageRecord]] = defaultdict(list)
    for record in valid:
        if not record.duplicate_group and not record.audit_model_group:
            by_model[record.public_model_key or record.model_key].append(record)

    visual_counter = 0
    for model_key, members in by_model.items():
        remaining = set(range(len(members)))
        while remaining:
            seed_index = remaining.pop()
            cluster = [seed_index]
            frontier = [seed_index]
            while frontier:
                current = frontier.pop()
                near = [idx for idx in remaining if hamming(members[current].dhash or "0", members[idx].dhash or "0") <= visual_threshold]
                for idx in near:
                    remaining.remove(idx)
                    frontier.append(idx)
                    cluster.append(idx)
            if len(cluster) < 2:
                continue
            visual_counter += 1
            group_id = f"visual-{visual_counter:06d}"
            cluster_records = [members[idx] for idx in cluster]
            canonical = choose_canonical(cluster_records)
            for member in cluster_records:
                member.duplicate_group = group_id
                member.canonical = member is canonical
            groups.append({
                "id": group_id, "kind": "visual_candidate", "model_key": model_key,
                "threshold": visual_threshold, "canonical": canonical.path,
                "members": [m.path for m in cluster_records],
            })
    return groups


class _BKNode:
    __slots__ = ("value", "records", "children")

    def __init__(self, value: int, record: ImageRecord) -> None:
        self.value = value
        self.records = [record]
        self.children: dict[int, _BKNode] = {}


def _bk_add(root: _BKNode, value: int, record: ImageRecord) -> None:
    node = root
    while True:
        distance = (node.value ^ value).bit_count()
        if distance == 0:
            node.records.append(record)
            return
        child = node.children.get(distance)
        if child is None:
            node.children[distance] = _BKNode(value, record)
            return
        node = child


def _bk_query(root: _BKNode, value: int, threshold: int) -> list[tuple[int, ImageRecord]]:
    matches: list[tuple[int, ImageRecord]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        distance = (node.value ^ value).bit_count()
        if distance <= threshold:
            matches.extend((distance, record) for record in node.records)
        lower = max(0, distance - threshold)
        upper = distance + threshold
        stack.extend(
            child
            for edge, child in node.children.items()
            if lower <= edge <= upper
        )
    return matches


def cross_product_visual_candidates(records: list[ImageRecord], visual_threshold: int) -> list[dict]:
    """Report near-identical images assigned to different product identities.

    This is review-only: it never changes canonical flags. Candidates are scoped
    to one source hierarchy/personagem, preventing comparisons across unrelated
    franchises while still catching accidental duplicate product registrations.
    """
    by_hierarchy: dict[str, list[ImageRecord]] = defaultdict(list)
    for record in records:
        if (
            record.status == "OK"
            and record.dhash
            and record.public_model_key
            and record.sha256
        ):
            by_hierarchy[record.model_key].append(record)

    candidates: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for model_key, members in by_hierarchy.items():
        root: _BKNode | None = None
        for record in sorted(members, key=lambda item: item.path.casefold()):
            value = int(record.dhash or "0", 16)
            if root is not None:
                for distance, other in _bk_query(root, value, visual_threshold):
                    if other.public_model_key == record.public_model_key:
                        continue
                    pair = tuple(sorted((other.path, record.path), key=str.casefold))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    candidates.append({
                        "kind": "cross_product_visual",
                        "model_key": model_key,
                        "distance": distance,
                        "left": {
                            "path": other.path,
                            "public_model_key": other.public_model_key,
                            "audit_code": other.audit_code,
                            "sha256": other.sha256,
                        },
                        "right": {
                            "path": record.path,
                            "public_model_key": record.public_model_key,
                            "audit_code": record.audit_code,
                            "sha256": record.sha256,
                        },
                    })
            if root is None:
                root = _BKNode(value, record)
            else:
                _bk_add(root, value, record)

    return sorted(
        candidates,
        key=lambda item: (
            item["model_key"].casefold(),
            item["distance"],
            item["left"]["path"].casefold(),
            item["right"]["path"].casefold(),
        ),
    )


def load_checkpoint(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return {row["path"]: row for row in (json.loads(line) for line in handle if line.strip())}


def record_from_checkpoint(row: dict) -> ImageRecord:
    allowed = ImageRecord.__dataclass_fields__.keys()
    return ImageRecord(**{key: row.get(key) for key in allowed})


def save_progress_manifest(output: Path, records: list[ImageRecord]) -> None:
    """Atomically persist resumable progress without final duplicate decisions."""
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "manifest.jsonl"
    temporary = output / "manifest.jsonl.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for record in sorted(records, key=lambda item: item.path.casefold()):
            checkpoint_record = ImageRecord(**asdict(record))
            checkpoint_record.duplicate_group = None
            checkpoint_record.canonical = True
            handle.write(json.dumps(asdict(checkpoint_record), ensure_ascii=False) + "\n")
    os.replace(temporary, manifest)


def write_outputs(output: Path, records: list[ImageRecord], groups: list[dict], identity_candidates: list[dict] | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as handle:
        for record in sorted(records, key=lambda item: item.path.casefold()):
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    with (output / "duplicates.json").open("w", encoding="utf-8") as handle:
        json.dump(groups, handle, ensure_ascii=False, indent=2)

    candidates = list(identity_candidates or [])
    with (output / "identity-candidates.json").open("w", encoding="utf-8") as handle:
        json.dump(candidates, handle, ensure_ascii=False, indent=2)

    fields = ["path", "status", "width", "height", "megapixels", "quality_score", "model_key", "duplicate_group", "canonical", "error"]
    with (output / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in sorted(records, key=lambda item: item.path.casefold()):
            row = asdict(record)
            writer.writerow({key: row.get(key) for key in fields})

    summary = {
        "images": len(records),
        "ok": sum(record.status == "OK" for record in records),
        "errors": sum(record.status != "OK" for record in records),
        "duplicate_groups": len(groups),
        "duplicate_members": sum(len(group["members"]) for group in groups),
        "canonical_images": sum(record.status == "OK" and record.canonical for record in records),
        "models_detected": len({record.public_model_key or record.model_key for record in records}),
        "cross_product_visual_candidates": len(identity_candidates or []),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


def configure_console() -> None:
    """Use UTF-8 for progress output on Windows and other legacy consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="Analisa um catálogo sem modificar os arquivos de origem.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".catalog-ingest"))
    parser.add_argument("--visual-threshold", type=int, default=6)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--audit-registry",
        type=Path,
        help="CSV auditado com colunas caminho/sha256/status; publica somente status OK_VISUAL; evita varredura recursiva da raiz",
    )
    args = parser.parse_args()

    root = args.source.resolve()
    if not root.is_dir():
        parser.error(f"pasta não encontrada: {root}")
    if not 0 <= args.visual_threshold <= 16:
        parser.error("--visual-threshold deve ficar entre 0 e 16")

    old = {} if args.no_resume else load_checkpoint(args.output / "manifest.jsonl")
    records: list[ImageRecord] = []
    expected_hashes: dict[str, str] = {}
    audit_metadata: dict[str, dict[str, str]] = {}
    if args.audit_registry:
        registry = args.audit_registry.resolve()
        if not registry.is_file():
            parser.error(f"registro de auditoria não encontrado: {registry}")
        try:
            paths, expected_hashes, audit_metadata = load_audit_registry(root, registry)
        except RuntimeError as exc:
            parser.error(str(exc))
        paths = sorted(paths, key=lambda item: str(item).casefold())
        source_mode = "audit-registry"
    else:
        paths = sorted(iter_images(root), key=lambda item: str(item).casefold())
        source_mode = "directory-scan"
    print(f"TOTAL={len(paths)} CHECKPOINT={len(old)} SOURCE={source_mode}")

    for index, path in enumerate(paths, 1):
        rel = str(path.relative_to(root))
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise RuntimeError(f"arquivo auditado ausente: {rel}") from exc
        cached = old.get(rel)
        if cached and cached.get("size") == stat.st_size and cached.get("mtime_ns") == stat.st_mtime_ns:
            record = record_from_checkpoint(cached)
            # Duplicate decisions are recomputed every run from the complete set.
            record.duplicate_group = None
            record.canonical = True
        else:
            print(f"PROGRESSO={index}/{len(paths)} {rel}", flush=True)
            record = analyze(root, path)
        if args.audit_registry:
            meta = audit_metadata.get(rel, {})
            audit_model_group = (meta.get("modelo_publico") or "").strip()
            model_identity = audit_model_group or path.stem
            record.public_model_key = f"{record.model_key} / {model_identity}"
            record.audit_code = meta.get("codigo") or None
            record.identification = meta.get("identificacao") or None
            record.audit_model_group = audit_model_group or None
        expected_sha = expected_hashes.get(rel)
        if expected_sha and record.sha256 != expected_sha:
            raise RuntimeError(
                f"integridade divergente do registro auditado: {rel} "
                f"(esperado {expected_sha}, obtido {record.sha256})"
            )
        records.append(record)
        if index % CHECKPOINT_INTERVAL == 0:
            save_progress_manifest(args.output, records)
            print(f"CHECKPOINT={len(records)}", flush=True)

    if args.audit_registry:
        inferred_views = infer_audited_gallery_groups(records)
        if inferred_views:
            print(f"GALLERY_VIEWS_INFERRED={inferred_views}", flush=True)
        disambiguate_public_model_keys(records)
    groups = mark_duplicates(records, args.visual_threshold)
    identity_candidates = cross_product_visual_candidates(records, args.visual_threshold)
    write_outputs(args.output, records, groups, identity_candidates)
    return 1 if any(record.status != "OK" for record in records) else 0


if __name__ == "__main__":
    sys.exit(main())
