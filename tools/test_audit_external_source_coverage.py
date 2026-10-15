import unittest

from audit_external_source_coverage import classify, subject_hint


class ExternalSourceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.models = [
            {"id": "mdl_a", "name": "Azula", "slug": "avatar-azula"},
            {"id": "mdl_r1", "name": "Ryu", "slug": "street-fighter-ryu-02"},
            {"id": "mdl_r2", "name": "Ryu", "slug": "street-fighter-ryu-03"},
            {"id": "mdl_new", "name": "Zoro 3 Diorama", "slug": "one-piece-zoro-3"},
        ]

    def test_subject_hint_is_review_only_character_hint(self):
        self.assertEqual(subject_hint("Ryu STL 3 – Street Fighter"), "Ryu")

    def test_map_existing_is_confirmed_present(self):
        report = classify(
            [{"id": 10, "name": "Azula STL – Avatar", "slug": "azula"}],
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

    def test_reviewed_new_model_counts_present_only_when_expected_id_is_published(self):
        report = classify(
            [{"id": 12, "name": "Zoro STL 3 Diorama – One Piece", "slug": "zoro"}],
            self.models,
            {12: {"new_model"}},
            {12: "mdl_new"},
        )
        self.assertEqual(report["summary"]["confirmedPresent"], 1)
        self.assertEqual(report["products"][0]["state"], "confirmed_present_reviewed_new_model")

    def test_single_name_candidate_is_not_confirmed(self):
        report = classify(
            [{"id": 13, "name": "Azula STL – Avatar", "slug": "azula"}],
            self.models,
            {},
            {},
        )
        self.assertEqual(report["summary"]["confirmedPresent"], 0)
        self.assertEqual(report["products"][0]["state"], "unreviewed_single_exact_name_candidate")

    def test_multiple_same_character_models_are_review_only(self):
        report = classify(
            [{"id": 14, "name": "Ryu STL 3 – Street Fighter", "slug": "ryu-3"}],
            self.models,
            {},
            {},
        )
        self.assertEqual(report["summary"]["confirmedPresent"], 0)
        self.assertEqual(report["products"][0]["state"], "unreviewed_multiple_exact_name_candidates")

    def test_missing_name_candidate_is_not_silently_declared_missing_or_present(self):
        report = classify(
            [{"id": 15, "name": "Completely New Character STL – Game", "slug": "new"}],
            self.models,
            {},
            {},
        )
        self.assertEqual(report["products"][0]["state"], "unreviewed_no_exact_name_candidate")
        self.assertFalse(report["summary"]["coverage100Proven"])


if __name__ == "__main__":
    unittest.main()
