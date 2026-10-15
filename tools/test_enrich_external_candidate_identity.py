import unittest

from enrich_external_candidate_identity import subject_hint


class EnrichExternalCandidateIdentityTests(unittest.TestCase):
    def test_subject_hint_removes_stl_suffix_without_deciding_identity(self):
        self.assertEqual(subject_hint("Kaido STL 2 &#8211; One Piece"), "Kaido")
        self.assertEqual(subject_hint("Jiraiya STL &#8211; Naruto"), "Jiraiya")

    def test_subject_hint_preserves_plain_names(self):
        self.assertEqual(subject_hint("Groot and Rocket"), "Groot and Rocket")

    def test_subject_hint_decodes_entities(self):
        self.assertEqual(subject_hint("Tom &amp; Jerry"), "Tom & Jerry")


if __name__ == "__main__":
    unittest.main()
