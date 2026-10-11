import unittest
from pathlib import Path


class PagesWorkflowConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")

    def test_runtime_secrets_and_endpoints_use_repository_variables(self):
        required = [
            "VITE_API_BASE_URL: ${{ vars.VITE_API_BASE_URL }}",
            "VITE_TURNSTILE_SITE_KEY: ${{ vars.VITE_TURNSTILE_SITE_KEY }}",
        ]
        for entry in required:
            self.assertIn(entry, self.workflow)
        self.assertNotIn("VITE_MEDIA_BASE_URL: ${{ vars.VITE_MEDIA_BASE_URL }}", self.workflow)
        self.assertNotIn("VITE_PUBLIC_SITE_URL: ${{ vars.VITE_PUBLIC_SITE_URL }}", self.workflow)

    def test_public_site_url_comes_from_versioned_ssot(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "config" / "public-runtime.json").read_text(encoding="utf-8")
        vite = (root / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("https://acheguese.com.br/tonecosstudios/", runtime)
        self.assertIn("publicRuntime.publicSiteUrl", vite)
        self.assertIn("config/public-runtime.json", self.workflow)

    def test_public_domain_documentation_matches_versioned_ssot(self):
        root = Path(__file__).resolve().parents[1]
        documentation = (root / "docs" / "DOMINIO-E-URL-PUBLICA.md").read_text(encoding="utf-8")
        self.assertIn("config/public-runtime.json", documentation)
        self.assertIn("https://acheguese.com.br/tonecosstudios/", documentation)
        self.assertIn("src/shared/config/publicExternalApps.config.ts", documentation)
        self.assertNotIn("Configure a Repository Variable `VITE_PUBLIC_SITE_URL`", documentation)

    def test_legacy_catalog_public_url_is_not_canonical_anymore(self):
        root = Path(__file__).resolve().parents[1]
        legacy_url = "https://acheguese.com.br/catalogo/"
        checked = [
            root / "config" / "public-runtime.json",
            root / "README.md",
            root / "docs" / "DOMINIO-E-URL-PUBLICA.md",
            root / "docs" / "CLOUDFLARE_DEPLOY.md",
        ]
        for path in checked:
            self.assertNotIn(legacy_url, path.read_text(encoding="utf-8"), str(path))

    def test_pages_workflow_has_no_production_endpoint_fallbacks(self):
        forbidden = [
            "tonecos-catalogo-api.ordax-ac1ca1b50d09.workers.dev",
            "pub-1a55c50ce21d47bc9200eef58527ee4b.r2.dev",
            "0x4AAAAAAFKrlH5Vf65-ffVq",
            "${PUBLIC_SITE_URL:-",
        ]
        for value in forbidden:
            self.assertNotIn(value, self.workflow)

    def test_missing_runtime_configuration_fails_before_build(self):
        self.assertIn("Validate public runtime configuration", self.workflow)
        self.assertIn("Missing required Repository Variables", self.workflow)
        self.assertIn("exit 1", self.workflow)

    def test_pages_rejects_any_legacy_r2_dev_endpoint_from_build(self):
        self.assertIn("Reject legacy public R2 endpoint from generated frontend", self.workflow)
        self.assertIn("\\.r2\\.dev", self.workflow)
        self.assertIn("mídia deve sair exclusivamente pelo Worker", self.workflow)

    def test_catalog_uses_first_party_api_proxy_only_on_acheguese_domain(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src" / "services" / "catalogApi.ts").read_text(encoding="utf-8")
        self.assertIn("hostname === 'acheguese.com.br'", source)
        self.assertIn("hostname === 'www.acheguese.com.br'", source)
        self.assertIn("${window.location.origin}/catalogo-api", source)
        self.assertIn("return configuredApiBase", source)

    def test_media_build_alias_is_derived_only_from_api_base(self):
        root = Path(__file__).resolve().parents[1]
        vite = (root / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("environment.VITE_API_BASE_URL", vite)
        self.assertIn("'import.meta.env.VITE_MEDIA_BASE_URL': JSON.stringify(apiBase)", vite)
        self.assertNotIn("environment.VITE_MEDIA_BASE_URL", vite)

    def test_published_brand_assets_are_validated_from_ssot(self):
        required = [
            "config/brand.json",
            'SHORT_NAME="$(node -e',
            'BRAND_INITIAL="$(node -e',
            "data.short_name !== expectedShort",
            'FAVICON_URL="${PAGE_URL%/}/favicon.svg"',
            'grep -Fq ">$BRAND_INITIAL</text>"',
        ]
        for entry in required:
            self.assertIn(entry, self.workflow)


if __name__ == "__main__":
    unittest.main()
