"""What a confined process may read but never write."""

import unittest
from pathlib import Path

from codeservo.controller.isolation import protected_paths
from codeservo.domain.constitution import ExecutionEnvironment


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


if __name__ == "__main__":
    unittest.main()
