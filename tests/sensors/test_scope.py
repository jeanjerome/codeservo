"""The structural invariants a candidate diff is held to.

Scope is the one measurement taken against the frozen base rather than against
the tree alone: which files moved, how far, and whether any of them is a path
the constitution says the actuator may not touch. A silent weakening here would
let a change through that no other gate is looking at.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from codeservo.domain.constitution import ScopePolicy
from codeservo.sensors.scope import changed_files, diff_line_count, scope_sensor
from harness import commit_repository


class ScopeTestCase(unittest.TestCase):
    """A repository with one committed file, and the base it is measured from."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        (self.repo / "app.py").write_text("def value():\n    return 1\n", "utf-8")
        (self.repo / ".codeservo").mkdir()
        (self.repo / ".codeservo" / "constitution.toml").write_text("version = 1\n")
        commit_repository(self.repo)
        self.base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def measure(self, policy: ScopePolicy):
        return scope_sensor(self.repo, self.base, policy)


class ChangedFilesTests(ScopeTestCase):
    def test_an_untouched_tree_moved_nothing(self):
        self.assertEqual(changed_files(self.repo, self.base), [])

    def test_a_tracked_file_edited_and_a_new_file_both_count(self):
        (self.repo / "app.py").write_text("def value():\n    return 2\n", "utf-8")
        (self.repo / "added.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(changed_files(self.repo, self.base), ["added.py", "app.py"])

    def test_a_file_git_is_told_to_ignore_is_not_a_change(self):
        (self.repo / ".gitignore").write_text("build/\n", encoding="utf-8")
        commit_repository(self.repo, "ignore build")
        self.base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        (self.repo / "build").mkdir()
        (self.repo / "build" / "out.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(changed_files(self.repo, self.base), [])


class DiffLineCountTests(ScopeTestCase):
    def test_an_untouched_tree_counts_nothing(self):
        self.assertEqual(diff_line_count(self.repo, self.base), 0)

    def test_a_line_replaced_counts_as_one_added_and_one_removed(self):
        (self.repo / "app.py").write_text("def value():\n    return 2\n", "utf-8")
        self.assertEqual(diff_line_count(self.repo, self.base), 2)

    def test_a_new_file_counts_its_own_lines(self):
        (self.repo / "added.py").write_text("a\nb\nc\n", encoding="utf-8")
        self.assertEqual(diff_line_count(self.repo, self.base), 3)

    def test_a_tracked_path_that_reads_as_a_number_is_not_counted_as_lines(self):
        """A numstat line carries two counts and then a path, in that order.

        A tracked file named `12` puts a digit string where the path is, and
        reading one column too many would add the name to the total. It has to
        be tracked: an added file never reaches numstat at all.
        """
        (self.repo / "12").write_text("a\n", encoding="utf-8")
        commit_repository(self.repo, "add a numeric name")
        self.base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        (self.repo / "12").write_text("a\nb\n", encoding="utf-8")
        self.assertEqual(diff_line_count(self.repo, self.base), 1)

    def test_a_new_file_that_is_not_text_counts_nothing(self):
        """Its bytes are not lines, and refusing to guess is the honest count."""
        (self.repo / "blob.bin").write_bytes(b"\xff\xfe\x00\x01")
        self.assertEqual(diff_line_count(self.repo, self.base), 0)


class ScopeSensorTests(ScopeTestCase):
    def test_a_tree_within_every_limit_passes(self):
        (self.repo / "app.py").write_text("def value():\n    return 2\n", "utf-8")
        result = self.measure(ScopePolicy())
        self.assertTrue(result.passed)
        self.assertEqual(result.summary, "scope OK")
        self.assertEqual(result.details["violations"], [])

    def test_a_protected_path_touched_is_a_violation(self):
        (self.repo / ".codeservo" / "constitution.toml").write_text("version = 2\n")
        result = self.measure(ScopePolicy())
        self.assertFalse(result.passed)
        self.assertIn("protected path changed", result.summary)

    def test_a_protected_directory_pattern_matches_the_directory_itself(self):
        """`a/**` names the subtree, and a file directly in it is in the subtree."""
        (self.repo / ".codeservo" / "notes.md").write_text("x\n", encoding="utf-8")
        result = self.measure(ScopePolicy(protected=(".codeservo/**",)))
        self.assertFalse(result.passed)

    def test_a_subtree_pattern_also_names_the_thing_it_is_rooted_at(self):
        """`docs/**` protects `docs`, which `fnmatch` alone would not match.

        A pattern naming a subtree is refused for the subtree's own name too,
        which is why the sensor matches against a second, shortened spelling.
        """
        (self.repo / "docs").write_text("not a directory\n", encoding="utf-8")
        self.assertFalse(self.measure(ScopePolicy(protected=("docs/**",))).passed)

    def test_a_subtree_pattern_reaches_names_that_only_start_the_same_way(self):
        """The consequence of that second spelling, stated rather than met.

        Shortening `docs/**` to `docs*` protects `docsx.py` as well. The rule
        errs towards refusing, which is the safe direction for a protection,
        but it protects more than it says. Narrowing it would narrow what the
        actuator may not touch, so it is a decision of its own.
        """
        (self.repo / "docsx.py").write_text("x = 1\n", encoding="utf-8")
        self.assertFalse(self.measure(ScopePolicy(protected=("docs/**",))).passed)

    def test_a_subtree_pattern_does_not_reach_a_shorter_name(self):
        """The shortened spelling keeps the whole segment it was rooted at.

        `docs/**` reaches names starting with `docs`, and stops there: `doc.md`
        is a different name, not a shorter spelling of the same one.
        """
        (self.repo / "doc.md").write_text("x\n", encoding="utf-8")
        self.assertTrue(self.measure(ScopePolicy(protected=("docs/**",))).passed)

    def test_a_path_no_pattern_names_is_not_protected(self):
        (self.repo / "app.py").write_text("def value():\n    return 2\n", "utf-8")
        result = self.measure(ScopePolicy(protected=("docs/**",)))
        self.assertTrue(result.passed)

    def test_more_files_than_the_policy_allows_is_a_violation(self):
        for index in range(3):
            (self.repo / f"f{index}.py").write_text("x = 1\n", encoding="utf-8")
        result = self.measure(ScopePolicy(max_changed_files=2))
        self.assertFalse(result.passed)
        self.assertIn("changed files 3 > max_changed_files 2", result.summary)

    def test_exactly_the_files_the_policy_allows_is_not_a_violation(self):
        for index in range(2):
            (self.repo / f"f{index}.py").write_text("x = 1\n", encoding="utf-8")
        self.assertTrue(self.measure(ScopePolicy(max_changed_files=2)).passed)

    def test_more_lines_than_the_policy_allows_is_a_violation(self):
        (self.repo / "added.py").write_text("a\nb\nc\n", encoding="utf-8")
        result = self.measure(ScopePolicy(max_diff_lines=2))
        self.assertFalse(result.passed)
        self.assertIn("diff lines 3 > max_diff_lines 2", result.summary)

    def test_exactly_the_lines_the_policy_allows_is_not_a_violation(self):
        (self.repo / "added.py").write_text("a\nb\nc\n", encoding="utf-8")
        self.assertTrue(self.measure(ScopePolicy(max_diff_lines=3)).passed)

    def test_every_violation_is_reported_and_not_only_the_first(self):
        (self.repo / ".codeservo" / "notes.md").write_text("x\n", encoding="utf-8")
        for index in range(3):
            (self.repo / f"f{index}.py").write_text("a\nb\n", encoding="utf-8")
        result = self.measure(ScopePolicy(max_changed_files=2, max_diff_lines=2))
        self.assertEqual(len(result.details["violations"]), 3)

    def test_a_protected_path_is_named_once_however_many_patterns_match(self):
        (self.repo / ".codeservo" / "notes.md").write_text("x\n", encoding="utf-8")
        result = self.measure(
            ScopePolicy(protected=(".codeservo/**", ".codeservo/notes.md"))
        )
        self.assertEqual(len(result.details["violations"]), 1)

    def test_the_details_carry_what_was_measured(self):
        (self.repo / "added.py").write_text("a\nb\n", encoding="utf-8")
        details = self.measure(ScopePolicy()).details
        self.assertEqual(details["changed_files"], ["added.py"])
        self.assertEqual(details["diff_lines"], 2)


if __name__ == "__main__":
    unittest.main()
