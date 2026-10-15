import urllib.parse
import unittest
from unittest.mock import patch

from audit_external_source_coverage import classify, fetch_source_products, subject_hint


class ExternalSourceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.models = [
            {
                "id": "mdl_a", "name": "Azula", "slug": "avatar-azula", "code": "TS-A",
                "image_count": 1, "franchise_id": 10, "franchise_name": "Avatar",
                "franchise_slug": "avatar",
            },
            {
                "id": "mdl_r1", "name": "Ryu", "slug": "street-fighter-ryu-02", "code": "TS-R1",
                "image_count": 1, "franchise_id": 20, "franchise_name": "Street Fighter",
                "franchise_slug": "street-fighter",
            },
            {
                "id": "mdl_r2", "name": "Ryu", "slug": "street-fighter-ryu-03", "code": "TS-R2",
                "image_count": 2, "franchise_id": 20, "franchise_name": "Street Fighter",
                "franchise_slug": "street-fighter",
            },
            {
                "id": "mdl_new", "name": "Zoro 3 Diorama", "slug": "one-piece-zoro-3", "code": "TS-Z3",
                "image_count": 7, "franchise_id": 30, "franchise_name": "One Piece",
                "franchise_slug": "one-piece",
            },
        ]

    def test_subject_hint_is_review_only_character_hint(self):
        self.assertEqual(subject_hint("Ryu STL 3 – Street Fighter"), "Ryu")

    def test_source_pagination_is_stable_and_requests_categories(self):
        requested: list[str] = []

        def fake_fetch(url, **_kwargs):
            requested.append(url)
            return ([{"id": 1, "name": "A", "slug": "a", "categories": []}], {"X-WP-Total": "1", "X-WP-TotalPages": "1"})

        with patch("audit_external_source_coverage.fetch_json", side_effect=fake_fetch):
            products, meta = fetch_source_products("https://example.com")

        self.assertEqual(len(products), 1)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(requested[0]).query)
        self.assertEqual(query["orderby"], ["id"])
        self.assertEqual(query["order"], ["asc"])
        self.assertIn("categories", query["_fields"][0])
        self.assertEqual(meta["ordering"], "id_asc")

    def test_map_existing_is_confirmed_present(self):
        report = classify(
            [{"id": 10, "name": "Azula STL – Avatar", "slug": "azula", "categories": [{"name": "Avatar"}]}],
            self.models,
            {10: {"map_existing"}},
            {},
        )
        self.assertEqual(report["summary"]["confirmedPresent"], 1)
        self.assertEqual(report["products"][0]["state"], "confirmed_present_map_existing")

    def test_reviewed_new_model_is_not_present_until_deterministic_id_is_published(self):
        report = classify(
            [{"id": 11, "name": "Hisoka STL 2 – Hunter x Hunter", "slug": "hisoka"}],
            self.models,
            {11: {"new_model", "media_blocked_or_pending"}},
            {11: "mdl_missing"},
        )
        self.assertEqual(report["summary"]["confirmedPresent"], 0)
        self.assertEqual(report["summary"]["confirmedReviewedNewModelsNotPublished"], 1)
        self.assertFalse(report["summary"]["coverage100Proven"])

    def test_reviewed_identity_only_new_model_is_not_present_without_batch_id(self):
        report = classify(
            [{"id": 16, "name": "Morrigan STL – Dragon Age", "slug": "morrigan-dragon-age"}],
            self.models,
            {16: {"new_model"}},
            {},
        )
        self.assertEqual(report["summary"]["confirmedPresent"], 0)
        self.assertEqual(report["products"][0]["state"], "reviewed_new_model_not_published")
        self.assertIsNone(report["products"][0]["expectedModelId"])

    def test_reviewed_new_model_counts_present_only_when_expected_id_is_published(self):
        report = classify(
            [{"id": 12, "name": "Zoro STL 3 Diorama – One Piece", "slug": "zoro"}],
            self.models,
            {12: {"new_model"}},
            {12: "mdl_new"},
        )
        self.assertEqual(report["summary"]["confirmedPresent"], 1)
        self.assertEqual(report["products"][0]["state"], "confirmed_present_reviewed_new_model")

    def test_single_name_candidate_is_not_confirmed_and_exposes_review_metadata(self):
        report = classify(
            [{"id": 13, "name": "Azula STL – Avatar", "slug": "azula", "categories": [{"name": "Avatar"}]}],
            self.models,
            {},
            {},
        )
        row = report["products"][0]
        self.assertEqual(report["summary"]["confirmedPresent"], 0)
        self.assertEqual(row["state"], "unreviewed_single_exact_name_candidate")
        self.assertEqual(row["sourceCategories"], ["Avatar"])
        self.assertEqual(len(row["exactNameCandidates"]), 1)
        candidate = row["exactNameCandidates"][0]
        self.assertEqual(candidate["modelId"], "mdl_a")
        self.assertEqual(candidate["franchiseName"], "Avatar")
        self.assertEqual(candidate["franchiseSlug"], "avatar")
        self.assertEqual(row["candidateMetadataSemantics"], "review_only")

    def test_multiple_same_character_models_are_review_only_with_all_candidates(self):
        report = classify(
            [{"id": 14, "name": "Ryu STL 3 – Street Fighter", "slug": "ryu-3"}],
            self.models,
            {},
            {},
        )
        row = report["products"][0]
        self.assertEqual(report["summary"]["confirmedPresent"], 0)
        self.assertEqual(row["state"], "unreviewed_multiple_exact_name_candidates")
        self.assertEqual({c["modelId"] for c in row["exactNameCandidates"]}, {"mdl_r1", "mdl_r2"})
        self.assertEqual(report["summary"]["candidateMetadataSemantics"], "review_only")

    def test_missing_name_candidate_is_not_silently_declared_missing_or_present(self):
        report = classify(
            [{"id": 15, "name": "Completely New Character STL – Game", "slug": "new"}],
            self.models,
            {},
            {},
        )
        self.assertEqual(report["products"][0]["state"], "unreviewed_no_exact_name_candidate")
        self.assertEqual(report["products"][0]["exactNameCandidates"], [])
        self.assertFalse(report["summary"]["coverage100Proven"])


if __name__ == "__main__":
    unittest.main()
