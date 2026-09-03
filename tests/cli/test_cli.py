import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from codeservo.cli import build_parser
from codeservo.cli.commands import RUN_EXIT_STATUS
from codeservo.evidence.journal import JOURNAL_NAME
from run_fixtures import RUN_ID, build_run, read_record, rewrite_record


def refusal(argv: list[str]) -> tuple[object, str]:
    """What the parser refuses a command line with, and why it says so.

    `argparse` writes its usage and its reason to `sys.stderr` before raising,
    and a suite runs on the streams the controller handed the gate: those bytes
    are what a failing gate feeds back to the actuator, so a refusal this suite
    asked for has no business arriving there. Captured here, and read, because
    capturing a message nobody checks would throw it away instead.
    """
    captured = io.StringIO()
    with redirect_stderr(captured):
        try:
            build_parser().parse_args(argv)
        except SystemExit as refused:
            return refused.code, captured.getvalue()
    raise AssertionError(f"the parser accepted {argv}")


def verify_run_command(run_dir: Path, *arguments: str) -> subprocess.CompletedProcess:
    """The command an operator runs, reaching the parser through the module."""
    source = str(Path(__file__).resolve().parents[1] / "src")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "codeservo.cli", "verify-run", str(run_dir), *arguments],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )


class RunExitStatusTests(unittest.TestCase):
    """The decision of one controlled change, as a shell reads it."""

    def test_each_outcome_reports_its_own_status(self) -> None:
        self.assertEqual(
            {"ACCEPTED": 0, "REJECTED": 1, "ESCALATED": 2},
            {str(status): code for status, code in RUN_EXIT_STATUS.items()},
        )


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
        code, reason = refusal(
            ["run", "--task", "TASK.md", "--review-actuator", "gemini"]
        )

        self.assertNotEqual(0, code)
        self.assertIn("--review-actuator", reason)

    def test_run_accepts_no_review_speed_tier_it_cannot_apply(self) -> None:
        code, reason = refusal(
            ["run", "--task", "TASK.md", "--review-speed", "priority"]
        )

        self.assertNotEqual(0, code)
        self.assertIn("--review-speed", reason)

    def test_run_accepts_no_speed_tier_it_cannot_apply(self) -> None:
        code, reason = refusal(["run", "--task", "TASK.md", "--speed", "priority"])

        self.assertNotEqual(0, code)
        self.assertIn("--speed", reason)


class VerifyRunCommandTests(unittest.TestCase):
    def test_verify_run_takes_a_run_directory_and_a_document_form(self) -> None:
        args = build_parser().parse_args(["verify-run", "runs/one", "--json"])

        self.assertEqual(Path("runs/one"), args.run_dir)
        self.assertTrue(args.json)

    def test_verify_run_defaults_to_the_human_listing(self) -> None:
        args = build_parser().parse_args(["verify-run", "runs/one"])

        self.assertFalse(args.json)

    def test_verify_run_requires_a_run_directory(self) -> None:
        code, reason = refusal(["verify-run"])

        self.assertNotEqual(0, code)
        self.assertIn("run_dir", reason)

    def test_a_valid_run_writes_the_report_document_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))

            completed = verify_run_command(run_dir, "--json")

            report = json.loads(completed.stdout)
            self.assertEqual(0, completed.returncode)
            self.assertEqual("VALID", report["status"])
            self.assertEqual(RUN_ID, report["run_id"])
            self.assertEqual([], report["failures"])

    def test_without_the_document_form_it_lists_every_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))

            completed = verify_run_command(run_dir)

            self.assertEqual(0, completed.returncode)
            self.assertIn("journal.chain", completed.stdout)
            self.assertIn("input.TASK.md", completed.stdout)
            self.assertIn("status: VALID", completed.stdout)

    def test_an_invalid_run_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp))
            (run_dir / JOURNAL_NAME).unlink()

            completed = verify_run_command(run_dir, "--json")

            self.assertEqual(1, completed.returncode)
            self.assertEqual("INVALID", json.loads(completed.stdout)["status"])

    def test_an_incomplete_run_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp), status="RUNNING")

            completed = verify_run_command(run_dir, "--json")

            self.assertEqual(2, completed.returncode)
            self.assertEqual("INCOMPLETE", json.loads(completed.stdout)["status"])

    def test_a_directory_holding_no_record_exits_three_without_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            completed = verify_run_command(Path(temp), "--json")

            self.assertEqual(3, completed.returncode)
            self.assertEqual("", completed.stdout)
            self.assertIn("evidence.json", completed.stderr)

    def test_the_command_never_rewrites_the_status_the_run_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = build_run(Path(temp), status="REJECTED", reasons=("red gate",))
            record = read_record(run_dir)
            record["status"] = "ACCEPTED"
            rewrite_record(run_dir, record)
            before = sorted(path.name for path in run_dir.rglob("*"))

            completed = verify_run_command(run_dir, "--json")

            self.assertEqual(1, completed.returncode)
            self.assertEqual("ACCEPTED", read_record(run_dir)["status"])
            self.assertEqual(before, sorted(path.name for path in run_dir.rglob("*")))


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
        code, reason = refusal(["models", "--actuator", "gemini"])

        self.assertNotEqual(0, code)
        self.assertIn("--actuator", reason)


if __name__ == "__main__":
    unittest.main()
