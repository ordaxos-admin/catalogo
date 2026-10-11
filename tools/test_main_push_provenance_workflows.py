import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MainPushProvenanceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        cls.pages = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        cls.cloudflare = (ROOT / ".github" / "workflows" / "cloudflare.yml").read_text(encoding="utf-8")

    def test_all_main_push_workflows_have_read_only_pr_lookup_permission(self):
        for name, workflow in (("ci", self.ci), ("pages", self.pages), ("cloudflare", self.cloudflare)):
            with self.subTest(workflow=name):
                self.assertIn("pull-requests: read", workflow)
                self.assertIn("contents: read", workflow)

    def test_all_main_push_workflows_invoke_the_same_provenance_gate(self):
        required = [
            "Verify main push provenance",
            "node tools/check_main_push_provenance.mjs",
            "GITHUB_TOKEN: ${{ github.token }}",
            "GITHUB_EVENT_NAME: ${{ github.event_name }}",
            "GITHUB_REF_NAME: ${{ github.ref_name }}",
        ]
        for name, workflow in (("ci", self.ci), ("pages", self.pages), ("cloudflare", self.cloudflare)):
            with self.subTest(workflow=name):
                for entry in required:
                    self.assertIn(entry, workflow)

    def test_ci_and_pages_verify_provenance_before_install_or_publish(self):
        for name, workflow in (("ci", self.ci), ("pages", self.pages)):
            with self.subTest(workflow=name):
                gate = workflow.index("Verify main push provenance")
                self.assertLess(gate, workflow.index("Install"))
        self.assertLess(self.pages.index("Verify main push provenance"), self.pages.index("Deploy\n"))

    def test_cloudflare_verifies_provenance_in_preflight_before_bootstrap_and_worker_write(self):
        gate = self.cloudflare.index("Verify main push provenance")
        self.assertLess(gate, self.cloudflare.index("Check production bootstrap readiness"))
        self.assertLess(gate, self.cloudflare.index("Upload Worker version with required secret and no traffic"))
        self.assertIn("tools/check_main_push_provenance.mjs", self.cloudflare)

    def test_provenance_script_is_a_cloudflare_deploy_contract(self):
        paths_block = self.cloudflare.split("permissions:", 1)[0]
        self.assertIn("- 'tools/check_main_push_provenance.mjs'", paths_block)


if __name__ == "__main__":
    unittest.main()
