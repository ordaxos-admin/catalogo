import unittest

from media_quality_policy import (
    MediaQualityPolicyError,
    PROMOTABLE_WATERMARK_STATUSES,
    WATERMARK_CREATOR_ATTRIBUTION,
    WATERMARK_WATERMARKED,
    audited_watermark_status,
)
from plan_gallery_merge import plan_gallery_merge


def row(
    path: str,
    sha: str,
    dhash: str,
    quality: float,
    *,
    watermark: str,
    external: bool = False,
    width: int = 1200,
    height: int = 1800,
):
    value = {
        "path": path,
        "status": "OK",
        "canonical": True,
        "sha256": sha,
        "dhash": dhash,
        "quality_score": quality,
        "width": width,
        "height": height,
        "size": 200_000,
        "model_key": "Games / Saga / Heroi",
        "public_model_key": "Games / Saga / Heroi / heroi-modelo-01",
        "watermark_status": watermark,
    }
    if external:
        value["source_provenance"] = {
            "kind": "AUTHORIZED_EXTERNAL_MEDIA",
            "sourceId": "external-source-001",
        }
    return value


class CreatorAttributionPolicyTests(unittest.TestCase):
    def test_creator_attribution_is_audited_and_promotable(self):
        self.assertEqual(
            audited_watermark_status("creator_attribution"),
            WATERMARK_CREATOR_ATTRIBUTION,
        )
        self.assertIn(WATERMARK_CREATOR_ATTRIBUTION, PROMOTABLE_WATERMARK_STATUSES)

    def test_arbitrary_watermark_label_is_not_accepted(self):
        with self.assertRaises(MediaQualityPolicyError):
            audited_watermark_status("telegram")

    def test_creator_attribution_can_add_a_distinct_view(self):
        incoming = [row(
            "external-source-001/product/costas.webp",
            "b" * 64,
            "ffffffffffffffff",
            88,
            watermark=WATERMARK_CREATOR_ATTRIBUTION,
            external=True,
        )]
        plan = plan_gallery_merge([], incoming)
        self.assertEqual(plan["counts"], {"add_view": 1})
        self.assertEqual(plan["actions"][0]["action"], "add_view")

    def test_creator_attribution_can_beat_blocking_watermark_after_review(self):
        existing = [row(
            "catalogo/frente-watermarked.webp",
            "a" * 64,
            "0000000000000000",
            99,
            watermark=WATERMARK_WATERMARKED,
        )]
        incoming = [row(
            "external-source-001/product/frente.webp",
            "b" * 64,
            "0000000000000001",
            82,
            watermark=WATERMARK_CREATOR_ATTRIBUTION,
            external=True,
        )]
        plan = plan_gallery_merge(existing, incoming)
        action = plan["actions"][0]
        self.assertEqual(action["action"], "review_visual_candidate")
        self.assertEqual(action["recommendedSource"], "incoming")
        self.assertEqual(action["reason"], "audited_incoming_beats_blocking_watermark")

    def test_blocking_watermark_is_rejected_even_when_quality_is_higher(self):
        existing = [row(
            "catalogo/frente.webp",
            "a" * 64,
            "0000000000000000",
            60,
            watermark="clear",
        )]
        incoming = [row(
            "external-source-001/product/frente-hq.webp",
            "b" * 64,
            "0000000000000001",
            100,
            watermark=WATERMARK_WATERMARKED,
            external=True,
            width=4000,
            height=6000,
        )]
        plan = plan_gallery_merge(existing, incoming)
        self.assertEqual(plan["counts"], {"reject_watermarked": 1})
        self.assertEqual(plan["actions"][0]["action"], "reject_watermarked")

    def test_exact_sha_is_never_duplicated_regardless_of_source_preference(self):
        existing = [row(
            "catalogo/frente.webp",
            "a" * 64,
            "0000000000000000",
            60,
            watermark="clear",
        )]
        incoming = [row(
            "external-source-001/product/frente-hq.webp",
            "a" * 64,
            "0000000000000000",
            100,
            watermark=WATERMARK_CREATOR_ATTRIBUTION,
            external=True,
            width=4000,
            height=6000,
        )]
        plan = plan_gallery_merge(existing, incoming)
        self.assertEqual(plan["counts"], {"skip_exact": 1})
        self.assertEqual(plan["actions"][0]["reason"], "sha256_equal")


if __name__ == "__main__":
    unittest.main()
