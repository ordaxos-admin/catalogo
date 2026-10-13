import tempfile
import unittest
from pathlib import Path

from resolve_gallery_merge import read_decisions, resolve_gallery_merge


def image(path: str, sha: str, *, watermark: str = "legacy_accepted") -> dict:
    return {
        "source": "incoming",
        "path": path,
        "sha256": sha,
        "dhash": "0000000000000000",
        "qualityScore": 80.0,
        "width": 800,
        "height": 1200,
        "bytes": 100000,
        "watermarkStatus": watermark,
    }


def action(kind: str, path: str, sha: str, *, model: str = "Games / Saga / Heroi / modelo-01", matches=None, watermark: str = "legacy_accepted") -> dict:
    return {
        "model": model,
        "sourceModel": "fonte / produto-01",
        "action": kind,
        "incoming": image(path, sha, watermark=watermark),
        "matches": matches or [],
    }


class ResolveGalleryMergeTests(unittest.TestCase):
    def test_automatic_actions_are_resolved_without_decisions(self):
        plan = {
            "version": 1,
            "actions": [
                action("add_view", "fonte/costas.jpg", "a" * 64),
                action("skip_exact", "fonte/frente.jpg", "b" * 64),
            ],
        }

        resolved = resolve_gallery_merge(plan)

        self.assertTrue(resolved["ready"])
        self.assertEqual(resolved["counts"], {"promote_add_view": 1, "skip_exact": 1})
        self.assertEqual(len(resolved["promotions"]), 1)
        self.assertEqual(resolved["promotions"][0]["mode"], "add_view")
        self.assertEqual(resolved["policy"]["destructiveDeletes"], 0)

    def test_visual_candidate_requires_explicit_decision(self):
        plan = {
            "version": 1,
            "actions": [
                action(
                    "review_visual_candidate",
                    "fonte/frente-hq.png",
                    "c" * 64,
                    matches=[{"sha256": "d" * 64}],
                )
            ],
        }

        with self.assertRaisesRegex(RuntimeError, "sem decisão explícita"):
            resolve_gallery_merge(plan)

    def test_replace_existing_requires_candidate_sha(self):
        key = ("fonte/frente-hq.png", "c" * 64)
        plan = {
            "version": 1,
            "actions": [
                action(
                    "review_visual_candidate",
                    key[0],
                    key[1],
                    matches=[{"sha256": "d" * 64}],
                )
            ],
        }

        with self.assertRaisesRegex(RuntimeError, "não pertence aos candidatos"):
            resolve_gallery_merge(
                plan,
                {key: {"decision": "replace_existing", "replace_sha256": "e" * 64}},
            )

    def test_replace_existing_creates_non_destructive_promotion(self):
        key = ("fonte/frente-hq.png", "c" * 64)
        plan = {
            "version": 1,
            "actions": [
                action(
                    "review_visual_candidate",
                    key[0],
                    key[1],
                    matches=[{"sha256": "d" * 64}],
                )
            ],
        }

        resolved = resolve_gallery_merge(
            plan,
            {key: {"decision": "replace_existing", "replace_sha256": "d" * 64}},
        )

        self.assertEqual(resolved["counts"], {"promote_replace_existing": 1})
        promotion = resolved["promotions"][0]
        self.assertEqual(promotion["mode"], "replace_existing")
        self.assertEqual(promotion["replaceSha256"], "d" * 64)
        self.assertEqual(resolved["policy"]["sourceFilesModified"], 0)

    def test_visual_candidate_can_be_kept_or_added_as_distinct_view(self):
        first = ("fonte/angulo.jpg", "1" * 64)
        second = ("fonte/lateral.jpg", "2" * 64)
        plan = {
            "version": 1,
            "actions": [
                action("review_visual_candidate", first[0], first[1], matches=[{"sha256": "3" * 64}]),
                action("review_visual_candidate", second[0], second[1], matches=[{"sha256": "4" * 64}]),
            ],
        }

        resolved = resolve_gallery_merge(
            plan,
            {
                first: {"decision": "keep_existing", "replace_sha256": ""},
                second: {"decision": "add_view", "replace_sha256": ""},
            },
        )

        self.assertEqual(resolved["counts"], {"keep_existing": 1, "promote_add_view": 1})
        self.assertEqual(len(resolved["keptExisting"]), 1)
        self.assertEqual(len(resolved["promotions"]), 1)

    def test_unused_decision_fails_closed(self):
        plan = {
            "version": 1,
            "actions": [action("add_view", "fonte/costas.jpg", "a" * 64)],
        }
        extra = {("fonte/extra.jpg", "f" * 64): {"decision": "add_view", "replace_sha256": ""}}

        with self.assertRaisesRegex(RuntimeError, "sem candidato visual correspondente"):
            resolve_gallery_merge(plan, extra)

    def test_decisions_csv_validates_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.csv"
            path.write_text(
                "incoming_path,incoming_sha256,decision,replace_sha256\n"
                f"fonte/frente.png,{'a' * 64},replace_existing,{'b' * 64}\n",
                encoding="utf-8",
            )
            decisions = read_decisions(path)
            self.assertEqual(
                decisions[("fonte/frente.png", "a" * 64)]["decision"],
                "replace_existing",
            )

            path.write_text(
                "incoming_path,incoming_sha256,decision,replace_sha256\n"
                f"fonte/frente.png,{'a' * 64},apagar,\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "decisão inválida"):
                read_decisions(path)

    def test_unknown_external_watermark_requires_upstream_media_audit(self):
        plan = {
            "version": 1,
            "actions": [
                action(
                    "review_watermark_required",
                    "fonte/sem-auditoria.jpg",
                    "9" * 64,
                    watermark="unknown",
                )
            ],
        }

        with self.assertRaisesRegex(RuntimeError, "--media-audit"):
            resolve_gallery_merge(plan)

    def test_watermarked_rejection_is_recorded_and_never_promoted(self):
        plan = {
            "version": 1,
            "actions": [
                action(
                    "reject_watermarked",
                    "fonte/com-marca.jpg",
                    "8" * 64,
                    watermark="watermarked",
                )
            ],
        }

        resolved = resolve_gallery_merge(plan)

        self.assertTrue(resolved["ready"])
        self.assertEqual(resolved["counts"], {"reject_watermarked": 1})
        self.assertEqual(resolved["promotions"], [])
        self.assertEqual(len(resolved["rejectedWatermarked"]), 1)
        self.assertEqual(resolved["policy"]["watermarkedIncomingPromoted"], 0)

    def test_tampered_add_view_cannot_bypass_watermark_gate(self):
        plan = {
            "version": 1,
            "actions": [
                action(
                    "add_view",
                    "fonte/com-marca.jpg",
                    "7" * 64,
                    watermark="watermarked",
                )
            ],
        }

        with self.assertRaisesRegex(RuntimeError, "add_view bloqueado"):
            resolve_gallery_merge(plan)


if __name__ == "__main__":
    unittest.main()
