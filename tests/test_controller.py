import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import codeservo
from codeservo.actuator import Actuator
from codeservo.controller import (
    EVIDENCE_SCHEMA_VERSION,
    ControlFailure,
    _altered_sensors,
    _command_version,
    _inference,
    _observations,
    _record_actuation,
    _resolve_state_dir,
    _review_schema_path,
    _runtime_metadata,
)
from codeservo.evidence import sha256_file, sha256_path
from codeservo.model import Constitution, Gate, ReviewPolicy, ScopePolicy

OBSERVATION_FIELDS = {
    "phase",
    "name",
    "kind",
    "sensor",
    "passed",
    "exit_code",
    "timed_out",
    "duration_ms",
    "stdout_sha256",
    "stderr_sha256",
    "result_sha256",
    "stdout_tail",
    "stderr_tail",
}


class InferenceProfileTests(unittest.TestCase):
    """The requested profile is frozen before anything actuates."""

    def _implementer(self, **overrides) -> dict:
        request = {
            "backend": "claude",
            "model": "opus",
            "effort": "high",
            "speed": "standard",
        }
        request.update(overrides)
        return _inference(**request)["implementer"]

    def test_freezes_the_four_requested_fields(self) -> None:
        implementer = self._implementer(speed="fast")

        self.assertEqual(
            {
                "backend": "claude",
                "model": "opus",
                "effort": "high",
                "speed": "fast",
            },
            implementer["requested"],
        )

    def test_records_an_absent_effort_as_null(self) -> None:
        self.assertIsNone(self._implementer(effort=None)["requested"]["effort"])

    def test_holds_nothing_the_backend_has_not_answered_yet(self) -> None:
        implementer = self._implementer()

        self.assertIsNone(implementer["native"])
        self.assertEqual(
            {"model": None, "effort": None, "speed": None}, implementer["observed"]
        )
        self.assertEqual("incomplete", implementer["provenance"])
        # A backend with no verified cache cannot contradict the request.
        self.assertEqual("unverified", implementer["validation"]["status"])
        self.assertEqual(
            {"status", "reason", "inventory_source"}, set(implementer["validation"])
        )


class ActuationRecordTests(unittest.TestCase):
    def _profile(self) -> dict:
        return {
            "native": {"--effort": "max"},
            "observed": {"model": "claude-opus-5", "effort": None, "speed": None},
            "provenance": "complete",
        }

    def test_reports_a_known_model_as_complete(self) -> None:
        profile = {"native": None, "observed": {}, "provenance": "incomplete"}

        _record_actuation(
            profile,
            {
                "native": {"model_reasoning_effort": "high"},
                "observed": {
                    "model": "gpt-5.6-sol",
                    "effort": "high",
                    "speed": None,
                },
            },
        )

        self.assertEqual({"model_reasoning_effort": "high"}, profile["native"])
        self.assertEqual("gpt-5.6-sol", profile["observed"]["model"])
        self.assertEqual("complete", profile["provenance"])

    def test_keeps_no_value_from_an_earlier_actuation(self) -> None:
        profile = self._profile()

        _record_actuation(
            profile,
            {
                "native": {},
                "observed": {"model": None, "effort": None, "speed": None},
            },
        )

        self.assertEqual({}, profile["native"])
        self.assertEqual(
            {"model": None, "effort": None, "speed": None}, profile["observed"]
        )
        self.assertEqual("incomplete", profile["provenance"])


class StateDirectoryTests(unittest.TestCase):
    def test_rejects_state_directory_inside_target_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp).resolve()

            with self.assertRaisesRegex(ControlFailure, "outside the target repository"):
                _resolve_state_dir(repo, repo / ".codeservo-state")


class SensorIntegrityTests(unittest.TestCase):
    def _frozen(self, root: Path) -> tuple[dict, dict]:
        sensor = root / "sensors" / "acceptance"
        sensor.mkdir(parents=True)
        (sensor / "contract.py").write_text("assert True\n", encoding="utf-8")
        return (
            {"acceptance": sensor},
            {"acceptance": {"sha256": sha256_path(sensor)}},
        )

    def test_reports_nothing_while_the_snapshot_is_intact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths, evidence = self._frozen(Path(temp))

            self.assertEqual([], _altered_sensors(paths, evidence))

    def test_reports_a_snapshot_a_gate_wrote_into(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths, evidence = self._frozen(Path(temp))
            (paths["acceptance"] / "__pycache__").mkdir()
            (paths["acceptance"] / "__pycache__" / "contract.pyc").write_bytes(b"\x00")

            self.assertEqual(["acceptance"], _altered_sensors(paths, evidence))


class ObservationBundleTests(unittest.TestCase):
    def _constitution(self) -> Constitution:
        return Constitution(
            path=Path(".codeservo/constitution.toml"),
            raw_text="",
            scope=ScopePolicy(),
            gates=(
                Gate(name="unit", phase="quick", command="make test"),
                Gate(
                    name="acceptance",
                    phase="quick",
                    command="run-sensor",
                    baseline=False,
                    sensor="owner/acceptance",
                ),
                Gate(name="compile", phase="full", command="make check"),
            ),
            review=ReviewPolicy(),
        )

    def _gate_result(self, name: str, out_dir: Path, stdout: str = "") -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = out_dir / f"{name}.stdout.log"
        stderr_path = out_dir / f"{name}.stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return {
            "name": name,
            "command": f"secret command for {name}",
            "passed": True,
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 7,
            "stdout_path": str(stdout_path),
            "stdout_sha256": "a" * 64,
            "stderr_path": str(stderr_path),
            "stderr_sha256": "b" * 64,
            "result_sha256": "c" * 64,
        }

    def test_orders_quick_gates_before_full_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            quick = [
                self._gate_result("unit", run_dir),
                self._gate_result("acceptance", run_dir),
            ]
            full = [self._gate_result("compile", run_dir)]

            bundle = _observations(self._constitution(), quick, full, (run_dir,))

            self.assertEqual(1, bundle["schema_version"])
            self.assertEqual(
                [
                    ("quick", "unit"),
                    ("quick", "acceptance"),
                    ("full", "compile"),
                ],
                [(gate["phase"], gate["name"]) for gate in bundle["gates"]],
            )
            for gate in bundle["gates"]:
                self.assertEqual(OBSERVATION_FIELDS, set(gate))
                self.assertTrue(gate["passed"])

    def test_classifies_gates_from_the_frozen_constitution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            # A repository gate named after a sensor stays a repository gate.
            quick = [
                self._gate_result("unit", run_dir),
                self._gate_result("acceptance", run_dir, "external sensor output\n"),
            ]

            bundle = _observations(self._constitution(), quick, [], (run_dir,))

            unit, acceptance = bundle["gates"]
            self.assertEqual("repository_gate", unit["kind"])
            self.assertIsNone(unit["sensor"])
            self.assertEqual("external_sensor", acceptance["kind"])
            self.assertEqual("owner/acceptance", acceptance["sensor"])

    def test_exposes_no_command_or_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "run"
            worktree = Path(temp) / "worktree"
            quick = [
                self._gate_result(
                    "acceptance",
                    run_dir,
                    f"sensor at {run_dir}/sensors/acceptance in {worktree}\n",
                )
            ]

            bundle = _observations(
                self._constitution(), quick, [], (run_dir, worktree)
            )

            serialized = json.dumps(bundle)
            self.assertNotIn("secret command", serialized)
            self.assertNotIn(str(run_dir), serialized)
            self.assertNotIn(str(worktree), serialized)
            self.assertEqual(
                "sensor at <redacted>/sensors/acceptance in <redacted>",
                bundle["gates"][0]["stdout_tail"],
            )

    def test_keeps_only_the_last_logged_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            emitted = [f"line {index}" for index in range(500)]
            quick = [
                self._gate_result("unit", run_dir, "\n".join(emitted) + "\n")
            ]

            bundle = _observations(self._constitution(), quick, [], (run_dir,))

            tail_lines = bundle["gates"][0]["stdout_tail"].splitlines()
            self.assertEqual(120, len(tail_lines))
            self.assertEqual(emitted[-120:], tail_lines)
            self.assertEqual("", bundle["gates"][0]["stderr_tail"])


class ReviewSchemaTests(unittest.TestCase):
    def test_prefers_the_repository_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository_copy = root / "templates" / "review.schema.json"
            repository_copy.parent.mkdir()
            repository_copy.write_text("{}", encoding="utf-8")

            self.assertEqual(repository_copy, _review_schema_path(root))

    def test_falls_back_to_the_packaged_schema_without_repository_templates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            packaged = _review_schema_path(Path(temp))

            self.assertTrue(packaged.is_file(), f"missing packaged schema: {packaged}")
            schema = json.loads(packaged.read_text(encoding="utf-8"))
            self.assertEqual({"criteria", "findings"}, set(schema["required"]))
            self.assertEqual(
                {"criteria", "findings"}, set(schema["properties"])
            )

    def test_both_copies_state_the_same_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            packaged = _review_schema_path(Path(temp))
        repository_copy = _review_schema_path()

        self.assertNotEqual(packaged, repository_copy)
        self.assertEqual(sha256_file(packaged), sha256_file(repository_copy))


class RuntimeIdentityTests(unittest.TestCase):
    def _actuator(self) -> Actuator:
        return Actuator(
            name="fake",
            version_command=(sys.executable, "-c", "print('fake 9.9')"),
            implement=lambda *args, **kwargs: {},
            review=lambda *args, **kwargs: ({}, {}),
            describe_isolation=lambda *args, **kwargs: {},
        )

    def _source_root(self) -> Path:
        return Path(codeservo.__file__).resolve().parents[2]

    def test_declares_the_shape_the_record_has(self) -> None:
        self.assertEqual(9, EVIDENCE_SCHEMA_VERSION)

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
        runtime = _runtime_metadata(self._actuator(), None, None)

        self.assertEqual(codeservo.__version__, runtime["codeservo_version"])

    def test_reports_the_commit_of_the_controller_checkout(self) -> None:
        checkout = subprocess.run(
            ["git", "-C", str(self._source_root()), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
        if checkout.returncode != 0:
            self.skipTest("controller does not run from a Git checkout")

        runtime = _runtime_metadata(self._actuator(), None, None)

        self.assertEqual(checkout.stdout.strip(), runtime["codeservo_commit"])
        self.assertEqual(40, len(runtime["codeservo_commit"]))

    def test_keeps_the_answer_of_a_successful_lookup(self) -> None:
        self.assertEqual(
            "fake 9.9",
            _command_version([sys.executable, "-c", "print('fake 9.9')"]),
        )

    def test_reports_a_failed_lookup_as_unavailable(self) -> None:
        failing = [
            sys.executable,
            "-c",
            "import sys; print('fatal: not a git repository', file=sys.stderr);"
            " sys.exit(128)",
        ]

        self.assertEqual("unavailable", _command_version(failing))


if __name__ == "__main__":
    unittest.main()
