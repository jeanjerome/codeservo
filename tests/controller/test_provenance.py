"""What a record says about the controller and the backends that ran it."""

import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

import codeservo
from codeservo.actuators import Actuator
from codeservo.controller import EVIDENCE_SCHEMA_VERSION
from codeservo.controller.provenance import command_version, runtime_metadata


class RuntimeIdentityTests(unittest.TestCase):
    def _actuator(self, name: str = "fake", version: str = "fake 9.9") -> Actuator:
        return Actuator(
            name=name,
            version_command=(sys.executable, "-c", f"print({version!r})"),
            implement=lambda *_args, **_kwargs: {},
            review=lambda *_args, **_kwargs: ({}, {}),
            describe_isolation=lambda *_args, **_kwargs: {},
        )

    def _source_root(self) -> Path:
        return Path(codeservo.__file__).resolve().parents[2]

    def test_declares_the_shape_the_record_has(self) -> None:
        # Every gate record now names the format it answered with, and a gate
        # answering with a document carries the four fields describing it.
        self.assertEqual(16, EVIDENCE_SCHEMA_VERSION)

    def test_names_both_backends_when_one_serves_both_roles(self) -> None:
        actuator = self._actuator()

        runtime = runtime_metadata(actuator, actuator, None, None)

        self.assertEqual("fake", runtime.actuator)
        self.assertEqual("fake", runtime.review_actuator)
        self.assertEqual("fake 9.9", runtime.actuator_version)
        self.assertEqual("fake 9.9", runtime.review_actuator_version)
        self.assertEqual("fake-default", runtime.implementer_model)
        self.assertEqual("fake-default", runtime.reviewer_model)

    def test_names_each_backend_and_its_own_cli_version(self) -> None:
        runtime = runtime_metadata(
            self._actuator(),
            self._actuator(name="other", version="other 1.2"),
            "a-model",
            "another-model",
        )

        self.assertEqual("fake", runtime.actuator)
        self.assertEqual("fake 9.9", runtime.actuator_version)
        self.assertEqual("other", runtime.review_actuator)
        self.assertEqual("other 1.2", runtime.review_actuator_version)
        self.assertEqual("a-model", runtime.implementer_model)
        self.assertEqual("another-model", runtime.reviewer_model)

    def test_reports_the_reviewing_backend_default_model(self) -> None:
        runtime = runtime_metadata(
            self._actuator(), self._actuator(name="other"), None, None
        )

        self.assertEqual("fake-default", runtime.implementer_model)
        self.assertEqual("other-default", runtime.reviewer_model)

    def test_declares_the_controller_version_in_one_place(self) -> None:
        pyproject = self._source_root() / "pyproject.toml"
        if not pyproject.is_file():
            self.skipTest("controller does not run from a source checkout")
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))

        self.assertIn("version", declared["project"]["dynamic"])
        self.assertNotIn("version", declared["project"])
        self.assertEqual(
            "src/codeservo/__init__.py",
            declared["tool"]["hatch"]["version"]["path"],
        )

    def test_reports_the_single_declared_controller_version(self) -> None:
        runtime = runtime_metadata(
            self._actuator(), self._actuator(), None, None
        )

        self.assertEqual(codeservo.__version__, runtime.codeservo_version)

    def test_reports_the_commit_of_the_controller_checkout(self) -> None:
        checkout = subprocess.run(
            ["git", "-C", str(self._source_root()), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
        if checkout.returncode != 0:
            self.skipTest("controller does not run from a Git checkout")

        runtime = runtime_metadata(
            self._actuator(), self._actuator(), None, None
        )

        self.assertEqual(checkout.stdout.strip(), runtime.codeservo_commit)
        self.assertEqual(40, len(runtime.codeservo_commit))

    def test_keeps_the_answer_of_a_successful_lookup(self) -> None:
        self.assertEqual(
            "fake 9.9",
            command_version([sys.executable, "-c", "print('fake 9.9')"]),
        )

    def test_reports_a_failed_lookup_as_unavailable(self) -> None:
        failing = [
            sys.executable,
            "-c",
            "import sys; print('fatal: not a git repository', file=sys.stderr);"
            " sys.exit(128)",
        ]

        self.assertEqual("unavailable", command_version(failing))


if __name__ == "__main__":
    unittest.main()
