from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
BRAND_PATH = ROOT / "config" / "brand.json"
PROVENANCE_ALLOWLIST_RELATIVE = "config/brand-provenance-allowlist.json"
PROVENANCE_ALLOWLIST_PATH = ROOT / PROVENANCE_ALLOWLIST_RELATIVE
FORBIDDEN_MARKS = [
    bytes((115, 116, 108, 102, 111, 114, 103, 101)),
    bytes((115, 116, 108, 32, 102, 111, 114, 103, 101)),
    bytes((115, 116, 108, 45, 102, 111, 114, 103, 101)),
    bytes((115, 116, 108, 95, 102, 111, 114, 103, 101)),
]
ALLOWED_PROVENANCE_PREFIXES = (
    ".github/workflows/",
    "config/external-batches/",
    "docs/",
    "tools/",
)
FORBIDDEN_PROVENANCE_PREFIXES = ("src/", "public/", "worker/")
FORBIDDEN_PROVENANCE_EXACT = {"index.html", "vite.config.ts", "config/brand.json"}


def tracked_and_untracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def contains_forbidden_mark(data: bytes) -> bool:
    lowered = data.lower()
    return any(mark in lowered for mark in FORBIDDEN_MARKS)


def load_provenance_allowlist() -> set[str]:
    data = json.loads(PROVENANCE_ALLOWLIST_PATH.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise AssertionError("config/brand-provenance-allowlist.json: version deve ser 1")
    purpose = data.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        raise AssertionError("config/brand-provenance-allowlist.json: purpose obrigatório")
    paths = data.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(item, str) for item in paths):
        raise AssertionError("config/brand-provenance-allowlist.json: paths deve ser lista não vazia")
    if len(paths) != len(set(paths)):
        raise AssertionError("config/brand-provenance-allowlist.json: paths duplicados")
    if paths != sorted(paths):
        raise AssertionError("config/brand-provenance-allowlist.json: paths deve permanecer ordenado")

    allowed: set[str] = set()
    for relative in paths:
        normalized = relative.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if normalized != relative or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise AssertionError(f"allowlist contém caminho não canônico: {relative}")
        if relative in FORBIDDEN_PROVENANCE_EXACT or relative.startswith(FORBIDDEN_PROVENANCE_PREFIXES):
            raise AssertionError(f"allowlist não pode liberar superfície user-facing/runtime: {relative}")
        if not relative.startswith(ALLOWED_PROVENANCE_PREFIXES):
            raise AssertionError(f"allowlist fora das raízes de proveniência permitidas: {relative}")
        path = ROOT / relative
        if not path.is_file():
            raise AssertionError(f"allowlist aponta para arquivo ausente: {relative}")
        path_marked = contains_forbidden_mark(relative.encode("utf-8"))
        content_marked = contains_forbidden_mark(path.read_bytes())
        if not path_marked and not content_marked:
            raise AssertionError(f"allowlist contém exceção stale/desnecessária: {relative}")
        allowed.add(relative)
    return allowed


class BrandIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.brand = json.loads(BRAND_PATH.read_text(encoding="utf-8"))
        cls.provenance_allowlist = load_provenance_allowlist()
        cls.provenance_scan_exemptions = cls.provenance_allowlist | {PROVENANCE_ALLOWLIST_RELATIVE}

    def test_brand_definition_has_required_fields(self) -> None:
        for key in ("name", "shortName", "catalogLabel"):
            value = self.brand.get(key)
            self.assertIsInstance(value, str)
            self.assertTrue(value.strip(), f"config/brand.json: {key} vazio")

    def test_external_source_provenance_allowlist_is_narrow_and_non_user_facing(self) -> None:
        self.assertTrue(self.provenance_allowlist)
        self.assertFalse(any(path.startswith(FORBIDDEN_PROVENANCE_PREFIXES) for path in self.provenance_allowlist))
        self.assertTrue(all(path.startswith(ALLOWED_PROVENANCE_PREFIXES) for path in self.provenance_allowlist))

    def test_competitor_name_is_absent_from_every_non_provenance_file(self) -> None:
        offenders: list[str] = []
        for relative in tracked_and_untracked_files():
            if relative in self.provenance_scan_exemptions:
                continue
            path = ROOT / relative
            if not path.is_file():
                continue
            if contains_forbidden_mark(path.read_bytes()):
                offenders.append(relative)
        self.assertEqual(offenders, [], f"identidade concorrente encontrada fora de proveniência autorizada: {offenders}")

    def test_competitor_name_is_absent_from_non_provenance_paths(self) -> None:
        offenders: list[str] = []
        for relative in tracked_and_untracked_files():
            if relative in self.provenance_scan_exemptions:
                continue
            normalized = relative.replace("\\", "/").encode("utf-8")
            if contains_forbidden_mark(normalized):
                offenders.append(relative)
        self.assertEqual(offenders, [], f"identidade concorrente encontrada em caminho não autorizado: {offenders}")

    def test_runtime_sources_do_not_hardcode_current_brand_name(self) -> None:
        needle = self.brand["name"].encode("utf-8").lower()
        offenders: list[str] = []
        for pattern in ("*.ts", "*.tsx"):
            for path in (ROOT / "src").rglob(pattern):
                if needle in path.read_bytes().lower():
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], f"marca hardcoded fora do SSOT: {offenders}")

    def test_current_brand_literal_exists_only_in_ssot(self) -> None:
        needle = self.brand["name"].encode("utf-8").lower()
        offenders: list[str] = []
        for relative in tracked_and_untracked_files():
            if relative == "config/brand.json":
                continue
            path = ROOT / relative
            if path.is_file() and needle in path.read_bytes().lower():
                offenders.append(relative)
        self.assertEqual(offenders, [], f"literal da marca fora do SSOT: {offenders}")

    def test_user_facing_short_brand_is_derived_from_ssot(self) -> None:
        home = (ROOT / "src" / "components" / "CatalogHome.tsx").read_text(encoding="utf-8")
        collections = (ROOT / "src" / "components" / "CollectionsDock.tsx").read_text(encoding="utf-8")
        brand_source = (ROOT / "src" / "config" / "brand.ts").read_text(encoding="utf-8")

        self.assertNotIn(f"Acervo {self.brand['shortName']}", home)
        self.assertIn("`Acervo ${BRAND_SHORT_NAME}`", home)
        self.assertNotIn(f"{self.brand['shortName'].lower()}-colecoes-", collections.lower())
        self.assertIn("BRAND_FILE_SLUG", collections)
        self.assertIn("export const BRAND_FILE_SLUG", brand_source)

    def test_local_check_includes_source_brand_gate(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(
            package["scripts"].get("brand:source"),
            "python -m unittest -q tools.test_brand_identity",
        )
        self.assertIn("npm run brand:source", package["scripts"]["check"])

    def test_generated_build_is_validated_before_publish(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        checker = (ROOT / "tools" / "check_generated_brand.mjs").read_bytes().lower()

        self.assertIn("brand:dist", package["scripts"])
        self.assertIn("npm run brand:dist", package["scripts"]["check"])
        self.assertIn("Validate generated brand artifacts", workflow)
        self.assertIn("npm run brand:dist", workflow)
        self.assertFalse(any(mark in checker for mark in FORBIDDEN_MARKS))

    def test_static_metadata_is_generated_from_brand_ssot(self) -> None:
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        vite = (ROOT / "vite.config.ts").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")

        self.assertIn("__BRAND_NAME__", index)
        self.assertIn("__CATALOG_PAGE_TITLE__", index)
        self.assertNotIn(self.brand["name"], index)
        self.assertIn("import brand from './config/brand.json'", vite)
        self.assertIn("fileName: 'site.webmanifest'", vite)
        self.assertIn("fileName: 'favicon.svg'", vite)
        self.assertIn("config/brand.json", workflow)
        self.assertFalse((ROOT / "public" / "site.webmanifest").exists())
        self.assertFalse((ROOT / "public" / "favicon.svg").exists())


if __name__ == "__main__":
    unittest.main()
