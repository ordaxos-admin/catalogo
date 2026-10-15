import unittest

from apply_exact_source_gallery_promotion import gallery_payload_with_reused_cover


class ExactSourceGalleryApplyTests(unittest.TestCase):
    def cover(self):
        return {
            "id": "img_cover",
            "role": "cover",
            "width": 800,
            "height": 800,
            "bytes": 100,
            "mime": "image/webp",
            "qualityScore": 50.0,
            "sourceSha256": "a" * 64,
            "variantKeys": {
                "thumb": "media/mdl/img_cover/thumb.webp",
                "card": "media/mdl/img_cover/card.webp",
                "detail": "media/mdl/img_cover/detail.webp",
            },
        }

    def new_image(self):
        return {
            "id": "img_new",
            "role": "gallery",
            "width": 2048,
            "height": 2048,
            "bytes": 200,
            "mime": "image/webp",
            "qualityScore": 70.0,
            "sourceSha256": "b" * 64,
            "variantKeys": {
                "thumb": "media/mdl/img_new/thumb.webp",
                "card": "media/mdl/img_new/card.webp",
                "detail": "media/mdl/img_new/detail.webp",
            },
        }

    def test_reuses_cover_byte_contract_unchanged(self):
        cover = self.cover()
        payload = gallery_payload_with_reused_cover(
            "mdl", ["Games", "Hades", "Artemis"], cover, "a" * 64, [self.new_image()]
        )
        self.assertEqual(payload["images"][0], cover)
        self.assertEqual(payload["images"][0]["variantKeys"]["card"], "media/mdl/img_cover/card.webp")
        self.assertEqual(payload["images"][1]["role"], "gallery")
        self.assertEqual(len(payload["images"]), 2)

    def test_rejects_anchor_sha_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, "âncora exact-source"):
            gallery_payload_with_reused_cover(
                "mdl", ["Games", "Hades"], self.cover(), "c" * 64, [self.new_image()]
            )

    def test_rejects_duplicate_source_sha(self):
        duplicate = self.new_image()
        duplicate["sourceSha256"] = "a" * 64
        with self.assertRaisesRegex(RuntimeError, "colide"):
            gallery_payload_with_reused_cover(
                "mdl", ["Games", "Hades"], self.cover(), "a" * 64, [duplicate]
            )

    def test_rejects_duplicate_image_id(self):
        duplicate = self.new_image()
        duplicate["id"] = "img_cover"
        with self.assertRaisesRegex(RuntimeError, "colide"):
            gallery_payload_with_reused_cover(
                "mdl", ["Games", "Hades"], self.cover(), "a" * 64, [duplicate]
            )

    def test_rejects_missing_reusable_card(self):
        cover = self.cover()
        cover["variantKeys"].pop("card")
        with self.assertRaisesRegex(RuntimeError, "card reutilizável"):
            gallery_payload_with_reused_cover(
                "mdl", ["Games", "Hades"], cover, "a" * 64, [self.new_image()]
            )


if __name__ == "__main__":
    unittest.main()
