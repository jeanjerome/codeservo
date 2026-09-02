"""The locations a run resolves before it owns any of them."""

import tempfile
import unittest
from pathlib import Path

from codeservo.controller import ControlFailure
from codeservo.controller.context import resolve_state_dir


class StateDirectoryTests(unittest.TestCase):
    def test_rejects_state_directory_inside_target_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp).resolve()

            with self.assertRaisesRegex(
                ControlFailure, "outside the target repository"
            ):
                resolve_state_dir(repo, repo / ".codeservo-state")


if __name__ == "__main__":
    unittest.main()
