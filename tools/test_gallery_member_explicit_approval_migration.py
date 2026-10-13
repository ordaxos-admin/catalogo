import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"


class GalleryMemberExplicitApprovalMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE models (
              id TEXT PRIMARY KEY,
              franchise_id INTEGER NOT NULL,
              folder_id INTEGER,
              name TEXT NOT NULL,
              published INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE model_gallery_members (
              canonical_model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
              source_model_id TEXT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
              position INTEGER NOT NULL CHECK(position >= 1),
              PRIMARY KEY (canonical_model_id, source_model_id),
              UNIQUE (source_model_id),
              UNIQUE (canonical_model_id, position),
              CHECK (canonical_model_id <> source_model_id)
            );
            INSERT INTO models(id,franchise_id,folder_id,name,published) VALUES
              ('a',1,10,'Produto frontal',1),
              ('b',1,10,'Produto frontal',1),
              ('c',1,10,'Produto lateral',1),
              ('d',2,10,'Produto lateral',1),
              ('e',1,11,'Produto lateral',1),
              ('f',1,10,'Produto lateral',0);
            """
        )
        self.db.executescript((MIGRATIONS / "0014_gallery_member_integrity.sql").read_text(encoding="utf-8"))
        self.db.executescript((MIGRATIONS / "0020_gallery_member_explicit_approvals.sql").read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()

    def test_same_public_name_still_needs_no_approval(self):
        self.db.execute(
            "INSERT INTO model_gallery_members(canonical_model_id,source_model_id,position) VALUES('a','b',1)"
        )
        self.assertEqual(1, self.db.execute("SELECT COUNT(*) FROM model_gallery_members").fetchone()[0])

    def test_different_public_name_requires_exact_approval(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "public name or explicit approval"):
            self.db.execute(
                "INSERT INTO model_gallery_members(canonical_model_id,source_model_id,position) VALUES('a','c',1)"
            )

        self.db.execute(
            "INSERT INTO model_gallery_member_approvals(source_model_id,canonical_model_id,registry_family,reason) VALUES('c','a','family','visual review')"
        )
        self.db.execute(
            "INSERT INTO model_gallery_members(canonical_model_id,source_model_id,position) VALUES('a','c',1)"
        )
        self.assertEqual(('a','c',1), self.db.execute(
            "SELECT canonical_model_id,source_model_id,position FROM model_gallery_members WHERE source_model_id='c'"
        ).fetchone())

    def test_approval_rejects_wrong_scope_or_inactive_source(self):
        for source in ('d','e','f'):
            with self.subTest(source=source):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "same franchise folder"):
                    self.db.execute(
                        "INSERT INTO model_gallery_member_approvals(source_model_id,canonical_model_id,registry_family,reason) VALUES(?, 'a','family','visual review')",
                        (source,),
                    )

    def test_approval_is_immutable_and_delete_protected(self):
        self.db.execute(
            "INSERT INTO model_gallery_member_approvals(source_model_id,canonical_model_id,registry_family,reason) VALUES('c','a','family','visual review')"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "approval is immutable"):
            self.db.execute(
                "UPDATE model_gallery_member_approvals SET reason='changed' WHERE source_model_id='c'"
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "deletion requires an explicit migration"):
            self.db.execute(
                "DELETE FROM model_gallery_member_approvals WHERE source_model_id='c'"
            )

    def test_approval_cannot_create_gallery_chain(self):
        self.db.execute(
            "INSERT INTO model_gallery_members(canonical_model_id,source_model_id,position) VALUES('a','b',1)"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot create a gallery chain"):
            self.db.execute(
                "INSERT INTO model_gallery_member_approvals(source_model_id,canonical_model_id,registry_family,reason) VALUES('c','b','family','visual review')"
            )


if __name__ == "__main__":
    unittest.main()
