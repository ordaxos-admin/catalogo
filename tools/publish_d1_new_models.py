#!/usr/bin/env python3
"""Publish a strictly new-only model delta to D1.

This publisher is intentionally narrower than publish_d1.py. It never treats an
incomplete candidate set as a catalog snapshot, never retires or updates an
existing model, and commits the complete small delta in one D1 batch transaction.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from publish_d1 import (
    build_image_source_statements,
    build_statements,
    candidate_image_sources,
    fetch_production_matches,
    fetch_production_sha_matches,
    load_models,
    request_batch,
    require_env,
    validate_production_compatibility,
)
from publish_r2 import default_state, discover

MAX_TRANSACTION_STATEMENTS = 500


def new_only_statements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reuse canonical SQL shape while making existing taxonomy immutable."""
    result: list[dict[str, Any]] = []
    for statement in build_statements(rows):
        sql = str(statement["sql"])
        if sql.startswith("INSERT INTO models("):
            marker = "\nON CONFLICT(id) DO UPDATE SET"
            if marker not in sql:
                raise RuntimeError("contrato SQL de models mudou; revise o publisher new-only")
            sql = sql.split(marker, 1)[0]
        else:
            replaced = re.sub(
                r"ON CONFLICT\(([^)]+)\) DO UPDATE SET[\s\S]*$",
                r"ON CONFLICT(\1) DO NOTHING",
                sql,
                count=1,
            )
            if replaced == sql:
                raise RuntimeError("contrato SQL de taxonomia mudou; revise o publisher new-only")
            sql = replaced
        result.append({"sql": sql, "params": list(statement["params"])})
    return result


def build_transaction(rows: list[dict[str, Any]], models_path: Path) -> list[dict[str, Any]]:
    statements = [
        *new_only_statements(rows),
        *build_image_source_statements(models_path, rows),
    ]
    if not statements:
        raise RuntimeError("transação new-only vazia")
    if len(statements) > MAX_TRANSACTION_STATEMENTS:
        raise RuntimeError(
            f"delta possui {len(statements)} statements; limite transacional é {MAX_TRANSACTION_STATEMENTS}. "
            "Divida o lote por autorização explícita em vez de publicar parcialmente."
        )
    return statements


def validate_new_only(rows: list[dict[str, Any]], production_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if production_rows:
        collisions = sorted(
            {
                str(row.get("id") or row.get("slug") or row.get("code") or "desconhecido")
                for row in production_rows
            }
        )
        raise RuntimeError(
            "delta new-only colide com modelo já publicado por id/slug/code: "
            + ", ".join(collisions[:10])
        )
    summary = validate_production_compatibility(rows, [])
    if summary.get("newModels") != len(rows) or summary.get("existingModels") != 0:
        raise RuntimeError(f"classificação new-only inesperada: {summary}")
    return summary


def validate_sha_new_only(production_sha_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if production_sha_rows:
        sample = sorted(
            {
                str(row.get("source_sha256") or "")
                for row in production_sha_rows
                if row.get("source_sha256")
            }
        )
        raise RuntimeError(
            f"delta new-only reutiliza {len(sample)} SHA(s) já publicados: {sample[:5]}"
        )
    return {"ready": True, "productionShaMatches": 0}


def load_r2_proof(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"proof R2 inválido: {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("objects"), dict):
        raise RuntimeError(f"contrato de proof R2 incompatível: {path}")
    return data


def validate_r2_proof(models_path: Path, proof_path: Path, expected_bucket: str) -> dict[str, Any]:
    r2_root = models_path.resolve().parent / "r2"
    if not r2_root.is_dir():
        raise RuntimeError(f"bundle R2 ausente: {r2_root}")
    candidates = discover(r2_root, default_state())
    proof = load_r2_proof(proof_path)
    proof_bucket = str(proof.get("bucket") or "").strip()
    if not expected_bucket or proof_bucket != expected_bucket:
        raise RuntimeError(
            f"proof R2 pertence ao bucket errado: {proof_bucket!r}; esperado {expected_bucket!r}"
        )
    objects = proof["objects"]
    candidate_keys = {item.key for item in candidates}
    proof_keys = set(objects)
    if candidate_keys != proof_keys:
        missing = sorted(candidate_keys - proof_keys)
        extra = sorted(proof_keys - candidate_keys)
        raise RuntimeError(
            f"proof R2 não corresponde exatamente ao bundle; missing={missing[:5]}, extra={extra[:5]}"
        )
    for item in candidates:
        value = objects.get(item.key)
        if not isinstance(value, dict):
            raise RuntimeError(f"proof R2 inválido para {item.key}")
        if str(value.get("sha256") or "") != item.sha256 or int(value.get("size") or -1) != item.size:
            raise RuntimeError(f"proof R2 diverge do bundle: {item.key}")
        if value.get("verified") is not True:
            raise RuntimeError(f"objeto R2 não verificado: {item.key}")
    return {"ready": True, "bucket": proof_bucket, "objects": len(candidates)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publica somente modelos realmente novos, sem semântica de snapshot/retirement."
    )
    parser.add_argument("models", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--r2-proof", type=Path, help="proof SHA+tamanho de todo o bundle; obrigatório em --apply")
    parser.add_argument("--expected-r2-bucket", help="bucket público esperado pelo proof; obrigatório em --apply")
    args = parser.parse_args()

    if not args.models.is_file():
        parser.error(f"models.jsonl ausente: {args.models}")
    if args.apply and (args.r2_proof is None or not args.r2_proof.is_file()):
        parser.error("--apply exige --r2-proof existente")
    if args.apply and not str(args.expected_r2_bucket or "").strip():
        parser.error("--apply exige --expected-r2-bucket")

    try:
        rows = load_models(args.models)
        account_id = require_env("CLOUDFLARE_ACCOUNT_ID")
        database_id = require_env("CLOUDFLARE_D1_DATABASE_ID")
        token = require_env("CLOUDFLARE_API_TOKEN")

        production_rows = fetch_production_matches(account_id, database_id, token, rows)
        compatibility = validate_new_only(rows, production_rows)
        sources = candidate_image_sources(args.models, rows)
        production_sha_rows = fetch_production_sha_matches(account_id, database_id, token, sources)
        sha_gate = validate_sha_new_only(production_sha_rows)
        transaction = build_transaction(rows, args.models)
        model_statements = len(new_only_statements(rows))
        image_statements = len(transaction) - model_statements

        summary: dict[str, Any] = {
            "mode": "new-only",
            "models": len(rows),
            "images": len(sources),
            "productionCompatibility": compatibility,
            "imageIdentityContinuity": sha_gate,
            "statements": model_statements,
            "imageSourceStatements": image_statements,
            "transactionStatements": len(transaction),
            "transactionLimit": MAX_TRANSACTION_STATEMENTS,
            "apply": args.apply,
            "updatesExistingModels": 0,
            "retirements": 0,
            "deletes": 0,
        }
        if not args.apply:
            print(json.dumps(summary, ensure_ascii=False))
            return 0

        assert args.r2_proof is not None
        summary["r2Gate"] = validate_r2_proof(
            args.models,
            args.r2_proof.resolve(),
            str(args.expected_r2_bucket).strip(),
        )

        # Re-check immediately before the only mutation. The model INSERT itself
        # has no conflict handler; a concurrent collision therefore aborts the
        # same D1 batch transaction instead of updating a pre-existing row.
        latest = fetch_production_matches(account_id, database_id, token, rows)
        validate_new_only(rows, latest)
        latest_sha = fetch_production_sha_matches(account_id, database_id, token, sources)
        validate_sha_new_only(latest_sha)

        request_batch(account_id, database_id, token, transaction)

        published = fetch_production_matches(account_id, database_id, token, rows)
        post = validate_production_compatibility(rows, published)
        if post.get("existingModels") != len(rows) or post.get("newModels") != 0:
            raise RuntimeError(f"verificação pós-write não encontrou todos os modelos: {post}")
        post_sha = fetch_production_sha_matches(account_id, database_id, token, sources)
        owners = {str(row.get("effective_model_id") or "") for row in post_sha}
        expected_owners = {str(row["id"]) for row in rows}
        if owners != expected_owners or len(post_sha) != len(sources):
            raise RuntimeError(
                f"índice SHA pós-write divergiu: rows={len(post_sha)}/{len(sources)}, "
                f"owners={sorted(owners)}, expected={sorted(expected_owners)}"
            )

        summary["batches"] = 1
        summary["postWrite"] = post
        summary["postWriteShaRows"] = len(post_sha)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
