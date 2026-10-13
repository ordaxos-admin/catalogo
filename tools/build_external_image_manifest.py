#!/usr/bin/env python3
"""Build an external-ingest image manifest from already authorized local media.

The acquisition boundary is deliberate: this tool never fetches network URLs.
It only verifies and analyzes bytes already materialized under an authorized
source root, then emits rows compatible with plan_gallery_merge.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image, ImageOps, UnidentifiedImageError

from import_woocommerce_catalog import (
    ImportContractError as ExternalIdentityError,
    image_extension,
    stable_image_identity,
    stable_product_id,
    stable_provider_image_id,
    stable_source_id,
)
from ingest_catalog import (
    IMAGE_EXTS,
    dhash_image,
    quality_score,
    sha256_file,
    sharpness_score,
)
from media_quality_policy import (
    MediaQualityPolicyError,
    WATERMARK_CLEAR,
    WATERMARK_UNKNOWN,
    WATERMARK_WATERMARKED,
    audited_watermark_status,
)


class ExternalManifestError(RuntimeError):
    pass


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ExternalManifestError(f"arquivo JSONL não encontrado: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExternalManifestError(
                    f"JSON inválido em {path}, linha {line_no}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ExternalManifestError(
                    f"registro não-objeto em {path}, linha {line_no}"
                )
            rows.append(row)
    return rows


def watermark_audit_index(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("version") != 1:
            raise ExternalManifestError("auditoria de marca d'água com versão inválida")
        product_key = str(row.get("sourceProductKey") or "").strip()
        image_id = str(row.get("sourceImageId") or "").strip()
        digest = str(row.get("sha256") or "").strip().lower()
        if not product_key or not image_id:
            raise ExternalManifestError("auditoria de marca d'água sem identidade de mídia")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ExternalManifestError(
                f"auditoria de marca d'água com SHA-256 inválido: {product_key}/{image_id}"
            )
        try:
            audited_watermark_status(row.get("watermarkStatus"))
        except MediaQualityPolicyError as exc:
            raise ExternalManifestError(
                f"{exc}: {product_key}/{image_id}"
            ) from exc
        key = (product_key, image_id)
        if key in indexed:
            raise ExternalManifestError(
                f"auditoria de marca d'água duplicada: {product_key}/{image_id}"
            )
        indexed[key] = row
    return indexed


def safe_local_path(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative:
        raise ExternalManifestError(f"targetPath inválido: {relative!r}")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ExternalManifestError(f"targetPath inseguro: {relative!r}")
    root_resolved = root.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ExternalManifestError(f"targetPath escapa da raiz autorizada: {relative!r}") from exc
    return target


def _normalized_product_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    if row.get("version") != 1:
        raise ExternalManifestError("produto externo com versão de contrato inválida")
    try:
        source_id = stable_source_id(row.get("sourceId"))
        product_id = stable_product_id(row.get("sourceProductId"))
    except ExternalIdentityError as exc:
        raise ExternalManifestError(f"identidade externa de produto inválida: {exc}") from exc

    if str(row.get("sourceProductId") or "") != product_id:
        raise ExternalManifestError(
            f"sourceProductId não canônico para {source_id}: {row.get('sourceProductId')!r}"
        )

    expected_key = f"{source_id}:product:{product_id}"
    expected_model = f"{source_id} / product:{product_id}"
    key = str(row.get("sourceProductKey") or "").strip()
    incoming_model = str(row.get("incomingModel") or "").strip()
    if key != expected_key or incoming_model != expected_model:
        raise ExternalManifestError(
            "contrato de identidade do produto diverge do namespace/ID imutável: "
            f"key={key!r}, model={incoming_model!r}, esperado={expected_key!r}/{expected_model!r}"
        )

    source_url = str(row.get("sourceUrl") or "").strip()
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ExternalManifestError(f"produto externo sem sourceUrl HTTPS válido: {key}")
    return source_id, product_id, expected_key, expected_model


def product_index(products: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in products:
        _, _, key, incoming_model = _normalized_product_identity(row)
        if key in indexed:
            raise ExternalManifestError(f"sourceProductKey duplicado: {key}")
        if row.get("readyForMaterialization") is not True:
            raise ExternalManifestError(
                f"produto ainda exige revisão de taxonomia/identidade: {key}"
            )
        category = str(row.get("canonicalCategoryCandidate") or "").strip()
        franchise = str(row.get("franchiseCandidate") or "").strip()
        if not category or not franchise or not incoming_model:
            raise ExternalManifestError(
                f"produto pronto sem categoria/franquia/identidade: {key}"
            )
        image_count = row.get("imageCount")
        if not isinstance(image_count, int) or isinstance(image_count, bool) or image_count <= 0:
            raise ExternalManifestError(
                f"produto pronto sem imageCount positivo: {key} ({image_count!r})"
            )
        indexed[key] = row
    return indexed


def validate_plan_identity(product: dict[str, Any], plan: dict[str, Any]) -> None:
    source_id, product_id, expected_key, expected_model = _normalized_product_identity(product)
    if plan.get("version") != 1:
        raise ExternalManifestError(f"image plan com versão inválida: {expected_key}")

    plan_source_id = str(plan.get("sourceId") or "").strip()
    plan_key = str(plan.get("sourceProductKey") or "").strip()
    plan_model = str(plan.get("incomingModel") or "").strip()
    if (
        plan_source_id != source_id
        or plan_key != expected_key
        or plan_model != expected_model
    ):
        raise ExternalManifestError(
            "image plan não pertence à identidade externa declarada: "
            f"{plan_source_id!r}/{plan_key!r}/{plan_model!r}"
        )

    source_url = str(plan.get("sourceUrl") or "").strip()
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ExternalManifestError(f"sourceUrl HTTPS inválida no plano: {expected_key}")

    image_id_raw = plan.get("sourceImageId")
    if not isinstance(image_id_raw, str) or image_id_raw != image_id_raw.strip() or not image_id_raw:
        raise ExternalManifestError(f"sourceImageId inválido no plano: {expected_key}")
    image_id = image_id_raw
    identity_kind = str(plan.get("sourceImageIdentityKind") or "").strip()

    try:
        if identity_kind == "provider_id":
            canonical_image_id = stable_provider_image_id(image_id)
            if canonical_image_id != image_id:
                raise ExternalManifestError(
                    f"sourceImageId provider_id não canônico: {image_id!r}"
                )
        elif identity_kind == "url_sha256":
            canonical_image_id, expected_kind = stable_image_identity({}, source_url)
            if expected_kind != "url_sha256" or canonical_image_id != image_id:
                raise ExternalManifestError(
                    f"sourceImageId url_sha256 não corresponde à sourceUrl: {image_id!r}"
                )
        else:
            raise ExternalManifestError(
                f"sourceImageIdentityKind inválido no plano: {identity_kind!r}"
            )
        extension = image_extension(source_url)
    except ExternalIdentityError as exc:
        raise ExternalManifestError(f"identidade de imagem inválida no plano: {exc}") from exc

    expected_target = (
        Path(source_id)
        / f"product-{product_id}"
        / f"{image_id}{extension}"
    ).as_posix()
    target = str(plan.get("targetPath") or "").strip()
    if target != expected_target:
        raise ExternalManifestError(
            f"targetPath diverge da identidade determinística: {target!r} != {expected_target!r}"
        )


def validate_plan_coverage(
    indexed: dict[str, dict[str, Any]],
    image_plan: list[dict[str, Any]],
) -> None:
    grouped_positions: dict[str, list[int]] = defaultdict(list)
    grouped_rows: dict[str, int] = defaultdict(int)

    for plan in image_plan:
        key = str(plan.get("sourceProductKey") or "").strip()
        if key not in indexed:
            raise ExternalManifestError(
                f"image plan referencia produto ausente/não pronto: {key or '<vazio>'}"
            )
        validate_plan_identity(indexed[key], plan)
        raw_position = plan.get("position")
        if not isinstance(raw_position, int) or isinstance(raw_position, bool) or raw_position <= 0:
            raise ExternalManifestError(
                f"posição inválida no plano para {key}: {raw_position!r}"
            )
        grouped_rows[key] += 1
        grouped_positions[key].append(raw_position)

    for key, product in indexed.items():
        expected = int(product["imageCount"])
        actual = grouped_rows.get(key, 0)
        if actual != expected:
            raise ExternalManifestError(
                f"galeria parcial no plano para {key}: esperado={expected}, encontrado={actual}"
            )
        positions = grouped_positions.get(key, [])
        expected_positions = list(range(1, expected + 1))
        if sorted(positions) != expected_positions:
            raise ExternalManifestError(
                f"posições incompletas/duplicadas no plano para {key}: "
                f"esperado={expected_positions}, encontrado={sorted(positions)}"
            )


def analyze_file(
    root: Path,
    image_plan: dict[str, Any],
    product: dict[str, Any],
    *,
    watermark_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    relative = str(image_plan.get("targetPath") or "").strip()
    path = safe_local_path(root, relative)
    if not path.is_file():
        raise ExternalManifestError(f"imagem materializada ausente: {relative}")
    if path.suffix.lower() not in IMAGE_EXTS:
        raise ExternalManifestError(f"extensão não suportada: {relative}")

    stat = path.stat()
    if stat.st_size <= 0:
        raise ExternalManifestError(f"imagem vazia: {relative}")

    digest = sha256_file(path)
    try:
        with Image.open(path) as verify_image:
            verify_image.verify()
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = image.size
            perceptual = dhash_image(image)
            sharpness = sharpness_score(image)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ExternalManifestError(
            f"imagem inválida {relative}: {type(exc).__name__}: {exc}"
        ) from exc

    if width <= 0 or height <= 0:
        raise ExternalManifestError(f"dimensões inválidas: {relative}")

    source_product_key = str(product["sourceProductKey"])
    source_image_id = str(image_plan.get("sourceImageId") or "")
    incoming_model = str(product["incomingModel"])
    watermark_status = WATERMARK_UNKNOWN
    watermark_audit_meta: dict[str, Any] | None = None
    if watermark_audit is not None:
        audit_digest = str(watermark_audit.get("sha256") or "").strip().lower()
        if audit_digest != digest:
            raise ExternalManifestError(
                "auditoria de marca d'água não corresponde aos bytes materializados: "
                f"{source_product_key}/{source_image_id}"
            )
        try:
            watermark_status = audited_watermark_status(watermark_audit.get("watermarkStatus"))
        except MediaQualityPolicyError as exc:
            raise ExternalManifestError(
                f"{exc}: {source_product_key}/{source_image_id}"
            ) from exc
        watermark_audit_meta = {
            "version": 1,
            "sha256": audit_digest,
            "reviewMethod": str(watermark_audit.get("reviewMethod") or "").strip() or None,
            "note": str(watermark_audit.get("note") or "").strip() or None,
        }

    return {
        "path": relative,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "status": "OK",
        "error": None,
        "sha256": digest,
        "dhash": perceptual,
        "width": width,
        "height": height,
        "megapixels": round(width * height / 1_000_000, 3),
        "sharpness": sharpness,
        "quality_score": quality_score(width, height, sharpness, path.suffix),
        "watermark_status": watermark_status,
        "watermark_audit": watermark_audit_meta,
        "category": str(product["canonicalCategoryCandidate"]),
        "franchise": str(product["franchiseCandidate"]),
        "model_key": incoming_model,
        "public_model_key": incoming_model,
        "audit_code": None,
        "identification": str(product.get("displayName") or product.get("sourceTitle") or ""),
        "audit_model_group": incoming_model,
        "duplicate_group": None,
        "canonical": True,
        "source_provenance": {
            "kind": "AUTHORIZED_EXTERNAL_MEDIA",
            "sourceId": str(product.get("sourceId") or ""),
            "sourceProductKey": source_product_key,
            "sourceProductUrl": str(product.get("sourceUrl") or ""),
            "sourceImageId": str(image_plan.get("sourceImageId") or ""),
            "sourceImageUrl": str(image_plan.get("sourceUrl") or ""),
            "position": int(image_plan["position"]),
        },
    }


def build_manifest(
    products: list[dict[str, Any]],
    image_plan: list[dict[str, Any]],
    *,
    images_root: Path,
    media_audit: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    indexed = product_index(products)
    validate_plan_coverage(indexed, image_plan)
    audit_index = watermark_audit_index(list(media_audit or []))

    seen_targets: set[str] = set()
    seen_source_pairs: set[tuple[str, str]] = set()
    used_audits: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []

    for plan in image_plan:
        key = str(plan.get("sourceProductKey") or "").strip()
        target = str(plan.get("targetPath") or "").strip()
        if target in seen_targets:
            raise ExternalManifestError(f"targetPath duplicado no plano: {target}")
        seen_targets.add(target)
        source_url = str(plan.get("sourceUrl") or "").strip()
        source_pair = (key, source_url)
        if not source_url or source_pair in seen_source_pairs:
            raise ExternalManifestError(
                f"sourceUrl ausente/duplicada no plano: {key} {source_url!r}"
            )
        seen_source_pairs.add(source_pair)
        audit_key = (key, str(plan.get("sourceImageId") or "").strip())
        audit = audit_index.get(audit_key)
        if audit is not None:
            used_audits.add(audit_key)
        rows.append(
            analyze_file(
                images_root,
                plan,
                indexed[key],
                watermark_audit=audit,
            )
        )

    unused_audits = sorted(set(audit_index).difference(used_audits))
    if unused_audits:
        raise ExternalManifestError(
            f"auditorias de marca d'água sem mídia correspondente no plano: {unused_audits[:5]}"
        )

    rows.sort(
        key=lambda row: (
            str(row["public_model_key"]).casefold(),
            int(row["source_provenance"]["position"]),
            str(row["path"]).casefold(),
        )
    )
    product_keys = {row["source_provenance"]["sourceProductKey"] for row in rows}
    missing_products = sorted(set(indexed).difference(product_keys), key=str.casefold)
    if missing_products:
        raise ExternalManifestError(
            f"produtos prontos sem nenhuma imagem materializada: {missing_products[:5]}"
        )

    watermark_counts = {
        status: sum(1 for row in rows if row.get("watermark_status") == status)
        for status in (WATERMARK_CLEAR, WATERMARK_WATERMARKED, WATERMARK_UNKNOWN)
    }
    summary = {
        "version": 1,
        "products": len(product_keys),
        "images": len(rows),
        "statusOk": len(rows),
        "networkDownloads": 0,
        "provenance": "AUTHORIZED_EXTERNAL_MEDIA",
        "completeGalleryCoverage": True,
        "watermarkStatus": watermark_counts,
        "watermarkAuditComplete": watermark_counts[WATERMARK_UNKNOWN] == 0,
    }
    return rows, summary


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analisa mídia externa já materializada e gera manifest.jsonl compatível "
            "com o planejador de galeria; nunca acessa a rede."
        )
    )
    parser.add_argument("products", type=Path, help="products.jsonl normalizado")
    parser.add_argument("image_plan", type=Path, help="image-download-plan.jsonl")
    parser.add_argument("--images-root", type=Path, required=True)
    parser.add_argument(
        "--media-audit",
        type=Path,
        help=(
            "JSONL opcional, fora do repositório, com sourceProductKey/sourceImageId/"
            "sha256/watermarkStatus; mídia sem auditoria fica watermark_status=unknown"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    try:
        rows, summary = build_manifest(
            read_jsonl(args.products),
            read_jsonl(args.image_plan),
            images_root=args.images_root,
            media_audit=read_jsonl(args.media_audit) if args.media_audit else None,
        )
        write_jsonl(args.output, rows)
        if args.summary:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except (ExternalManifestError, OSError, ValueError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
