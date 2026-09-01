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

    def test_run_defaults_to_no_effort_and_the_standard_speed(self) -> None:
        args = build_parser().parse_args(["run", "--task", "TASK.md"])

        self.assertIsNone(args.effort)
        self.assertEqual("standard", args.speed)

    def test_run_selects_the_inference_profile(self) -> None:
        args = build_parser().parse_args(
            [
                "run",
                "--task",
                "TASK.md",
                "--actuator",
                "claude",
                "--model",
                "opus",
                "--effort",
                "xhigh",
                "--speed",
                "fast",
            ]
        )

        self.assertEqual("claude", args.actuator)
        self.assertEqual("opus", args.model)
        self.assertEqual("xhigh", args.effort)
        self.assertEqual("fast", args.speed)

    def test_run_leaves_the_review_profile_to_the_documented_defaults(self) -> None:
        args = build_parser().parse_args(["run", "--task", "TASK.md"])

        # An absent review backend is resolved from --actuator by the
        # controller, an absent effort stays null, and the speed is standard.
        self.assertIsNone(args.review_actuator)
        self.assertIsNone(args.review_model)
        self.assertIsNone(args.review_effort)
        self.assertEqual("standard", args.review_speed)

    def test_run_selects_the_review_profile_independently(self) -> None:
        args = build_parser().parse_args(
            [
                "run",
                "--task",
                "TASK.md",
                "--actuator",
                "claude",
                "--model",
                "opus",
                "--effort",
                "high",
                "--review-actuator",
                "codex",
                "--review-model",
                "gpt-5.6-sol",
                "--review-effort",
                "medium",
                "--review-speed",
                "fast",
            ]
        )

        self.assertEqual("claude", args.actuator)
        self.assertEqual("high", args.effort)
        self.assertEqual("standard", args.speed)
        self.assertEqual("codex", args.review_actuator)
        self.assertEqual("gpt-5.6-sol", args.review_model)
        self.assertEqual("medium", args.review_effort)
        self.assertEqual("fast", args.review_speed)

    def test_run_rejects_a_review_backend_it_cannot_load(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(
                ["run", "--task", "TASK.md", "--review-actuator", "gemini"]
            )

        self.assertNotEqual(0, raised.exception.code)

    def test_run_accepts_no_review_speed_tier_it_cannot_apply(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(
                ["run", "--task", "TASK.md", "--review-speed", "priority"]
            )

        self.assertNotEqual(0, raised.exception.code)

    def test_run_accepts_no_speed_tier_it_cannot_apply(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(
                ["run", "--task", "TASK.md", "--speed", "priority"]
            )

        self.assertNotEqual(0, raised.exception.code)


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
