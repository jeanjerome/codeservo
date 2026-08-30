import unittest
from pathlib import Path

from codeservo.cli import build_parser


class CliTests(unittest.TestCase):
    def test_run_accepts_state_directory(self) -> None:
        args = build_parser().parse_args(
            ["run", "--task", "TASK.md", "--state-dir", "state"]
        )

        self.assertEqual(Path("state"), args.state_dir)

    def test_run_selects_the_actuator(self) -> None:
        args = build_parser().parse_args(
            ["run", "--task", "TASK.md", "--actuator", "codex"]
        )

        self.assertEqual("codex", args.actuator)

    def test_run_leaves_the_actuator_to_the_environment_default(self) -> None:
        args = build_parser().parse_args(["run", "--task", "TASK.md"])

        self.assertIsNone(args.actuator)


if __name__ == "__main__":
    unittest.main()
