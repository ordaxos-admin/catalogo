import re
import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
MIGRATION = MIGRATIONS / "0014_gallery_member_integrity.sql"
PENDING_TRIGGER_MIGRATIONS = tuple(
    MIGRATIONS / name
    for name in (
        "0014_gallery_member_integrity.sql",
        "0015_gallery_publication_invariant.sql",
        "0016_public_gallery_revision.sql",
        "0017_gallery_lifecycle_integrity.sql",
        "0018_gallery_alias_immutability.sql",
        "0019_model_variant_name.sql",
        "0020_gallery_member_explicit_approvals.sql",
    )
)


def executable_sql(sql: str) -> str:
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )


class GalleryMemberIntegrityMigrationTests(unittest.TestCase):
    def setUp(self):
        self.sql = MIGRATION.read_text(encoding="utf-8")
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE models (
              id TEXT PRIMARY KEY,
              franchise_id INTEGER NOT NULL,
              folder_id INTEGER,
              name TEXT NOT NULL
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
            INSERT INTO models(id,franchise_id,folder_id,name) VALUES
              ('a', 1, 10, 'Produto'),
              ('b', 1, 10, 'Produto'),
              ('c', 1, 10, 'Produto'),
              ('d', 2, 10, 'Produto'),
              ('e', 1, 11, 'Produto'),
              ('f', 1, 10, 'Outro');
            """
        )
        self.db.executescript(self.sql)

    def tearDown(self):
        self.db.close()

    def test_pending_trigger_migrations_avoid_standalone_select_case_guards(self):
        for path in PENDING_TRIGGER_MIGRATIONS:
            with self.subTest(migration=path.name):
                sql = executable_sql(path.read_text(encoding="utf-8")).upper()
                self.assertNotRegex(sql, r"\bSELECT\s+CASE\b")

    def test_0014_trigger_source_has_only_trigger_level_end_markers(self):
        sql = executable_sql(self.sql).upper()
        self.assertEqual(2, len(re.findall(r"(?mi)^\s*END;\s*$", sql)))
        self.assertEqual(6, sql.count("SELECT RAISE(ABORT"))

    def test_migration_creates_both_integrity_triggers(self):
        names = {
            row[0]
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
            )
        }
        self.assertEqual(
            {
                "trg_gallery_members_validate_insert",
                "trg_gallery_members_validate_update",
            },
            names,
        )

    def test_insert_rejects_chains_in_both_directions(self):
        self.db.execute(
            "INSERT INTO model_gallery_members(canonical_model_id,source_model_id,position) VALUES('a','b',1)"
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "gallery canonical cannot already be a source"):
            self.db.execute(
                "INSERT INTO model_gallery_members(canonical_model_id,source_model_id,position) VALUES('b','c',1)"
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "gallery source cannot already be a canonical"):
            self.db.execute(
                "INSERT INTO model_gallery_members(canonical_model_id,source_model_id,position) VALUES('c','a',1)"
            )

    def test_insert_requires_same_franchise_folder_and_public_name(self):
        for source in ("d", "e", "f"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "gallery members must share franchise folder and public name",
                ):
                    self.db.execute(
                        "INSERT INTO model_gallery_members(canonical_model_id,source_model_id,position) VALUES('a',?,1)",
                        (source,),
                    )

    def test_update_revalidates_scope(self):
        self.db.execute(
            "INSERT INTO model_gallery_members(canonical_model_id,source_model_id,position) VALUES('a','b',1)"
        )
        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "gallery members must share franchise folder and public name",
        ):
            self.db.execute(
                "UPDATE model_gallery_members SET source_model_id='d' WHERE canonical_model_id='a' AND source_model_id='b'"
            )


if __name__ == "__main__":
    unittest.main()
