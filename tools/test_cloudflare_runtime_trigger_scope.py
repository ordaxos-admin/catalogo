import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CloudflareRuntimeTriggerScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github" / "workflows" / "cloudflare.yml").read_text(encoding="utf-8")
        cls.trigger_block = cls.workflow.split("permissions:", 1)[0]

    def test_frontend_package_manifest_does_not_trigger_worker_rollout(self):
        self.assertNotIn("- 'package.json'", self.trigger_block)
        self.assertNotIn("- 'package-lock.json'", self.trigger_block)

    def test_worker_runtime_and_schema_contracts_still_trigger_deploy(self):
        for path in (
            "- 'worker/**'",
            "- 'migrations/**'",
            "- 'wrangler.jsonc'",
            "- 'config/catalog-schema-contract.json'",
            "- 'config/migration-deploy-policy.json'",
            "- 'tools/check_remote_worker_config.mjs'",
            "- 'tools/check_staged_worker_version.mjs'",
            "- 'tools/check_promoted_worker_version.mjs'",
            "- 'tools/verify_public_catalog.py'",
        ):
            with self.subTest(path=path):
                self.assertIn(path, self.trigger_block)

    def test_orchestration_files_do_not_self_trigger_runtime_rollout(self):
        self.assertNotIn(".github/workflows/cloudflare.yml", self.trigger_block)
        self.assertNotIn(".github/workflows/catalog-maintenance.yml", self.trigger_block)


if __name__ == "__main__":
    unittest.main()
