"""The execution environment, frozen from the base commit and installed once."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.controller import ControlFailure, declared_environment
from codeservo.controller.document import CandidateEnvironment, EnvironmentBlock
from codeservo.controller.environment import (
    candidate_digests,
    changed_environment,
    frozen_environment,
    install_candidate,
    resolved_environment,
)
from codeservo.domain.constitution import (
    Constitution,
    ExecutionEnvironment,
    ReviewPolicy,
    ScopePolicy,
)
from codeservo.evidence.digests import sha256_file
from codeservo.workspace.pixi import Pixi
from harness import PIXI_PACKAGES, PIXI_TASK, commit_repository, write_provider

PROVIDER = Pixi()


def declaring(execution: ExecutionEnvironment | None) -> Constitution:
    """A constitution declaring that execution environment, and no gate."""
    return Constitution(
        path=Path("constitution.toml"),
        raw_text="version = 1\n",
        scope=ScopePolicy(),
        gates=(),
        review=ReviewPolicy(),
        execution=execution,
    )


def provider_workspace(case: unittest.TestCase) -> Path:
    """A tree the stand-in provider answers about, with nothing installed."""
    temp = tempfile.TemporaryDirectory()
    case.addCleanup(temp.cleanup)
    root = Path(temp.name)
    worktree = root / "worktree"
    bin_dir = root / "bin"
    worktree.mkdir()
    bin_dir.mkdir()
    write_provider(bin_dir, worktree, installed=False)
    patcher = patch.dict(
        os.environ,
        {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")},
        clear=False,
    )
    patcher.start()
    case.addCleanup(patcher.stop)
    return worktree


class OpeningBlockTests(unittest.TestCase):
    """The block a record opens with, before anything has been measured.

    A run refused while its control inputs are verified closes on this block,
    so what it states about the provider is what the constitution the record
    hashed declares.
    """

    def test_a_run_declaring_no_provider_records_none(self) -> None:
        block = declared_environment(declaring(None))

        self.assertEqual({"provider": "none"}, block.to_document())

    def test_states_the_provider_the_constitution_declares(self) -> None:
        execution = ExecutionEnvironment(
            provider="pixi",
            manifest="pyproject.toml",
            lock="pixi.lock",
            environment="default",
        )

        block = declared_environment(declaring(execution))

        # The provider alone: nothing else about the environment has been
        # read, so the block asserts nothing else about it.
        self.assertEqual({"provider": "pixi"}, block.to_document())


class ExecutionEnvironmentTests(unittest.TestCase):
    """The environment is frozen from the base commit, before anything runs."""

    def _repo(self) -> tuple[Path, Path, str]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        repo = root / "repo"
        bin_dir = root / "bin"
        repo.mkdir()
        bin_dir.mkdir()
        write_provider(bin_dir, repo)
        commit_repository(repo)
        base_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        patcher = patch.dict(os.environ, {"PATH": path}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        return root, repo, base_commit

    def _execution(self) -> ExecutionEnvironment:
        return ExecutionEnvironment(
            provider="pixi",
            manifest="pyproject.toml",
            lock="pixi.lock",
            environment="default",
        )

    def test_digests_the_two_files_the_base_commit_holds(self) -> None:
        _, repo, base_commit = self._repo()
        frozen = frozen_environment(repo, base_commit, self._execution())

        # A later edit of the working tree cannot change what was frozen.
        (repo / "pyproject.toml").write_text("[tool.pixi]\n", encoding="utf-8")

        self.assertEqual("pixi", frozen.provider)
        self.assertEqual("pyproject.toml", frozen.manifest_path)
        self.assertEqual("pixi.lock", frozen.lock_path)
        self.assertEqual("default", frozen.environment)
        self.assertEqual(
            frozen_environment(repo, base_commit, self._execution()), frozen
        )
        self.assertNotEqual(
            sha256_file(repo / "pyproject.toml"), frozen.manifest_sha256
        )

    def test_refuses_a_manifest_the_base_commit_does_not_hold(self) -> None:
        _, repo, base_commit = self._repo()
        execution = ExecutionEnvironment(
            provider="pixi",
            manifest="untracked.toml",
            lock="pixi.lock",
            environment="default",
        )

        with self.assertRaisesRegex(ControlFailure, "untracked.toml is not committed"):
            frozen_environment(repo, base_commit, execution)

    def test_stores_the_inventory_the_lockfile_resolves_to(self) -> None:
        root, repo, _ = self._repo()
        run_dir = root / "run"

        resolved, prefix = resolved_environment(
            repo, run_dir, self._execution(), (PIXI_TASK,), PROVIDER
        )

        # The directory the provider reports is returned, never recorded.
        self.assertEqual(str(repo / ".pixi" / "envs" / "default"), prefix)
        self.assertNotIn("prefix", resolved.to_document())
        stored = run_dir / "environment" / "packages.json"
        self.assertEqual("environment/packages.json", resolved.packages_path)
        self.assertEqual(PIXI_PACKAGES, json.loads(stored.read_text()))
        self.assertEqual(sha256_file(stored), resolved.packages_sha256)
        self.assertEqual(len(PIXI_PACKAGES), resolved.package_count)
        self.assertEqual("0.77.1-test", resolved.provider_version)
        self.assertEqual("test-platform", resolved.platform)
        self.assertEqual((PIXI_TASK,), resolved.declared_tasks)

    def test_records_nothing_the_description_says_about_the_operator(self) -> None:
        root, repo, _ = self._repo()

        resolved, _ = resolved_environment(
            repo, root / "run", self._execution(), (PIXI_TASK,), PROVIDER
        )

        document = resolved.to_document()
        serialized = json.dumps(document)
        for private in ("cache_dir", "auth_dir", "config_locations", "global_info"):
            self.assertNotIn(private, serialized)
            self.assertNotIn(private, document)
        self.assertNotIn("/operator", serialized)


class CandidateEnvironmentTests(unittest.TestCase):
    """What the candidate was prepared with, rechecked after each phase."""

    def _candidate(self) -> tuple[Path, dict, ExecutionEnvironment]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        worktree = Path(temp.name)
        (worktree / "pyproject.toml").write_text("[tool.pixi]\n", encoding="utf-8")
        (worktree / "pixi.lock").write_text("version: 6\n", encoding="utf-8")
        execution = ExecutionEnvironment(
            provider="pixi",
            manifest="pyproject.toml",
            lock="pixi.lock",
            environment="default",
        )
        digests = candidate_digests(worktree, execution, PROVIDER)
        # As the installation leaves it: the digests, and no verdict about a
        # workspace nothing has compared yet.
        environment = EnvironmentBlock(
            provider="pixi",
            candidate=CandidateEnvironment(
                prefix_path=str(worktree / ".pixi" / "envs" / "default"),
                command=("pixi", "install"),
                exit_code=0,
                duration_ms=1,
                manifest_sha256=digests.manifest_sha256,
                lock_sha256=digests.lock_sha256,
                config_sha256=digests.config_sha256,
            ),
        )
        return worktree, environment, execution

    def test_the_installation_states_no_verdict_about_the_workspace(self) -> None:
        worktree = provider_workspace(self)
        execution = ExecutionEnvironment(
            provider="pixi",
            manifest="pyproject.toml",
            lock="pixi.lock",
            environment="default",
        )

        candidate, _ = install_candidate(worktree, execution, PROVIDER)

        # Nothing has been compared to the digests just taken, so the record
        # leaves the verdict out rather than asserting one.
        self.assertEqual(0, candidate.exit_code)
        self.assertNotIn("unchanged_at_end", candidate.to_document())

    def test_a_workspace_nobody_touched_is_unchanged(self) -> None:
        worktree, environment, execution = self._candidate()

        checked, reasons = changed_environment(
            environment, worktree, execution, PROVIDER
        )

        self.assertEqual([], reasons)
        self.assertIsNone(checked.candidate.config_sha256)
        # The verdict appears with the comparison that establishes it, and
        # not before: the block handed over carried none.
        self.assertNotIn("unchanged_at_end", environment.to_document()["candidate"])
        self.assertTrue(checked.candidate.unchanged_at_end)

    def test_names_the_lockfile_a_run_rewrote(self) -> None:
        worktree, environment, execution = self._candidate()
        (worktree / "pixi.lock").write_text(
            "version: 6\n# resolved\n", encoding="utf-8"
        )

        checked, reasons = changed_environment(
            environment, worktree, execution, PROVIDER
        )

        self.assertEqual(
            ["execution environment: pixi.lock changed during the run"], reasons
        )
        self.assertFalse(checked.candidate.unchanged_at_end)

    def test_names_a_provider_configuration_that_appeared(self) -> None:
        worktree, environment, execution = self._candidate()
        configuration = worktree / ".pixi" / "config.toml"
        configuration.parent.mkdir(parents=True)
        configuration.write_text("detached-environments = true\n", encoding="utf-8")

        checked, reasons = changed_environment(
            environment, worktree, execution, PROVIDER
        )

        self.assertEqual(
            ["execution environment: .pixi/config.toml changed during the run"],
            reasons,
        )
        self.assertFalse(checked.candidate.unchanged_at_end)

    def test_a_manifest_that_is_gone_is_a_change_and_not_a_crash(self) -> None:
        worktree, environment, execution = self._candidate()
        (worktree / "pyproject.toml").unlink()

        _, reasons = changed_environment(environment, worktree, execution, PROVIDER)

        self.assertEqual(
            ["execution environment: pyproject.toml changed during the run"], reasons
        )

    def test_a_run_declaring_no_provider_has_nothing_to_recompute(self) -> None:
        worktree, _, _ = self._candidate()
        block = declared_environment(declaring(None))

        self.assertEqual((block, []), changed_environment(block, worktree, None, None))


if __name__ == "__main__":
    unittest.main()
