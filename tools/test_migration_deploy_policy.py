from __future__ import annotations

import unittest

from check_migration_deploy_policy import (
    trigger_replacement_approval_errors,
    unsafe_operations,
    validate_repository,
)


class MigrationDeployPolicyTests(unittest.TestCase):
    def test_repository_policy_is_complete_and_expand_safe(self) -> None:
        self.assertEqual([], validate_repository())

    def test_destructive_sql_is_rejected(self) -> None:
        cases = {
            "DROP TABLE models": "drop-object",
            "ALTER TABLE models RENAME TO old_models": "destructive-alter",
            "ALTER TABLE models DROP COLUMN code": "destructive-alter",
            "DELETE FROM models WHERE published = 0": "delete-rows",
            "INSERT OR REPLACE INTO models(id) VALUES ('x')": "replace-rows",
            "REPLACE INTO models(id) VALUES ('x')": "replace-rows",
            "TRUNCATE models": "truncate",
            "PRAGMA writable_schema = ON": "writable-schema",
        }
        for sql, expected in cases.items():
            with self.subTest(sql=sql):
                self.assertIn(expected, unsafe_operations(sql))


    def test_trigger_replacement_approval_is_exact_and_cannot_mask_other_drops(self) -> None:
        approved = ["trg_a", "trg_b"]
        safe = """
        DROP TRIGGER IF EXISTS trg_a;
        DROP TRIGGER IF EXISTS trg_b;
        CREATE TRIGGER trg_a AFTER INSERT ON models BEGIN SELECT 1; END;
        CREATE TRIGGER trg_b AFTER INSERT ON models BEGIN SELECT 1; END;
        """
        self.assertEqual([], trigger_replacement_approval_errors(safe, approved))

        unsafe = safe + "\nDROP TABLE models;\n"
        errors = trigger_replacement_approval_errors(unsafe, approved)
        self.assertTrue(any("non-trigger DROP" in error for error in errors))

        wrong = safe.replace("trg_b", "trg_c")
        errors = trigger_replacement_approval_errors(wrong, approved)
        self.assertTrue(any("unapproved trigger DROP" in error for error in errors))
        self.assertTrue(any("not present" in error for error in errors))

    def test_trigger_delete_events_and_foreign_keys_are_not_false_positives(self) -> None:
        sql = """
        CREATE TRIGGER protect_model
        BEFORE DELETE ON models
        BEGIN
          SELECT RAISE(ABORT, 'do not delete');
        END;

        CREATE TABLE child (
          parent_id TEXT REFERENCES parent(id) ON DELETE CASCADE
        );
        """
        self.assertEqual([], unsafe_operations(sql))

    def test_comments_and_error_messages_do_not_trigger_policy(self) -> None:
        sql = """
        -- DROP TABLE models;
        /* DELETE FROM models; */
        CREATE TRIGGER note_only AFTER UPDATE ON models
        BEGIN
          SELECT RAISE(ABORT, 'DELETE FROM models is forbidden');
        END;
        """
        self.assertEqual([], unsafe_operations(sql))


if __name__ == "__main__":
    unittest.main()
