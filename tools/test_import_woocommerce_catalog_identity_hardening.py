from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

MODULE_PATH = TOOLS_DIR / "import_woocommerce_catalog.py"
SPEC = importlib.util.spec_from_file_location("import_woocommerce_catalog_identity_hardening", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

ImportContractError = module.ImportContractError
stable_image_identity = module.stable_image_identity
stable_source_id = module.stable_source_id


class ImportWooCommerceIdentityHardeningTests(unittest.TestCase):
    def test_source_id_is_a_strict_path_safe_namespace(self) -> None:
        self.assertEqual(stable_source_id("vendor-a"), "vendor-a")
        self.assertEqual(stable_source_id("vendor.a_2"), "vendor.a_2")

        for invalid in (
            "../vendor",
            "vendor/child",
            "vendor\\child",
            "/absolute",
            ".vendor",
            "vendor.",
            "Vendor",
            "vendor a",
            " vendor",
            "vendor ",
            "",
        ):
            with self.subTest(source_id=invalid):
                with self.assertRaisesRegex(ImportContractError, "sourceId inválido"):
                    stable_source_id(invalid)

    def test_explicit_provider_image_id_must_be_positive_decimal(self) -> None:
        url = "https://cdn.vendor.example/image.webp"
        for invalid in (0, -1, True, "", "abc", "1/2", "1-2"):
            with self.subTest(image_id=invalid):
                with self.assertRaisesRegex(ImportContractError, "inteiro positivo"):
                    stable_image_identity({"id": invalid}, url)

    def test_provider_image_id_is_canonicalized_before_path_identity(self) -> None:
        url = "https://cdn.vendor.example/image.webp"
        self.assertEqual(stable_image_identity({"id": 7}, url), ("7", "provider_id"))
        self.assertEqual(stable_image_identity({"id": "0007"}, url), ("7", "provider_id"))

    def test_missing_provider_image_id_uses_url_hash_fallback(self) -> None:
        url = "https://cdn.vendor.example/image.webp"
        identity, kind = stable_image_identity({}, url)
        self.assertEqual(kind, "url_sha256")
        self.assertTrue(identity.startswith("url-sha256-"))
        self.assertEqual(len(identity), len("url-sha256-") + 64)


if __name__ == "__main__":
    unittest.main()
