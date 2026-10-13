import tempfile
import unittest
from pathlib import Path

from plan_gallery_merge import plan_gallery_merge, read_identity_mapping


def row(
    path: str,
    sha: str,
    dhash: str,
    quality: float,
    *,
    model: str = "Games / Saga / Heroi / heroi-modelo-01",
    width: int = 800,
    height: int = 1200,
    size: int = 100_000,
    watermark: str | None = None,
    external: bool = False,
):
    result = {
        "path": path,
        "status": "OK",
        "canonical": True,
        "sha256": sha,
        "dhash": dhash,
        "quality_score": quality,
        "width": width,
        "height": height,
        "size": size,
        "model_key": "Games / Saga / Heroi",
        "public_model_key": model,
    }
    if watermark is not None:
        result["watermark_status"] = watermark
    if external:
        result["source_provenance"] = {"kind": "AUTHORIZED_EXTERNAL_MEDIA"}
    return result


class GalleryMergePlannerTests(unittest.TestCase):
    def test_exact_duplicate_is_skipped_without_mutation(self):
        existing = [row("catalogo/frente.jpg", "a" * 64, "0000000000000000", 70)]
        incoming = [row("fonte-externa/frente.png", "a" * 64, "0000000000000000", 95, width=1800, height=2400)]

        plan = plan_gallery_merge(existing, incoming)

        self.assertEqual(plan["counts"], {"skip_exact": 1})
        self.assertEqual(plan["actions"][0]["action"], "skip_exact")
        self.assertEqual(plan["policy"]["destructiveDeletes"], 0)

    def test_distinct_front_back_views_are_both_kept(self):
        existing = [row("catalogo/frente.jpg", "a" * 64, "0000000000000000", 70)]
        incoming = [row("fonte-externa/costas.jpg", "b" * 64, "ffffffffffffffff", 85)]

        plan = plan_gallery_merge(existing, incoming)

        self.assertEqual(plan["counts"], {"add_view": 1})
        self.assertEqual(plan["actions"][0]["action"], "add_view")

    def test_near_visual_higher_quality_incoming_is_reviewed_and_preferred(self):
        existing = [row("catalogo/frente.jpg", "a" * 64, "0000000000000000", 55, width=600, height=900)]
        incoming = [row("fonte-externa/frente-hq.png", "b" * 64, "0000000000000001", 92, width=1800, height=2700)]

        plan = plan_gallery_merge(existing, incoming)

        self.assertEqual(plan["counts"]["review_visual_candidate"], 1)
        self.assertEqual(plan["counts"]["review_prefers_incoming"], 1)
        action = plan["actions"][0]
        self.assertEqual(action["action"], "review_visual_candidate")
        self.assertEqual(action["recommendedSource"], "incoming")
        self.assertEqual(action["matches"][0]["dhashDistance"], 1)

    def test_near_visual_lower_quality_incoming_does_not_replace_existing(self):
        existing = [row("catalogo/frente-hq.png", "a" * 64, "0000000000000000", 95, width=1800, height=2700)]
        incoming = [row("fonte-externa/frente-low.jpg", "b" * 64, "0000000000000003", 40, width=500, height=750)]

        plan = plan_gallery_merge(existing, incoming)

        self.assertEqual(plan["counts"]["review_prefers_existing"], 1)
        self.assertEqual(plan["actions"][0]["recommendedSource"], "existing_or_planned")

    def test_multiple_incoming_views_for_same_model_remain_one_model_plan(self):
        existing = []
        incoming = [
            row("fonte-externa/frente.jpg", "a" * 64, "0000000000000000", 80),
            row("fonte-externa/costas.jpg", "b" * 64, "ffffffffffffffff", 78),
            row("fonte-externa/lateral.jpg", "c" * 64, "aaaaaaaaaaaaaaaa", 76),
        ]

        plan = plan_gallery_merge(existing, incoming)

        self.assertEqual(plan["modelsTouched"], 1)
        self.assertEqual(plan["counts"], {"add_view": 3})

    def test_incoming_exact_duplicate_inside_batch_is_added_once(self):
        incoming = [
            row("fonte-externa/frente-a.jpg", "a" * 64, "0000000000000000", 80),
            row("fonte-externa/frente-b.jpg", "a" * 64, "0000000000000000", 80),
        ]

        plan = plan_gallery_merge([], incoming)

        self.assertEqual(plan["counts"], {"add_view": 1, "skip_exact": 1})


    def test_explicit_identity_mapping_matches_external_product_to_existing_model(self):
        existing_model = "Games / Saga / Heroi / modelo-publico-01"
        incoming_model = "fonte / produto-abc"
        existing = [row("catalogo/frente.jpg", "a" * 64, "0000000000000000", 60, model=existing_model)]
        incoming = [row("fonte-externa/frente-hq.png", "b" * 64, "0000000000000001", 95, model=incoming_model, width=1800, height=2700)]

        plan = plan_gallery_merge(
            existing,
            incoming,
            identity_mapping={incoming_model: existing_model},
        )

        self.assertEqual(plan["identityMappings"], 1)
        self.assertEqual(plan["modelsTouched"], 1)
        action = plan["actions"][0]
        self.assertEqual(action["model"], existing_model)
        self.assertEqual(action["sourceModel"], incoming_model)
        self.assertEqual(action["action"], "review_visual_candidate")
        self.assertEqual(action["recommendedSource"], "incoming")

    def test_identity_mapping_fails_when_target_does_not_exist(self):
        incoming_model = "fonte / produto-abc"
        incoming = [row("fonte-externa/frente.jpg", "a" * 64, "0000000000000000", 80, model=incoming_model)]

        with self.assertRaisesRegex(RuntimeError, "alvos do mapa ausentes"):
            plan_gallery_merge(
                [],
                incoming,
                identity_mapping={incoming_model: "Games / Ausente / modelo-01"},
            )

    def test_identity_mapping_fails_when_source_is_not_in_incoming_manifest(self):
        existing_model = "Games / Saga / Heroi / modelo-publico-01"
        existing = [row("catalogo/frente.jpg", "a" * 64, "0000000000000000", 80, model=existing_model)]

        with self.assertRaisesRegex(RuntimeError, "origens do mapa ausentes"):
            plan_gallery_merge(
                existing,
                [],
                identity_mapping={"fonte / produto-inexistente": existing_model},
            )

    def test_identity_mapping_rejects_two_products_to_same_public_model(self):
        existing_model = "Games / Saga / Heroi / modelo-publico-01"
        incoming_a = "fonte / produto-a"
        incoming_b = "fonte / produto-b"
        existing = [row("catalogo/frente.jpg", "a" * 64, "0000000000000000", 80, model=existing_model)]
        incoming = [
            row("fonte-externa/a.jpg", "b" * 64, "ffffffffffffffff", 80, model=incoming_a),
            row("fonte-externa/b.jpg", "c" * 64, "aaaaaaaaaaaaaaaa", 80, model=incoming_b),
        ]

        with self.assertRaisesRegex(RuntimeError, "produtos diferentes"):
            plan_gallery_merge(
                existing,
                incoming,
                identity_mapping={incoming_a: existing_model, incoming_b: existing_model},
            )

    def test_mapping_csv_is_fail_closed_and_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.csv"
            path.write_text(
                "incoming_model,target_model\n"
                "fonte / produto-a,Games / Saga / Heroi / modelo-a\n",
                encoding="utf-8",
            )
            self.assertEqual(
                read_identity_mapping(path),
                {"fonte / produto-a": "Games / Saga / Heroi / modelo-a"},
            )

            path.write_text(
                "incoming_model,target_model\n"
                "fonte / produto-a,Games / Saga / Heroi / modelo-a\n"
                "fonte / produto-b,Games / Saga / Heroi / modelo-a\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "produtos diferentes"):
                read_identity_mapping(path)

    def test_watermarked_external_image_is_rejected_even_when_quality_is_higher(self):
        existing = [row("catalogo/frente.jpg", "a" * 64, "0000000000000000", 55)]
        incoming = [row(
            "fonte-externa/frente-hq.png",
            "b" * 64,
            "0000000000000001",
            99,
            width=3000,
            height=4000,
            watermark="watermarked",
            external=True,
        )]

        plan = plan_gallery_merge(existing, incoming)

        self.assertEqual(plan["counts"], {"reject_watermarked": 1})
        self.assertEqual(plan["actions"][0]["action"], "reject_watermarked")
        self.assertEqual(plan["actions"][0]["recommendedSource"], "existing_or_planned")

    def test_external_image_without_watermark_audit_is_blocked(self):
        incoming = [row(
            "fonte-externa/costas.jpg",
            "b" * 64,
            "ffffffffffffffff",
            90,
            external=True,
        )]

        plan = plan_gallery_merge([], incoming)

        self.assertEqual(plan["counts"], {"review_watermark_required": 1})
        self.assertEqual(plan["actions"][0]["action"], "review_watermark_required")
        self.assertEqual(plan["actions"][0]["recommendedSource"], "audit_required")

    def test_clear_external_image_beats_watermarked_existing_match_before_quality(self):
        existing = [row(
            "catalogo/frente-watermark.jpg",
            "a" * 64,
            "0000000000000000",
            98,
            width=3000,
            height=4000,
            watermark="watermarked",
        )]
        incoming = [row(
            "fonte-externa/frente-clear.jpg",
            "b" * 64,
            "0000000000000001",
            75,
            width=1600,
            height=2200,
            watermark="clear",
            external=True,
        )]

        plan = plan_gallery_merge(existing, incoming)

        action = plan["actions"][0]
        self.assertEqual(action["action"], "review_visual_candidate")
        self.assertEqual(action["recommendedSource"], "incoming")
        self.assertEqual(action["reason"], "clear_incoming_beats_watermarked_match")
        self.assertEqual(plan["counts"]["review_prefers_incoming"], 1)

    def test_verified_clear_external_is_recommended_over_unverified_legacy_even_if_lower_score(self):
        existing = [row(
            "catalogo/frente-legado.png",
            "a" * 64,
            "0000000000000000",
            99,
            width=3000,
            height=4000,
        )]
        incoming = [row(
            "fonte-externa/frente-clear.jpg",
            "b" * 64,
            "0000000000000001",
            70,
            width=1400,
            height=2000,
            watermark="clear",
            external=True,
        )]

        plan = plan_gallery_merge(existing, incoming)

        action = plan["actions"][0]
        self.assertEqual(action["action"], "review_visual_candidate")
        self.assertEqual(action["recommendedSource"], "incoming")
        self.assertEqual(
            action["reason"],
            "verified_clear_incoming_beats_unverified_legacy_match",
        )


if __name__ == "__main__":
    unittest.main()
