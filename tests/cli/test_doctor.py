import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codeservo.cli.commands import report_host
from codeservo.cli.doctor import Reading, readings
from codeservo.runtime.confinement import host_confiner, mechanism
from codeservo.runtime.sandbox import SandboxError
from harness import commit_repository


def named(found: list[Reading], subject: str) -> Reading:
    for one in found:
        if one.subject == subject:
            return one
    raise AssertionError(f"no reading named {subject}: {[r.subject for r in found]}")


class HostReadingTests(unittest.TestCase):
    def test_it_names_the_mechanism_this_host_applies(self) -> None:
        found = readings()

        self.assertEqual(mechanism(), named(found, "confinement").answer)
        self.assertTrue(named(found, "confinement").holds)

    def test_a_host_that_cannot_confine_says_so_and_says_what_would(self) -> None:
        host_confiner.cache_clear()
        self.addCleanup(host_confiner.cache_clear)
        refusal = SandboxError("no confinement mechanism on this host -- ...")

        with patch(
            "codeservo.cli.doctor.host_confiner", side_effect=refusal
        ):
            confinement = named(readings(), "confinement")

        self.assertFalse(confinement.holds)
        self.assertTrue(confinement.required)
        self.assertIn("no confinement mechanism", confinement.answer)
        self.assertTrue(confinement.remedy)

    def test_the_python_it_runs_on_is_the_one_it_reports(self) -> None:
        running = ".".join(str(part) for part in sys.version_info[:3])

        self.assertEqual(running, named(readings(), "python").answer)

    def test_an_absent_actuator_narrows_the_choice_without_stopping_a_run(
        self,
    ) -> None:
        found = readings()

        for backend in ("claude", "codex"):
            self.assertFalse(named(found, f"actuator {backend}").required)
        self.assertTrue(named(found, "an actuator answers").required)

    def test_a_state_directory_nothing_can_write_is_missing(self) -> None:
        with patch("codeservo.cli.doctor.os.access", return_value=False):
            state = named(readings(state_dir=Path("/nowhere")), "state directory")

        self.assertFalse(state.holds)
        self.assertIn("not writable", state.answer)


class RepositoryReadingTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name).resolve()

    def test_a_directory_no_git_knows_about_is_refused(self) -> None:
        found = readings(repo=self.repo)

        repository = named(found, "repository")
        self.assertFalse(repository.holds)
        self.assertIn("not a Git work tree", repository.answer)

    def test_a_clean_checkout_reports_the_gates_its_constitution_declares(
        self,
    ) -> None:
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.repo / ".codeservo").mkdir()
        (self.repo / ".codeservo" / "constitution.toml").write_text(
            'version = 1\n\n[[gate]]\nname = "unit"\nphase = "quick"\n'
            'command = "true"\ntimeout_seconds = 60\nbaseline = true\n\n'
            '[[gate]]\nname = "all"\nphase = "full"\n'
            'command = "true"\ntimeout_seconds = 60\nbaseline = true\n',
            encoding="utf-8",
        )
        commit_repository(self.repo)

        found = readings(repo=self.repo)

        self.assertTrue(named(found, "clean tree").holds)
        self.assertIn("unit", named(found, "constitution").answer)

    def test_an_uncommitted_path_is_not_what_a_run_would_measure(self) -> None:
        (self.repo / "app.py").write_text("value = 1\n", encoding="utf-8")
        commit_repository(self.repo)
        (self.repo / "app.py").write_text("value = 2\n", encoding="utf-8")

        clean = named(readings(repo=self.repo), "clean tree")

        self.assertFalse(clean.holds)
        self.assertIn("1 uncommitted", clean.answer)

    def test_a_constitution_that_cannot_be_read_is_reported_by_its_reason(
        self,
    ) -> None:
        (self.repo / ".codeservo").mkdir()
        (self.repo / ".codeservo" / "constitution.toml").write_text(
            "version = 1\n[[gate]]\nname = 1\n", encoding="utf-8"
        )
        commit_repository(self.repo)

        constitution = named(readings(repo=self.repo), "constitution")

        self.assertFalse(constitution.holds)
        self.assertTrue(constitution.remedy)


class ExitStatusTests(unittest.TestCase):
    def _report(self, found: list[Reading], as_json: bool = False) -> tuple[int, str]:
        captured = io.StringIO()
        with patch("codeservo.cli.commands.readings", return_value=found):
            with redirect_stdout(captured):
                status = report_host(None, None, as_json)
        return status, captured.getvalue()

    def test_a_host_answering_everything_a_run_needs_exits_zero(self) -> None:
        status, printed = self._report(
            [Reading(subject="confinement", answer="a-mechanism", holds=True)]
        )

        self.assertEqual(0, status)
        self.assertIn("ok", printed)

    def test_something_a_run_needs_and_this_host_lacks_exits_one(self) -> None:
        status, printed = self._report(
            [
                Reading(
                    subject="git",
                    answer="not installed",
                    holds=False,
                    remedy="install Git",
                )
            ]
        )

        self.assertEqual(1, status)
        self.assertIn("missing", printed)
        self.assertIn("install Git", printed)

    def test_a_reading_a_run_does_not_need_decides_nothing(self) -> None:
        status, printed = self._report(
            [
                Reading(subject="git", answer="git version 2", holds=True),
                Reading(
                    subject="actuator codex",
                    answer="not installed",
                    holds=False,
                    required=False,
                    remedy="install it to drive a run with it",
                ),
            ]
        )

        self.assertEqual(0, status)
        self.assertIn("absent", printed)

    def test_the_json_form_carries_every_field_a_reading_holds(self) -> None:
        status, printed = self._report(
            [Reading(subject="git", answer="git version 2", holds=True)], as_json=True
        )

        self.assertEqual(0, status)
        self.assertEqual(
            [
                {
                    "subject": "git",
                    "answer": "git version 2",
                    "holds": True,
                    "required": True,
                    "remedy": "",
                }
            ],
            json.loads(printed),
        )


if __name__ == "__main__":
    unittest.main()
