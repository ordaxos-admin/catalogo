import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
EXTERNAL_ACTION = re.compile(
    r"^\s*uses:\s+(?P<action>[^\s@]+)@(?P<ref>[^\s#]+)(?:\s+#\s*(?P<label>[^\s]+))?\s*$",
    re.MULTILINE,
)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
VERSION_LABEL = re.compile(r"^v\d+(?:\.\d+(?:\.\d+)?)?$", re.IGNORECASE)


class GithubActionsPinTests(unittest.TestCase):
    def test_every_external_action_is_pinned_to_full_commit_sha(self):
        found = 0
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            for match in EXTERNAL_ACTION.finditer(text):
                action = match.group("action")
                if action.startswith("./"):
                    continue
                found += 1
                ref = match.group("ref")
                self.assertRegex(
                    ref,
                    FULL_SHA,
                    f"{workflow.name}: {action}@{ref} must be pinned to an immutable 40-char commit SHA",
                )
                label = match.group("label") or ""
                self.assertRegex(
                    label,
                    VERSION_LABEL,
                    f"{workflow.name}: {action}@{ref} must retain a human-readable version comment",
                )
        self.assertGreater(found, 0, "expected at least one external GitHub Action reference")

    def test_no_mutable_action_tag_or_branch_reference_remains(self):
        forbidden = re.compile(r"^\s*uses:\s+[^\s@]+@(?![0-9a-f]{40}(?:\s|#|$))", re.MULTILINE)
        violations = []
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            for match in forbidden.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(f"{workflow.name}:{line}: {match.group(0).strip()}")
        self.assertEqual([], violations, "mutable GitHub Action references found: " + "; ".join(violations))

    def test_dependabot_manages_github_actions_pins_through_pull_requests(self):
        config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn('package-ecosystem: "github-actions"', config)
        self.assertIn('directory: "/"', config)
        self.assertIn("interval: weekly", config)
        self.assertIn("target-branch: main", config)


if __name__ == "__main__":
    unittest.main()
