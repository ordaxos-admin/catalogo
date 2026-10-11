import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CloudflareWorkflowConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github" / "workflows" / "cloudflare.yml").read_text(encoding="utf-8")
        cls.maintenance = (ROOT / ".github" / "workflows" / "catalog-maintenance.yml").read_text(encoding="utf-8")
        cls.wrangler = json.loads((ROOT / "wrangler.jsonc").read_text(encoding="utf-8"))

    def test_preflight_fails_closed_when_deployment_credentials_are_missing(self):
        self.assertIn("API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", self.workflow)
        self.assertIn("TURNSTILE_SECRET: ${{ secrets.TURNSTILE_SECRET_KEY }}", self.workflow)
        self.assertIn("missing+=(CLOUDFLARE_API_TOKEN)", self.workflow)
        self.assertIn("missing+=(TURNSTILE_SECRET_KEY)", self.workflow)
        self.assertIn("missing+=(VITE_API_BASE_URL)", self.workflow)
        self.assertIn("echo 'ready=false'", self.workflow)
        self.assertIn("::error::Deploy Cloudflare bloqueado", self.workflow)
        self.assertIn("credencial ausente é falha de deploy", self.workflow)
        self.assertIn("exit 1", self.workflow)

    def test_deploy_still_requires_secrets_when_preflight_is_ready(self):
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", self.workflow)
        self.assertIn("TURNSTILE_SECRET_KEY: ${{ secrets.TURNSTILE_SECRET_KEY }}", self.workflow)
        self.assertIn("Validate deployment credentials", self.workflow)
        self.assertEqual(["TURNSTILE_SECRET_KEY"], self.wrangler.get("secrets", {}).get("required"))

    def test_d1_database_id_is_versioned_ssot_not_repository_variable(self):
        databases = self.wrangler.get("d1_databases")
        self.assertIsInstance(databases, list)
        self.assertEqual(1, len(databases))
        self.assertEqual("DB", databases[0].get("binding"))
        database_id = str(databases[0].get("database_id") or "")
        self.assertNotEqual("REPLACE_AFTER_D1_CREATE", database_id)
        self.assertRegex(database_id, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assertNotIn("vars.CLOUDFLARE_D1_DATABASE_ID", self.workflow)
        self.assertNotIn("missing+=(CLOUDFLARE_D1_DATABASE_ID)", self.workflow)
        self.assertNotIn("vars.CLOUDFLARE_D1_DATABASE_ID", self.maintenance)

    def test_worker_entrypoint_owns_api_and_private_r2_media(self):
        self.assertEqual("worker/entry.ts", self.wrangler.get("main"))
        buckets = self.wrangler.get("r2_buckets")
        self.assertIsInstance(buckets, list)
        self.assertEqual([{"binding": "MEDIA", "bucket_name": "tonecos-catalogo-media"}], buckets)

    def test_production_cors_contains_only_public_origins(self):
        origins = self.wrangler.get("vars", {}).get("CORS_ORIGINS", "")
        self.assertEqual(
            "https://washingtonmsdj.github.io,https://acheguese.com.br,https://www.acheguese.com.br",
            origins,
        )
        self.assertNotIn("localhost", origins)
        self.assertNotIn("127.0.0.1", origins)

    def test_deploy_exports_canonical_d1_binding_before_render_and_migrations(self):
        self.assertIn("Export canonical D1 binding", self.workflow)
        self.assertIn("node tools/read_wrangler_d1_id.mjs", self.workflow)
        self.assertIn('CLOUDFLARE_D1_DATABASE_ID=$database_id', self.workflow)
        self.assertLess(self.workflow.index("Export canonical D1 binding"), self.workflow.index("Render production config"))
        self.assertLess(self.workflow.index("Export canonical D1 binding"), self.workflow.index("Apply D1 migrations"))

    def test_remote_worker_semantic_drift_is_checked_before_worker_write(self):
        self.assertIn("Validate remote Worker config against canonical SSOT", self.workflow)
        self.assertIn("node tools/check_remote_worker_config.mjs", self.workflow)
        self.assertLess(self.workflow.index("Validate remote Worker config against canonical SSOT"), self.workflow.index("Capture current production Worker version"))
        self.assertLess(self.workflow.index("Validate remote Worker config against canonical SSOT"), self.workflow.index("Upload Worker version with required secret and no traffic"))
        self.assertNotIn("--strict", self.workflow)

    def test_turnstile_secret_is_staged_atomically_without_implicit_deploy(self):
        self.assertNotIn("npx wrangler secret put", self.workflow)
        self.assertIn("tonecos-worker-secrets.json", self.workflow)
        self.assertIn("--secrets-file \"$secrets_file\"", self.workflow)
        self.assertIn("trap 'rm -f \"$secrets_file\"' EXIT", self.workflow)
        self.assertIn("TURNSTILE_SECRET_KEY: process.env.TURNSTILE_SECRET_KEY", self.workflow)

    def test_previous_production_version_is_captured_before_any_worker_write(self):
        self.assertIn("Capture current production Worker version", self.workflow)
        self.assertIn("node tools/cloudflare_deploy_state.mjs active-version", self.workflow)
        self.assertIn("PREVIOUS_WORKER_VERSION_ID=$previous_version", self.workflow)
        self.assertLess(self.workflow.index("Capture current production Worker version"), self.workflow.index("Upload Worker version with required secret and no traffic"))

    def test_staged_worker_version_is_captured_from_structured_wrangler_output(self):
        self.assertIn("WRANGLER_OUTPUT_FILE_PATH:", self.workflow)
        self.assertIn("wrangler-version-upload.ndjson", self.workflow)
        self.assertIn("Capture staged Worker version", self.workflow)
        self.assertIn("cloudflare_deploy_state.mjs uploaded-version", self.workflow)
        self.assertIn("STAGED_WORKER_VERSION_ID=$staged_version", self.workflow)
        self.assertLess(self.workflow.index("Upload Worker version with required secret and no traffic"), self.workflow.index("Capture staged Worker version"))

    def test_zero_traffic_deployment_registers_staged_version_before_override_smoke(self):
        self.assertIn("Register staged Worker at zero traffic", self.workflow)
        self.assertIn("id: stage_deployment", self.workflow)
        self.assertIn('"${PREVIOUS_WORKER_VERSION_ID}@100%"', self.workflow)
        self.assertIn('"${STAGED_WORKER_VERSION_ID}@0%"', self.workflow)
        self.assertLess(self.workflow.index("Apply D1 migrations"), self.workflow.index("Register staged Worker at zero traffic"))
        self.assertLess(self.workflow.index("Register staged Worker at zero traffic"), self.workflow.index("Smoke staged Worker version before promotion"))

    def test_staged_smoke_runs_after_expand_migrations_and_before_promotion(self):
        self.assertIn("Smoke staged Worker version before promotion", self.workflow)
        self.assertIn("check_staged_worker_version.mjs", self.workflow)
        self.assertLess(self.workflow.index("Validate migration-first compatibility"), self.workflow.index("Apply D1 migrations"))
        self.assertLess(self.workflow.index("Apply D1 migrations"), self.workflow.index("Smoke staged Worker version before promotion"))
        self.assertLess(self.workflow.index("Smoke staged Worker version before promotion"), self.workflow.index("Promote staged Worker version"))

    def test_worker_promotion_is_explicit_and_only_after_staged_smoke(self):
        self.assertIn("WORKER_VERSION_TAG: ci-${{ github.run_id }}-${{ github.run_attempt }}", self.workflow)
        self.assertIn("npx wrangler versions upload", self.workflow)
        self.assertIn('--tag "$WORKER_VERSION_TAG"', self.workflow)
        self.assertIn("Promote staged Worker version", self.workflow)
        self.assertIn("id: promote", self.workflow)
        self.assertIn('"${STAGED_WORKER_VERSION_ID}@100%"', self.workflow)
        self.assertIn("--yes", self.workflow)
        self.assertNotIn("npx wrangler deploy --config .wrangler.deploy.jsonc", self.workflow)

    def test_promoted_worker_full_release_is_one_bounded_gate(self):
        gate = "Verify promoted Worker deployment and full public release"
        self.assertIn(gate, self.workflow)
        self.assertIn("check_promoted_worker_version.mjs", self.workflow)
        self.assertLess(self.workflow.index("Promote staged Worker version"), self.workflow.index(gate))

    def test_public_media_gateway_is_proved_after_promotion_before_cache_gate(self):
        release_gate = "Verify promoted Worker deployment and full public release"
        media_gate = "Verify public media gateway after promotion"
        cache_gate = "Verify Workers Cache hit after promotion"
        self.assertIn(media_gate, self.workflow)
        self.assertIn('python tools/verify_public_catalog.py --api-base "$CATALOG_API_URL"', self.workflow)
        self.assertNotIn("VITE_MEDIA_BASE_URL", self.workflow)
        self.assertLess(self.workflow.index(release_gate), self.workflow.index(media_gate))
        self.assertLess(self.workflow.index(media_gate), self.workflow.index(cache_gate))

    def test_failed_staged_or_promoted_release_restores_explicit_previous_version(self):
        gate = "Verify promoted Worker deployment and full public release"
        restore_name = "Restore previous Worker after failed staged or production smoke"
        self.assertIn(restore_name, self.workflow)
        self.assertIn("failure() && steps.stage_deployment.outcome == 'success'", self.workflow)
        self.assertIn('npx wrangler rollback "$PREVIOUS_WORKER_VERSION_ID"', self.workflow)
        restore = self.workflow.index(restore_name)
        self.assertLess(self.workflow.index("Smoke staged Worker version before promotion"), restore)
        self.assertLess(self.workflow.index(gate), restore)
        self.assertLess(self.workflow.index("Verify public media gateway after promotion"), restore)
        self.assertLess(restore, self.workflow.index("Audit D1 structural integrity"))

    def test_migration_policy_gate_runs_before_remote_migrations(self):
        self.assertIn("Validate migration-first compatibility", self.workflow)
        self.assertIn("python tools/check_migration_deploy_policy.py", self.workflow)
        self.assertLess(self.workflow.index("Validate migration-first compatibility"), self.workflow.index("Apply D1 migrations"))

    def test_deploy_does_not_own_catalog_data_maintenance(self):
        self.assertNotIn("apply_legacy_gallery_repairs:", self.workflow)
        self.assertNotIn("backfill_image_source_index:", self.workflow)
        self.assertNotIn("Apply reviewed legacy gallery repairs", self.workflow)
        self.assertNotIn("Backfill image SHA index", self.workflow)
        self.assertNotIn("Verify repaired legacy aliases through public API", self.workflow)
        self.assertNotIn("config/catalog-legacy-gallery-overrides.json", self.workflow)
        self.assertNotIn("tools/apply_legacy_gallery_repairs.py", self.workflow)
        self.assertNotIn("tools/backfill_image_source_index.py", self.workflow)

    def test_deploy_workflow_does_not_self_trigger_on_orchestration_only_changes(self):
        self.assertNotIn(".github/workflows/cloudflare.yml", self.workflow)
        self.assertNotIn(".github/workflows/catalog-maintenance.yml", self.workflow)

    def test_deploy_watches_only_deploy_runtime_contracts(self):
        for path in (
            "config/catalog-schema-contract.json",
            "config/migration-deploy-policy.json",
            "tools/wrangler_config_contract.mjs",
            "tools/read_wrangler_d1_id.mjs",
            "tools/cloudflare_deploy_state.mjs",
            "tools/bounded_retry.mjs",
            "tools/worker_release_smoke.mjs",
            "tools/check_remote_worker_config.mjs",
            "tools/check_staged_worker_version.mjs",
            "tools/check_promoted_worker_version.mjs",
            "tools/verify_public_catalog.py",
            "tools/check_migration_deploy_policy.py",
            "tools/check_worker_health.mjs",
            "tools/audit_d1_integrity.py",
        ):
            self.assertIn(path, self.workflow)

    def test_worker_preview_urls_are_explicitly_disabled(self):
        self.assertIs(self.wrangler.get("preview_urls"), False)


class CatalogMaintenanceWorkflowConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github" / "workflows" / "catalog-maintenance.yml").read_text(encoding="utf-8")

    def test_maintenance_is_manual_and_one_operation_per_run(self):
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("operation:", self.workflow)
        self.assertIn("type: choice", self.workflow)
        self.assertIn("- audit", self.workflow)
        self.assertIn("- verify-legacy-gallery-repairs", self.workflow)
        self.assertIn("- backfill-image-sha", self.workflow)
        self.assertIn("- apply-legacy-gallery-repairs", self.workflow)
        self.assertNotIn("push:", self.workflow)

    def test_maintenance_serializes_with_production_deploy(self):
        self.assertIn("group: cloudflare-api", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertIn("environment: production", self.workflow)

    def test_maintenance_never_uploads_or_promotes_worker_or_runs_migrations(self):
        self.assertNotIn("wrangler versions upload", self.workflow)
        self.assertNotIn("wrangler versions deploy", self.workflow)
        self.assertNotIn("wrangler rollback", self.workflow)
        self.assertNotIn("d1 migrations apply", self.workflow)
        self.assertNotIn("TURNSTILE_SECRET_KEY", self.workflow)

    def test_maintenance_uses_canonical_d1_binding_and_worker_health_before_write(self):
        health = "Validate active Worker health and schema"
        self.assertIn("node tools/read_wrangler_d1_id.mjs", self.workflow)
        self.assertIn('CLOUDFLARE_D1_DATABASE_ID=$database_id', self.workflow)
        self.assertIn(health, self.workflow)
        self.assertIn("node tools/check_worker_health.mjs", self.workflow)
        self.assertLess(self.workflow.index(health), self.workflow.index("Backfill image SHA index"))
        self.assertLess(self.workflow.index(health), self.workflow.index("Apply reviewed legacy gallery repairs"))

    def test_backfill_is_bounded_by_coverage_checks(self):
        before = "Measure image SHA index coverage"
        apply_step = "Backfill image SHA index"
        after = "Verify image SHA index coverage after backfill"
        self.assertIn("inputs.operation == 'backfill-image-sha'", self.workflow)
        self.assertIn("python tools/backfill_image_source_index.py --apply", self.workflow)
        self.assertLess(self.workflow.index(before), self.workflow.index(apply_step))
        self.assertLess(self.workflow.index(apply_step), self.workflow.index(after))

    def test_gallery_repair_verifies_public_api_before_expensive_reaudit(self):
        plan = "Plan reviewed legacy gallery repairs"
        apply_step = "Apply reviewed legacy gallery repairs"
        public_smoke = "Verify repaired legacy aliases through public API"
        reaudit = "Re-audit D1 after legacy gallery verification"
        self.assertIn("inputs.operation == 'apply-legacy-gallery-repairs'", self.workflow)
        self.assertIn("python tools/apply_legacy_gallery_repairs.py --apply", self.workflow)
        self.assertIn("node tools/check_legacy_gallery_repairs.mjs", self.workflow)
        self.assertLess(self.workflow.index(plan), self.workflow.index(apply_step))
        self.assertLess(self.workflow.index(apply_step), self.workflow.index(public_smoke))
        self.assertLess(self.workflow.index(public_smoke), self.workflow.index(reaudit))

    def test_legacy_gallery_verification_is_read_only_and_reaudits_after_public_smoke(self):
        public_smoke = "Verify repaired legacy aliases through public API"
        reaudit = "Re-audit D1 after legacy gallery verification"
        self.assertIn("inputs.operation == 'verify-legacy-gallery-repairs'", self.workflow)
        self.assertIn("inputs.operation == 'apply-legacy-gallery-repairs' || inputs.operation == 'verify-legacy-gallery-repairs'", self.workflow)
        self.assertLess(self.workflow.index(public_smoke), self.workflow.index(reaudit))
        verify_clause = "inputs.operation == 'verify-legacy-gallery-repairs'"
        apply_clause = "inputs.operation == 'apply-legacy-gallery-repairs'"
        self.assertIn(verify_clause, self.workflow)
        self.assertIn(apply_clause, self.workflow)
        self.assertNotIn("verify-legacy-gallery-repairs' }}\n        run: python tools/apply_legacy_gallery_repairs.py --apply", self.workflow)
        self.assertNotIn("verify-legacy-gallery-repairs' }}\n        run: python tools/backfill_image_source_index.py --apply", self.workflow)

    def test_media_origin_is_derived_from_canonical_api_without_separate_repository_variable(self):
        self.assertNotIn("vars.VITE_MEDIA_BASE_URL", self.workflow)
        self.assertIn('echo "CATALOG_MEDIA_URL=${CATALOG_API_URL%/}" >> "$GITHUB_ENV"', self.workflow)
        self.assertIn("Validate maintenance credentials and derive media origin", self.workflow)


if __name__ == "__main__":
    unittest.main()
