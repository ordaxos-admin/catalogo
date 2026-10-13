#!/usr/bin/env python3
"""Validate that migrate-before-worker remains backward compatible.

The production deploy applies pending D1 migrations before publishing the new
Worker. That ordering is safe only while every post-baseline migration is an
expand migration that the currently deployed Worker can tolerate.

This gate deliberately fails when a new required migration is unclassified or
contains an obviously destructive SQL operation. A future contract/cleanup
migration must therefore change the deployment strategy explicitly instead of
silently riding the migration-first path.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "catalog-schema-contract.json"
POLICY_PATH = ROOT / "config" / "migration-deploy-policy.json"
MIGRATIONS_DIR = ROOT / "migrations"

_UNSAFE_SQL = (
    ("drop-object", re.compile(r"\bDROP\s+(?:TABLE|VIEW|INDEX|TRIGGER)\b", re.IGNORECASE)),
    (
        "destructive-alter",
        re.compile(
            r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[^\s;]+\s+(?:RENAME\b|DROP\s+COLUMN\b)",
            re.IGNORECASE,
        ),
    ),
    ("delete-rows", re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE)),
    (
        "replace-rows",
        re.compile(r"\b(?:INSERT\s+OR\s+REPLACE|REPLACE\s+INTO)\b", re.IGNORECASE),
    ),
    ("truncate", re.compile(r"\bTRUNCATE\b", re.IGNORECASE)),
    ("writable-schema", re.compile(r"\bPRAGMA\s+writable_schema\b", re.IGNORECASE)),
)


_DROP_OBJECT_CAPTURE = re.compile(
    r"\bDROP\s+(TABLE|VIEW|INDEX|TRIGGER)\s+(?:IF\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _scrub_sql(sql: str) -> str:
    """Remove comments and string literals before scanning SQL keywords."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def unsafe_operations(sql: str) -> list[str]:
    scrubbed = _scrub_sql(sql)
    return [name for name, pattern in _UNSAFE_SQL if pattern.search(scrubbed)]


def trigger_replacement_approval_errors(
    sql: str, approved_triggers: list[str]
) -> list[str]:
    approved = [str(name).strip() for name in approved_triggers]
    if not approved or any(not name for name in approved) or len(set(approved)) != len(approved):
        return ["approved trigger replacement list must be non-empty and unique"]
    dropped = [
        (kind.upper(), name)
        for kind, name in _DROP_OBJECT_CAPTURE.findall(_scrub_sql(sql))
    ]
    if not dropped:
        return ["approved trigger replacement has no DROP object"]
    errors: list[str] = []
    non_trigger = [f"{kind}:{name}" for kind, name in dropped if kind != "TRIGGER"]
    if non_trigger:
        errors.append("approval cannot authorize non-trigger DROP: " + ", ".join(non_trigger))
    actual = [name for kind, name in dropped if kind == "TRIGGER"]
    unexpected = sorted(set(actual).difference(approved))
    missing = sorted(set(approved).difference(actual))
    if unexpected:
        errors.append("unapproved trigger DROP: " + ", ".join(unexpected))
    if missing:
        errors.append("approved trigger replacement not present in migration: " + ", ".join(missing))
    return errors


def validate_repository(root: Path = ROOT) -> list[str]:
    contract_path = root / "config" / "catalog-schema-contract.json"
    policy_path = root / "config" / "migration-deploy-policy.json"
    migrations_dir = root / "migrations"

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))

    errors: list[str] = []
    required = contract.get("requiredMigrations")
    latest = contract.get("latestMigration")
    strategy = policy.get("deploymentStrategy")
    baseline = policy.get("baselineThrough")
    classifications = policy.get("migrations")
    approved_trigger_replacements = policy.get("approvedTriggerReplacements", {})

    if not isinstance(required, list) or not required:
        return ["catalog schema contract must declare non-empty requiredMigrations"]
    if latest != required[-1]:
        errors.append("latestMigration must equal the last required migration")
    if strategy != "migrate-before-worker":
        errors.append("migration policy must explicitly use migrate-before-worker")
    if not isinstance(classifications, dict):
        return errors + ["migration policy must declare a migrations object"]
    if not isinstance(approved_trigger_replacements, dict):
        return errors + ["approvedTriggerReplacements must be an object"]
    if baseline not in required:
        errors.append("baselineThrough must be one of requiredMigrations")
        baseline_index = -1
    else:
        baseline_index = required.index(baseline)

    required_set = set(required)
    classified_set = set(classifications)
    missing = sorted(required_set - classified_set)
    extra = sorted(classified_set - required_set)
    if missing:
        errors.append("unclassified required migrations: " + ", ".join(missing))
    if extra:
        errors.append("policy contains non-required migrations: " + ", ".join(extra))

    approval_extra = sorted(set(approved_trigger_replacements).difference(required_set))
    if approval_extra:
        errors.append(
            "trigger replacement approval references non-required migrations: "
            + ", ".join(approval_extra)
        )

    for index, name in enumerate(required):
        migration_path = migrations_dir / name
        if not migration_path.is_file():
            errors.append(f"required migration is missing: {name}")
            continue

        classification = classifications.get(name)
        expected = "baseline" if index <= baseline_index else "expand"
        if classification != expected:
            errors.append(
                f"{name}: expected classification {expected!r}, got {classification!r}"
            )
            continue

        if classification == "expand":
            sql = migration_path.read_text(encoding="utf-8")
            hazards = unsafe_operations(sql)
            approval = approved_trigger_replacements.get(name)
            if approval is not None:
                if not isinstance(approval, dict):
                    errors.append(f"{name}: trigger replacement approval must be an object")
                else:
                    triggers = approval.get("triggers")
                    reason = str(approval.get("reason") or "").strip()
                    if not isinstance(triggers, list):
                        errors.append(f"{name}: trigger replacement approval requires triggers list")
                    elif not reason:
                        errors.append(f"{name}: trigger replacement approval requires reason")
                    elif "drop-object" not in hazards:
                        errors.append(f"{name}: trigger replacement approval is stale; no DROP object")
                    else:
                        approval_errors = trigger_replacement_approval_errors(sql, triggers)
                        if approval_errors:
                            errors.extend(f"{name}: {error}" for error in approval_errors)
                        else:
                            hazards = [hazard for hazard in hazards if hazard != "drop-object"]
            if hazards:
                errors.append(f"{name}: expand migration contains {', '.join(hazards)}")

    return errors


def main() -> int:
    errors = validate_repository()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Migration-first deployment policy: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
