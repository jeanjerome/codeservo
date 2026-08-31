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


class ModelsCommandTests(unittest.TestCase):
    def test_models_reports_every_backend_by_default(self) -> None:
        args = build_parser().parse_args(["models"])

        self.assertIsNone(args.actuator)
        self.assertIsNone(args.model)
        self.assertIsNone(args.state_dir)
        self.assertFalse(args.json)

    def test_models_accepts_both_selectors(self) -> None:
        args = build_parser().parse_args(
            ["models", "--actuator", "codex", "--model", "gpt-5.6-sol"]
        )

        self.assertEqual("codex", args.actuator)
        self.assertEqual("gpt-5.6-sol", args.model)

    def test_models_accepts_a_state_directory_and_a_document_form(self) -> None:
        args = build_parser().parse_args(["models", "--json", "--state-dir", "state"])

        self.assertTrue(args.json)
        self.assertEqual(Path("state"), args.state_dir)

    def test_models_rejects_an_unknown_backend(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["models", "--actuator", "gemini"])

        self.assertNotEqual(0, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
