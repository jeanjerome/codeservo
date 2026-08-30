import unittest
from pathlib import Path

from codeservo.cli import build_parser


class CliTests(unittest.TestCase):
    def test_run_accepts_state_directory(self) -> None:
        args = build_parser().parse_args(
            ["run", "--task", "TASK.md", "--state-dir", "state"]
        )

        self.assertEqual(Path("state"), args.state_dir)


if __name__ == "__main__":
    unittest.main()
