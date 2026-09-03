"""The locations a run resolves before it owns any of them."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from codeservo.actuators.inventory import DEFAULT_SPEED
from codeservo.controller import ControlFailure
from codeservo.controller.context import (
    RunContext,
    RunRequest,
    prepare,
    resolve_state_dir,
)
from harness import TASK_TEXT, commit_repository, constitution


class StateDirectoryTests(unittest.TestCase):
    def test_rejects_state_directory_inside_target_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp).resolve()

            with self.assertRaisesRegex(
                ControlFailure, "outside the target repository"
            ):
                resolve_state_dir(repo, repo / ".codeservo-state")


class SourceMetadataTests(unittest.TestCase):
    """Where the confinement a run is frozen to puts the source repository.

    A maintainer may drive a run from a linked worktree, where `.git` is a
    file holding a pointer and Git writes the metadata under the main
    repository. What the gates measuring that tree may not write is the
    directory Git writes.
    """

    def _repository(self, root: Path) -> Path:
        repo = root / "repo"
        (repo / ".codeservo").mkdir(parents=True)
        (repo / "app.py").write_text("def value():\n    return 0\n", encoding="utf-8")
        (repo / ".codeservo" / "constitution.toml").write_text(
            constitution(sensor_command=None), encoding="utf-8"
        )
        commit_repository(repo)
        return repo

    def _linked_worktree(self, repo: Path, destination: Path) -> Path:
        subprocess.run(
            ["git", "worktree", "add", "-q", str(destination)], cwd=repo, check=True
        )
        return destination

    def _prepare(self, root: Path, tree: Path) -> RunContext:
        task = root / "TASK.md"
        task.write_text(TASK_TEXT, encoding="utf-8")
        context, _ = prepare(
            RunRequest(
                repo_path=tree,
                task_path=task,
                max_iterations=1,
                agent_timeout_seconds=60,
                state_dir=root / "state",
                actuator="claude",
                model=None,
                effort=None,
                speed=DEFAULT_SPEED,
                review_actuator=None,
                review_model=None,
                review_effort=None,
                review_speed=DEFAULT_SPEED,
            )
        )
        return context

    def _common_git_dir(self, tree: Path) -> Path:
        answered = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=tree,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        return Path(answered).resolve()

    def test_a_linked_worktree_is_measured_against_the_directory_git_writes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            repo = self._repository(root)
            tree = self._linked_worktree(repo, root / "linked")
            metadata = self._common_git_dir(tree)
            self.assertTrue((tree / ".git").is_file())
            self.assertNotEqual(tree / ".git", metadata)

            context = self._prepare(root, tree)

            profiles = context.confinement
            self.assertEqual(
                (context.run_dir, metadata), profiles.source_gates.read_only
            )
            self.assertNotIn(tree / ".git", profiles.source_gates.read_only)
            # The same directory in the seatbelt the gates run under, and in
            # the document the record carries about them.
            self.assertEqual(
                (str(context.run_dir), str(metadata)),
                profiles.gate_evidence().source.read_only_paths,
            )
            # And the actuator keeps being denied it.
            self.assertIn(metadata, profiles.actuator.denied)

    def test_the_candidate_is_named_before_it_exists(self) -> None:
        """Its metadata is the `.git` of a checkout the clone has yet to make."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            repo = self._repository(root)
            tree = self._linked_worktree(repo, root / "linked")

            context = self._prepare(root, tree)

            self.assertFalse(context.worktree.exists())
            self.assertEqual(
                (context.worktree / ".git",),
                context.confinement.candidate_protected,
            )

    def test_an_ordinary_checkout_is_measured_against_its_own_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            repo = self._repository(root)

            context = self._prepare(root, repo)

            self.assertEqual(
                (context.run_dir, repo / ".git"),
                context.confinement.source_gates.read_only,
            )


if __name__ == "__main__":
    unittest.main()
