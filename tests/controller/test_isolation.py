"""What a confined process may read but never write."""

import unittest
from pathlib import Path

from codeservo.controller.isolation import confinement, protected_paths
from codeservo.domain.constitution import ExecutionEnvironment
from codeservo.runtime.sandbox import seatbelt_profile

# A source repository checked out as a linked worktree: `/source/.git` is a
# file holding a pointer, and Git writes the metadata under the main
# repository. The per-worktree directory lies inside the common one, so the
# common one is the whole of what a measurement must not write.
COMMON_GIT_DIR = Path("/main/.git")


class ProtectedPathTests(unittest.TestCase):
    """What stays readable and stops being writable in a measured tree."""

    def _execution(self, manifest: str = "pyproject.toml") -> ExecutionEnvironment:
        return ExecutionEnvironment(
            provider="pixi",
            manifest=manifest,
            lock="pixi.lock",
            environment="default",
        )

    def test_protects_the_git_metadata_and_the_provider_directory(self) -> None:
        self.assertEqual(
            (Path("/tree/.git"), Path("/tree/.pixi")),
            protected_paths(Path("/tree"), self._execution()),
        )

    def test_a_nested_manifest_names_its_own_workspace_directory(self) -> None:
        execution = self._execution("sub/pyproject.toml")

        self.assertEqual(
            (Path("/tree/.git"), Path("/tree/sub/.pixi")),
            protected_paths(Path("/tree"), execution),
        )

    def test_a_run_declaring_no_provider_protects_the_metadata_only(self) -> None:
        self.assertEqual((Path("/tree/.git"),), protected_paths(Path("/tree"), None))

    def test_each_tree_names_its_own_paths_and_never_the_other(self) -> None:
        source = protected_paths(Path("/source"), self._execution())
        candidate = protected_paths(Path("/candidate"), self._execution())

        self.assertTrue(all(path.is_relative_to("/source") for path in source))
        self.assertTrue(all(path.is_relative_to("/candidate") for path in candidate))

    def test_metadata_written_elsewhere_is_protected_where_git_writes_it(
        self,
    ) -> None:
        """A tree whose metadata is not `<tree>/.git` protects the directory."""
        self.assertEqual(
            (COMMON_GIT_DIR, Path("/source/.pixi")),
            protected_paths(Path("/source"), self._execution(), COMMON_GIT_DIR),
        )

    def test_the_pointer_file_is_not_named_in_place_of_the_metadata(self) -> None:
        paths = protected_paths(Path("/source"), None, COMMON_GIT_DIR)

        self.assertNotIn(Path("/source/.git"), paths)


class ConfinementTests(unittest.TestCase):
    """The profiles one run applies, built before any candidate exists."""

    REPO = Path("/source")
    WORKTREE = Path("/state/worktrees/source/run")
    RUN_DIR = Path("/state/runs/source/run")
    STATE_ROOT = Path("/state")

    def _execution(self) -> ExecutionEnvironment:
        return ExecutionEnvironment(
            provider="pixi",
            manifest="pyproject.toml",
            lock="pixi.lock",
            environment="default",
        )

    def _confinement(
        self, git_dir: Path, execution: ExecutionEnvironment | None = None
    ):
        return confinement(
            repo=self.REPO,
            worktree=self.WORKTREE,
            run_dir=self.RUN_DIR,
            state_root=self.STATE_ROOT,
            git_dir=git_dir,
            execution=execution,
        )

    def test_an_ordinary_checkout_protects_its_own_metadata(self) -> None:
        profiles = self._confinement(self.REPO / ".git", self._execution())

        self.assertEqual(
            (self.RUN_DIR, Path("/source/.git"), Path("/source/.pixi")),
            profiles.source_gates.read_only,
        )

    def test_source_gates_are_held_to_the_directory_git_writes(self) -> None:
        """The metadata of a linked worktree, and not the pointer file."""
        profiles = self._confinement(COMMON_GIT_DIR, self._execution())

        self.assertEqual(
            (self.RUN_DIR, COMMON_GIT_DIR, Path("/source/.pixi")),
            profiles.source_gates.read_only,
        )
        self.assertNotIn(Path("/source/.git"), profiles.source_gates.read_only)

    def test_the_seatbelt_the_source_gates_run_under_names_that_directory(
        self,
    ) -> None:
        """The rule holds in the profile a gate is confined with, not only in
        the paths the controller computed."""
        profile = seatbelt_profile(self._confinement(COMMON_GIT_DIR).source_gates)

        self.assertIn(f'(deny file-write* (subpath "{COMMON_GIT_DIR}"))', profile)
        self.assertNotIn("/source/.git", profile)

    def test_the_recorded_isolation_names_that_directory(self) -> None:
        """And in the document the record carries about the same gates."""
        evidence = self._confinement(COMMON_GIT_DIR).gate_evidence()

        self.assertEqual(
            (str(self.RUN_DIR), str(COMMON_GIT_DIR)),
            evidence.source.read_only_paths,
        )
        self.assertEqual(
            (str(self.RUN_DIR), str(self.WORKTREE / ".git")),
            evidence.candidate.read_only_paths,
        )

    def test_the_actuator_is_still_denied_that_same_directory(self) -> None:
        profiles = self._confinement(COMMON_GIT_DIR, self._execution())

        self.assertEqual(
            (
                self.STATE_ROOT / "runs",
                self.STATE_ROOT / "sensors",
                self.STATE_ROOT / ".git",
                COMMON_GIT_DIR,
            ),
            profiles.actuator.denied,
        )
        self.assertEqual(
            (self.REPO, self.WORKTREE / ".git", self.WORKTREE / ".pixi"),
            profiles.actuator.read_only,
        )

    def test_the_candidate_keeps_naming_its_own_checkout_metadata(self) -> None:
        """The candidate is cloned later, so its `.git` is a directory, and
        nothing here may ask a checkout that does not exist yet."""
        self.assertFalse(self.WORKTREE.exists())

        profiles = self._confinement(COMMON_GIT_DIR, self._execution())

        self.assertEqual(
            (self.WORKTREE / ".git", self.WORKTREE / ".pixi"),
            profiles.candidate_protected,
        )
        self.assertEqual(
            (self.RUN_DIR, self.WORKTREE / ".git", self.WORKTREE / ".pixi"),
            profiles.candidate_gates.read_only,
        )

    def test_each_tree_names_its_own_paths_and_never_the_other(self) -> None:
        profiles = self._confinement(COMMON_GIT_DIR, self._execution())

        source = profiles.source_gates.read_only[1:]
        candidate = profiles.candidate_gates.read_only[1:]
        self.assertFalse([path for path in source if path.is_relative_to(self.WORKTREE)])
        self.assertFalse([path for path in candidate if path.is_relative_to(self.REPO)])

    def test_a_run_declaring_no_provider_names_no_provider_directory(self) -> None:
        profiles = self._confinement(COMMON_GIT_DIR)

        self.assertEqual(
            (self.RUN_DIR, COMMON_GIT_DIR), profiles.source_gates.read_only
        )
        self.assertEqual((self.WORKTREE / ".git",), profiles.candidate_protected)


if __name__ == "__main__":
    unittest.main()
