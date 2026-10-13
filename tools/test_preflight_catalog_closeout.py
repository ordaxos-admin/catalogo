from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

MODULE_PATH = TOOLS_DIR / "preflight_catalog_closeout.py"
SPEC = importlib.util.spec_from_file_location("preflight_catalog_closeout", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

CloseoutError = module.CloseoutError
closeout = module.closeout


class PreflightCatalogCloseoutTests(unittest.TestCase):
    def paths(self, tmp: str):
        base = Path(tmp)
        repo = base / "repo"
        catalog = base / "Catalogo"
        incoming = base / "incoming"
        repo.mkdir()
        catalog.mkdir()
        incoming.mkdir()
        registry = catalog / "audit.csv"
        mapping = base / "mapping.json"
        manifest = repo / "manifest.json"
        registry.write_text("stub", encoding="utf-8")
        mapping.write_text("{}", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")
        ingest = repo / ".catalog-ingest"
        bundle = repo / ".publish-bundle"
        report = ingest / "closeout-preflight.json"
        return repo, catalog, registry, mapping, incoming, manifest, ingest, bundle, report

    def test_run_step_forces_utf8_subprocess_boundary(self) -> None:
        completed = module.subprocess.CompletedProcess(
            args=[sys.executable, "tool.py"],
            returncode=0,
            stdout='{"mensagem":"ação — café"}\n',
            stderr="",
        )
        caller_env = {"SENTINEL": "preserved"}
        with patch.object(module.subprocess, "run", return_value=completed) as mocked:
            result = module.run_step(Path("."), "unicode-step", ["tool.py"], env=caller_env)

        self.assertEqual(result["summary"]["mensagem"], "ação — café")
        kwargs = mocked.call_args.kwargs
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "strict")
        self.assertEqual(kwargs["env"]["SENTINEL"], "preserved")
        self.assertEqual(kwargs["env"]["PYTHONUTF8"], "1")
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(caller_env, {"SENTINEL": "preserved"})

    def test_dry_run_stops_before_local_mutation_when_copies_are_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.paths(tmp)
            with patch.object(
                module,
                "run_step",
                return_value={
                    "name": "recovery-materialize-dry-run",
                    "durationMs": 1,
                    "summary": {"pendingCopies": 3},
                },
            ) as mocked:
                result = closeout(
                    repo_root=args[0],
                    catalog_root=args[1],
                    registry=args[2],
                    mapping=args[3],
                    incoming_root=args[4],
                    manifest=args[5],
                    ingest_output=args[6],
                    bundle_output=args[7],
                    report_path=args[8],
                    apply_local=False,
                )
            self.assertEqual(result["status"], "local_changes_required")
            self.assertEqual(result["pendingLocalCopies"], 3)
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(result["productionWrites"], 0)

    def test_apply_local_runs_full_preflight_without_cloud_apply_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.paths(tmp)
            summaries = {
                "recovery-materialize-dry-run": {"pendingCopies": 3},
                "recovery-materialize-apply": {"completeCoverage": True, "verified": 3},
                "recovery-verify": {"completeCoverage": True, "verified": 3},
                "recovery-registry-dry-run": {"pendingUpdates": 3},
                "recovery-registry-apply": {"pendingUpdates": 0, "alreadyApplied": 3},
                "ingest-fresh": {"images": 10, "ok": 10, "errors": 0},
                "bundle-v8-fresh": {"models": 7, "canonicalImages": 10, "variantFiles": 30},
                "r2-dry-run": {"dryRun": True},
                "d1-check-production": {"checkProduction": True},
                "legacy-gallery-repairs-preflight": {"apply": False, "pendingRelations": 2},
            }
            calls: list[tuple[str, list[str]]] = []

            def fake_run_step(_repo, name, argv, env=None):
                calls.append((name, list(argv)))
                return {"name": name, "durationMs": 1, "summary": summaries[name]}

            with patch.object(module, "run_step", side_effect=fake_run_step):
                result = closeout(
                    repo_root=args[0],
                    catalog_root=args[1],
                    registry=args[2],
                    mapping=args[3],
                    incoming_root=args[4],
                    manifest=args[5],
                    ingest_output=args[6],
                    bundle_output=args[7],
                    report_path=args[8],
                    apply_local=True,
                )

            self.assertEqual(result["status"], "preflight_green")
            self.assertEqual(result["productionWrites"], 0)
            by_name = {name: argv for name, argv in calls}
            self.assertIn("--apply", by_name["recovery-materialize-apply"])
            self.assertIn("--apply", by_name["recovery-registry-apply"])
            self.assertIn("--dry-run", by_name["r2-dry-run"])
            self.assertNotIn("--apply", by_name["r2-dry-run"])
            self.assertIn("--check-production", by_name["d1-check-production"])
            self.assertNotIn("--apply", by_name["d1-check-production"])
            self.assertNotIn("--apply", by_name["legacy-gallery-repairs-preflight"])
            self.assertIn("--no-resume", by_name["ingest-fresh"])
            self.assertIn("--no-resume", by_name["bundle-v8-fresh"])

    def test_non_integral_ingest_aborts_before_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.paths(tmp)
            summaries = {
                "recovery-materialize-dry-run": {"pendingCopies": 0},
                "recovery-verify": {"completeCoverage": True, "verified": 3},
                "recovery-registry-dry-run": {"pendingUpdates": 0},
                "ingest-fresh": {"images": 10, "ok": 9, "errors": 1},
            }
            calls: list[str] = []

            def fake_run_step(_repo, name, argv, env=None):
                calls.append(name)
                return {"name": name, "durationMs": 1, "summary": summaries[name]}

            with patch.object(module, "run_step", side_effect=fake_run_step):
                with self.assertRaisesRegex(CloseoutError, "integralmente OK"):
                    closeout(
                        repo_root=args[0],
                        catalog_root=args[1],
                        registry=args[2],
                        mapping=args[3],
                        incoming_root=args[4],
                        manifest=args[5],
                        ingest_output=args[6],
                        bundle_output=args[7],
                        report_path=args[8],
                        apply_local=False,
                    )
            self.assertNotIn("bundle-v8-fresh", calls)


if __name__ == "__main__":
    unittest.main()
