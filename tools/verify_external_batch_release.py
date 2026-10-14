#!/usr/bin/env python3
"""Verify that every model from a released external batch is public with its gallery."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from publish_d1 import load_models
from verify_public_catalog import CatalogSmokeError, normalize_base, request_json, verify_public_model_gallery


def verify(models_path: Path, api_base: str) -> dict[str, Any]:
    expected = load_models(models_path)
    expected_by_id = {str(row["id"]): row for row in expected}
    api_base = normalize_base(api_base, "API base")
    found: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    pages = 0

    while True:
        query: dict[str, str | int] = {"limit": 100}
        if cursor:
            query["cursor"] = cursor
        payload = request_json(
            urllib.request.urlopen,
            f"{api_base}/api/catalog?{urllib.parse.urlencode(query)}",
            f"catálogo página {pages + 1}",
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise CatalogSmokeError("catálogo retornou items inválido")
        for item in items:
            if not isinstance(item, dict):
                raise CatalogSmokeError("catálogo contém item inválido")
            model_id = str(item.get("id") or "")
            if model_id in expected_by_id:
                if model_id in found:
                    raise CatalogSmokeError(f"modelo esperado apareceu duas vezes: {model_id}")
                found[model_id] = item
        pages += 1
        if set(found) == set(expected_by_id):
            break
        raw_cursor = payload.get("nextCursor")
        if raw_cursor in (None, ""):
            break
        next_cursor = str(raw_cursor)
        if next_cursor in seen_cursors:
            raise CatalogSmokeError("paginação entrou em ciclo")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if pages > 1000:
            raise CatalogSmokeError("catálogo excedeu 1000 páginas")

    missing = sorted(set(expected_by_id) - set(found))
    if missing:
        raise CatalogSmokeError(f"modelos do lote ausentes da API pública: {missing}")

    galleries = []
    for model_id, candidate in expected_by_id.items():
        public = found[model_id]
        comparisons = {
            "slug": (str(candidate["slug"]), str(public.get("slug") or "")),
            "code": (str(candidate["code"]), str(public.get("code") or "")),
            "image_count": (int(candidate["imageCount"]), int(public.get("image_count") or 0)),
            "cover_storage_key": (str(candidate["coverStorageKey"]), str(public.get("cover_storage_key") or "")),
        }
        mismatches = [key for key, pair in comparisons.items() if pair[0] != pair[1]]
        if mismatches:
            raise CatalogSmokeError(f"modelo público diverge do bundle {model_id}: {mismatches}")

        # galleryManifestKey is an internal storage pointer and is intentionally
        # not part of the public catalog payload. Its exact physical object is
        # already bound by the R2 proof. The Worker loads that private manifest
        # internally and validates it against the model's physical gallery_version
        # before serving /api/models/<slug>/images, so a successful public gallery
        # read is the correct public proof rather than exposing the private key.
        #
        # galleryVersion in models.jsonl is the physical R2 manifest version.
        # The public API exposes models.public_gallery_version as gallery_version,
        # a separate monotonic cache revision. Requiring those values to be equal
        # would conflate physical storage identity with public cache revision.
        try:
            public_gallery_revision = int(public.get("gallery_version") or 0)
        except (TypeError, ValueError) as exc:
            raise CatalogSmokeError(
                f"modelo público {model_id} possui revisão pública de galeria inválida"
            ) from exc
        if public_gallery_revision < 1:
            raise CatalogSmokeError(
                f"modelo público {model_id} possui revisão pública de galeria inválida"
            )

        gallery = verify_public_model_gallery(api_base, public)
        gallery["physicalGalleryVersion"] = int(candidate["galleryVersion"])
        gallery["publicGalleryVersion"] = public_gallery_revision
        galleries.append(gallery)

    return {
        "ok": True,
        "models": len(expected),
        "pagesScanned": pages,
        "images": sum(int(item["images"]) for item in galleries),
        "galleries": galleries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica publicação pública de um lote externo.")
    parser.add_argument("models", type=Path)
    parser.add_argument("--api-base", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.models, args.api_base), ensure_ascii=False))
        return 0
    except (RuntimeError, CatalogSmokeError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
