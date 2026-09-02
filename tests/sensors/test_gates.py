import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.domain.constitution import ExecutionEnvironment, Gate, ResultFormat
from codeservo.evidence.digests import sha256_record
from codeservo.runtime.sandbox import Isolation, seatbelt_profile
from codeservo.sensors.gates import gate_command, run_gates
from codeservo.sensors.observations import (
    OBSERVATION_PATH_VARIABLE,
    ObservationPathError,
)
from harness import PIXI_TASK, commit_repository, write_provider
from isolation_harness import (
    already_confined,
    nested_seatbelt_exit_code,
    protected_gate_record,
)


def hashlib_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


EXECUTION = ExecutionEnvironment(
    provider="pixi",
    manifest="pyproject.toml",
    lock="pixi.lock",
    environment="default",
)


class GateCommandTests(unittest.TestCase):
    """A gate becomes a command against the tree that gate measures."""

    def test_a_shell_gate_is_its_command_and_nothing_else(self) -> None:
        gate = Gate(name="unit", phase="quick", command="make test")

        self.assertEqual(
            "make test", gate_command(gate, tree=Path("/tree"), execution=EXECUTION)
        )
        self.assertEqual(
            "make test", gate_command(gate, tree=Path("/tree"), execution=None)
        )

    def test_a_task_gate_names_the_manifest_of_the_tree_it_measures(self) -> None:
        gate = Gate(name="unit", phase="quick", task="test-unit")

        source = gate_command(gate, tree=Path("/source"), execution=EXECUTION)
        checkout = gate_command(gate, tree=Path("/checkout"), execution=EXECUTION)

        self.assertIn("--manifest-path '/source/pyproject.toml'", source)
        self.assertIn("--manifest-path '/checkout/pyproject.toml'", checkout)
        self.assertNotIn("/checkout", source)
        self.assertNotIn("/source", checkout)

    def test_the_constitution_supplies_the_task_and_nothing_around_it(self) -> None:
        gate = Gate(name="unit", phase="quick", task="test-unit")

        command = gate_command(gate, tree=Path("/tree"), execution=EXECUTION)

        self.assertEqual(
            "pixi run --as-is --clean-env --no-config"
            " --manifest-path '/tree/pyproject.toml'"
            " --environment 'default' 'test-unit'",
            command,
        )

    def test_refuses_a_task_gate_with_no_declared_provider(self) -> None:
        gate = Gate(name="unit", phase="quick", task="test-unit")

        with self.assertRaisesRegex(ValueError, "requires an execution provider"):
            gate_command(gate, tree=Path("/tree"), execution=None)


class GateEnvironmentTests(unittest.TestCase):
    def test_exposes_sensor_path_only_to_owning_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sensor = root / "frozen-sensor"
            sensor.mkdir()
            gates = (
                Gate(
                    name="ordinary",
                    phase="quick",
                    command='test -z "$CODESERVO_SENSOR_PATH"',
                ),
                Gate(
                    name="acceptance",
                    phase="quick",
                    command=f'test "$CODESERVO_SENSOR_PATH" = "{sensor}"',
                    baseline=False,
                    sensor="example/acceptance",
                ),
            )

            with patch.dict(
                os.environ, {"CODESERVO_SENSOR_PATH": "inherited-leak"}
            ):
                results = run_gates(
                    repo=root,
                    gates=gates,
                    out_dir=root / "logs",
                    sensor_paths={"acceptance": sensor},
                )

            self.assertTrue(all(result.passed for result in results))

    def test_no_gate_of_a_provider_run_can_resolve_or_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sensor = root / "frozen-sensor"
            sensor.mkdir()
            forbidden = (
                'test xtruetruetrue'
                ' = "x$PIXI_OFFLINE$PIXI_NO_INSTALL$PIXI_FROZEN"'
            )
            gates = (
                Gate(name="ordinary", phase="quick", command=forbidden),
                Gate(
                    name="acceptance",
                    phase="quick",
                    command=f'{forbidden} && test -n "$CODESERVO_SENSOR_PATH"',
                    baseline=False,
                    sensor="example/acceptance",
                ),
            )

            results = run_gates(
                repo=root,
                gates=gates,
                out_dir=root / "logs",
                sensor_paths={"acceptance": sensor},
                execution=EXECUTION,
            )

            self.assertTrue(all(result.passed for result in results))

    def test_a_run_without_a_provider_sets_none_of_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gate = Gate(
                name="ordinary",
                phase="quick",
                command='test x = "x$PIXI_OFFLINE$PIXI_NO_INSTALL$PIXI_FROZEN"',
            )

            with patch.dict(os.environ):
                for variable in ("PIXI_OFFLINE", "PIXI_NO_INSTALL", "PIXI_FROZEN"):
                    os.environ.pop(variable, None)
                results = run_gates(
                    repo=root, gates=(gate,), out_dir=root / "logs", execution=None
                )

            self.assertTrue(results[0].passed)

    def test_runs_a_task_gate_through_the_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_provider(bin_dir, root)
            (root / "app.py").write_text("value = 2\n", encoding="utf-8")
            gates = (
                Gate(name="task", phase="quick", task=PIXI_TASK),
                Gate(name="shell", phase="quick", command="test -f app.py"),
            )

            path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            with patch.dict(os.environ, {"PATH": path}, clear=False):
                results = run_gates(
                    repo=root,
                    gates=gates,
                    out_dir=root / "logs",
                    execution=EXECUTION,
                )

            task, shell = results
            self.assertTrue(task.passed, Path(task.stderr_path).read_text())
            self.assertTrue(shell.passed)
            # A shell gate is built and run exactly as it was before.
            self.assertEqual("test -f app.py", shell.command)
            self.assertEqual(
                f"pixi run --as-is --clean-env --no-config"
                f" --manifest-path '{root / 'pyproject.toml'}'"
                f" --environment 'default' '{PIXI_TASK}'",
                task.command,
            )


DOCUMENT = {
    "schema_version": 1,
    "sensor": "mutation",
    "status": "failed",
    "summary": "3 surviving mutants",
    "findings": [
        {
            "id": "mutation-42",
            "severity": "major",
            "path": "src/example.py",
            "line": 18,
            "message": "conditional boundary survived",
        }
    ],
    "metrics": {"killed": 37, "survived": 3, "timeout": 0},
}

OBSERVATION_FIELDS = (
    "observation_status",
    "observation_error",
    "observation_path",
    "observation_sha256",
)


def writes(text: str) -> str:
    """A gate writing exactly these bytes where the controller told it to."""
    return f'printf %s {json.dumps(text)} > "${OBSERVATION_PATH_VARIABLE}"'


class GateObservationTests(unittest.TestCase):
    """The second, structured answer a gate may declare it produces."""

    def _run(self, command: str, **overrides) -> dict:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "tree").mkdir()
        arguments = {
            "name": "mutation",
            "phase": "quick",
            "command": command,
            "result_format": ResultFormat.CODESERVO_JSON,
        }
        arguments.update(overrides)
        self.out_dir = root / "run" / "quick"
        results = run_gates(
            repo=root / "tree",
            gates=(Gate(**arguments),),
            out_dir=self.out_dir,
            run_dir=root / "run",
        )
        return results[0]

    def test_hands_the_declaring_gate_an_absolute_path_to_write(self) -> None:
        probe = (
            f'test -n "${OBSERVATION_PATH_VARIABLE}"'
            f' && case "${OBSERVATION_PATH_VARIABLE}" in /*) ;; *) exit 1;; esac'
            f' && test ! -e "${OBSERVATION_PATH_VARIABLE}"'
            f' && test -d "$(dirname "${OBSERVATION_PATH_VARIABLE}")"'
            f' && {writes(json.dumps({**DOCUMENT, "status": "passed"}))}'
        )

        record = self._run(probe)

        self.assertTrue(record.passed, Path(record.stderr_path).read_text())
        self.assertEqual("valid", record.observation_status)

    def test_keeps_the_document_as_the_gate_wrote_it(self) -> None:
        # Neither canonical, nor sorted, nor reindented: whatever comes back
        # must be exactly these bytes.
        written = (
            '{"metrics":{"killed":37},   "summary":"3 surviving mutants",'
            ' "findings":[], "status":"failed", "sensor":"mutation",'
            ' "schema_version":1}'
        )

        record = self._run(f"{writes(written)}; exit 1")

        kept = Path(record.observation_path)
        self.assertEqual("mutation.observation.json", kept.name)
        self.assertEqual(self.out_dir, kept.parent)
        self.assertEqual(written, kept.read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib_sha256(written.encode("utf-8")), record.observation_sha256
        )
        self.assertEqual("valid", record.observation_status)
        self.assertIsNone(record.observation_error)

    def test_a_gate_that_wrote_nothing_is_absent(self) -> None:
        record = self._run("true")

        self.assertEqual("absent", record.observation_status)
        self.assertEqual("the gate wrote no observation", record.observation_error)
        self.assertIsNone(record.observation_path)
        self.assertIsNone(record.observation_sha256)

    def test_a_document_breaking_the_contract_is_invalid_and_still_kept(self) -> None:
        broken = json.dumps({**DOCUMENT, "severity": "major"})

        record = self._run(f"{writes(broken)}; exit 1")

        self.assertEqual("invalid", record.observation_status)
        self.assertIn("unknown field severity", record.observation_error)
        # The document the controller refused is kept, like any it accepted.
        self.assertEqual(
            broken, Path(record.observation_path).read_text(encoding="utf-8")
        )
        self.assertEqual(64, len(record.observation_sha256))

    def test_a_document_disagreeing_with_the_exit_code_is_contradicted(self) -> None:
        passing = json.dumps({**DOCUMENT, "status": "passed"})

        record = self._run(f"{writes(passing)}; exit 1")

        self.assertFalse(record.passed)
        self.assertEqual(1, record.exit_code)
        self.assertEqual("contradicted", record.observation_status)
        self.assertIn("did not pass", record.observation_error)

    def test_nothing_a_document_says_changes_whether_the_gate_passed(self) -> None:
        failing = json.dumps({**DOCUMENT, "status": "failed"})

        record = self._run(f"{writes(failing)}; exit 0")

        self.assertTrue(record.passed)
        self.assertEqual("contradicted", record.observation_status)

    def test_a_timeout_excuses_only_a_document_that_was_never_written(self) -> None:
        absent = self._run("sleep 30", timeout_seconds=1)

        self.assertTrue(absent.timed_out)
        self.assertEqual("absent", absent.observation_status)

    def test_a_document_written_before_a_timeout_is_judged_like_any_other(
        self,
    ) -> None:
        broken = json.dumps({**DOCUMENT, "status": "errored"})

        record = self._run(f"{writes(broken)}; sleep 30", timeout_seconds=1)

        self.assertTrue(record.timed_out)
        self.assertIsNone(record.exit_code)
        self.assertEqual("invalid", record.observation_status)
        self.assertIn("field status", record.observation_error)

    def test_removes_the_location_once_the_result_is_recorded(self) -> None:
        record = self._run(
            f'{writes(json.dumps(DOCUMENT))};'
            f' dirname "${OBSERVATION_PATH_VARIABLE}" > written-location; exit 1'
        )

        location = Path(
            (self.out_dir.parent.parent / "tree" / "written-location")
            .read_text(encoding="utf-8")
            .strip()
        )
        self.assertFalse(location.exists())
        self.assertEqual("valid", record.observation_status)

    def test_the_four_fields_are_flat_and_the_digest_recomputes(self) -> None:
        record = self._run(f"{writes(json.dumps(DOCUMENT))}; exit 1")

        document = record.to_document()
        for field in OBSERVATION_FIELDS:
            self.assertIn(field, document)
        self.assertEqual(ResultFormat.CODESERVO_JSON, record.result_format)
        # `sha256_record` drops only top-level keys ending in `_path`, so the
        # digest recomputes from the record exactly as it is persisted.
        self.assertEqual(record.result_sha256, sha256_record(document))


class GateExitCodeModeTests(unittest.TestCase):
    """A gate that declared nothing behaves exactly as it did before."""

    def test_carries_the_format_and_none_of_the_four_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            results = run_gates(
                repo=root,
                gates=(Gate(name="unit", phase="quick", command="true"),),
                out_dir=root / "run" / "quick",
                run_dir=root / "run",
            )

            record = results[0]
            document = record.to_document()
            self.assertEqual(ResultFormat.EXIT_CODE, record.result_format)
            for field in OBSERVATION_FIELDS:
                self.assertNotIn(field, document)
            self.assertEqual(record.result_sha256, sha256_record(document))

    def test_sees_the_variable_unset_even_when_the_controller_carries_one(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inherited = root / "inherited.json"
            gates = (
                Gate(
                    name="declares-nothing",
                    phase="quick",
                    command=(
                        f'test -z "${OBSERVATION_PATH_VARIABLE}"'
                        f' || echo leaked > "{inherited}"; '
                        f'test -z "${OBSERVATION_PATH_VARIABLE}"'
                    ),
                ),
                Gate(
                    name="declares-the-format",
                    phase="quick",
                    command=(
                        f'test "${OBSERVATION_PATH_VARIABLE}" !='
                        f' "{inherited}"'
                    ),
                    result_format=ResultFormat.CODESERVO_JSON,
                ),
            )

            with patch.dict(
                os.environ, {OBSERVATION_PATH_VARIABLE: str(inherited)}
            ):
                results = run_gates(
                    repo=root,
                    gates=gates,
                    out_dir=root / "run" / "quick",
                    run_dir=root / "run",
                )

            declared_nothing, declared = results
            # The variable is unset for the gate that declared nothing, and is
            # not the inherited value for the gate that declared the format.
            self.assertTrue(declared_nothing.passed)
            self.assertTrue(declared.passed)
            # The gate saw no path, so the file the inherited value named was
            # never written.
            self.assertFalse(inherited.exists())


class ObservationLocationTests(unittest.TestCase):
    """Where a temporary directory lands is checked, never assumed."""

    def _refuses(self, landing: str) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "run"
            tree = root / "tree"
            (run_dir / "quick").mkdir(parents=True)
            tree.mkdir()
            (tree / "app.py").write_text("value = 2\n", encoding="utf-8")
            before = sorted(item.name for item in tree.iterdir())
            landed = {"run": run_dir, "tree": tree}[landing] / "observation-location"
            landed.mkdir()
            gate = Gate(
                name="mutation",
                phase="quick",
                command="touch ran-anyway",
                result_format=ResultFormat.CODESERVO_JSON,
            )

            with patch(
                "codeservo.sensors.gates.tempfile.mkdtemp", return_value=str(landed)
            ):
                with self.assertRaises(ObservationPathError) as refused:
                    run_gates(
                        repo=tree,
                        gates=(gate,),
                        out_dir=run_dir / "quick",
                        run_dir=run_dir,
                    )

            self.assertIn(str(landed), str(refused.exception))
            # Nothing is left behind in either, and the gate never ran.
            self.assertFalse(landed.exists())
            self.assertEqual(before, sorted(item.name for item in tree.iterdir()))
            self.assertFalse((tree / "ran-anyway").exists())
            self.assertEqual(
                [], sorted(item.name for item in (run_dir / "quick").iterdir())
            )

    def test_reports_a_removal_that_failed_instead_of_suppressing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gate = Gate(
                name="mutation",
                phase="quick",
                command="true",
                result_format=ResultFormat.CODESERVO_JSON,
            )

            with patch(
                "codeservo.sensors.gates.shutil.rmtree",
                side_effect=OSError("directory not empty"),
            ):
                with self.assertRaises(ObservationPathError) as reported:
                    run_gates(
                        repo=root,
                        gates=(gate,),
                        out_dir=root / "run" / "quick",
                        run_dir=root / "run",
                    )

            self.assertIn("could not be removed", str(reported.exception))
            self.assertIn("directory not empty", str(reported.exception))

    def test_refuses_a_location_inside_the_run_directory(self) -> None:
        self._refuses("run")

    def test_refuses_a_location_inside_the_measured_tree(self) -> None:
        self._refuses("tree")


@unittest.skipUnless(sys.platform == "darwin", "requires macOS sandbox-exec")
class GateConfinementTests(unittest.TestCase):
    def test_reads_the_run_directory_without_writing_to_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "run"
            sensor = run_dir / "sensors" / "acceptance"
            sensor.mkdir(parents=True)
            (sensor / "contract.py").write_text("assert True\n", encoding="utf-8")
            gates = (
                Gate(
                    name="reads-the-sensor",
                    phase="quick",
                    command='cat "$CODESERVO_SENSOR_PATH/contract.py"',
                    baseline=False,
                    sensor="example/acceptance",
                ),
                Gate(
                    name="writes-the-record",
                    phase="quick",
                    command=f'touch "{run_dir}/tampered.txt"',
                ),
            )

            if already_confined():
                self.assertEqual(os.EX_OSERR, nested_seatbelt_exit_code())
                self.assertTrue(protected_gate_record().is_dir())
                profile = seatbelt_profile(Isolation(read_only=(run_dir,)))
                self.assertIn("deny file-write*", profile)
                self.assertIn(str(run_dir.resolve()), profile)
                self.assertEqual(
                    "assert True\n", (sensor / "contract.py").read_text(encoding="utf-8")
                )
                return

            results = run_gates(
                repo=root,
                gates=gates,
                out_dir=run_dir / "quick",
                sensor_paths={"reads-the-sensor": sensor},
                isolation=Isolation(read_only=(run_dir,)),
            )

            reads, writes = results
            self.assertTrue(reads.passed)
            self.assertFalse(writes.passed)
            self.assertFalse((run_dir / "tampered.txt").exists())
            self.assertEqual(
                "assert True\n",
                Path(reads.stdout_path).read_text(encoding="utf-8"),
            )

    def test_cannot_rewrite_the_frozen_sensor_it_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "run"
            sensor = run_dir / "sensors" / "acceptance"
            sensor.mkdir(parents=True)
            contract = sensor / "contract.py"
            contract.write_text("assert True\n", encoding="utf-8")
            gate = Gate(
                name="acceptance",
                phase="quick",
                command='echo "assert False" > "$CODESERVO_SENSOR_PATH/contract.py"',
                baseline=False,
                sensor="example/acceptance",
            )

            if already_confined():
                self.assertEqual(os.EX_OSERR, nested_seatbelt_exit_code())
                self.assertTrue(protected_gate_record().is_dir())
                profile = seatbelt_profile(Isolation(read_only=(run_dir,)))
                self.assertIn("deny file-write*", profile)
                self.assertIn(str(run_dir.resolve()), profile)
                self.assertEqual("assert True\n", contract.read_text(encoding="utf-8"))
                return

            results = run_gates(
                repo=root,
                gates=(gate,),
                out_dir=run_dir / "quick",
                sensor_paths={"acceptance": sensor},
                isolation=Isolation(read_only=(run_dir,)),
            )

            self.assertFalse(results[0].passed)
            self.assertEqual("assert True\n", contract.read_text(encoding="utf-8"))

    def test_reads_the_git_metadata_it_cannot_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp) / "checkout"
            checkout.mkdir()
            (checkout / "app.py").write_text("value = 2\n", encoding="utf-8")
            commit_repository(checkout)
            metadata = checkout / ".git"
            isolation = Isolation(read_only=(metadata,))
            reads = " && ".join(
                [
                    "git status --porcelain",
                    "git diff --name-only HEAD --",
                    "git diff --numstat HEAD --",
                    "git diff --binary --no-ext-diff HEAD --",
                    "git log --oneline -1",
                    "git ls-files --others --exclude-standard",
                    "git rev-parse HEAD",
                ]
            )
            gates = (
                Gate(name="reads-the-metadata", phase="quick", command=reads),
                Gate(name="records-a-commit", phase="quick", command="git add -A"),
                Gate(
                    name="writes-the-metadata",
                    phase="quick",
                    command=f'echo tampered > "{metadata}/tampered.txt"',
                ),
            )

            if already_confined():
                profile = seatbelt_profile(isolation)
                self.assertIn("deny file-write*", profile)
                self.assertIn(str(metadata.resolve()), profile)
                self.assertNotIn("file-read*", profile)
                return

            (checkout / "untracked.py").write_text("value = 3\n", encoding="utf-8")
            results = run_gates(
                repo=checkout,
                gates=gates,
                out_dir=Path(temp) / "logs",
                isolation=isolation,
            )

            reading, records, writes = results
            self.assertTrue(
                reading.passed, Path(reading.stderr_path).read_text()
            )
            self.assertIn(
                "untracked.py", Path(reading.stdout_path).read_text()
            )
            self.assertFalse(records.passed)
            self.assertFalse(writes.passed)
            self.assertFalse((metadata / "tampered.txt").exists())

    def test_a_task_gate_runs_on_an_environment_it_cannot_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            write_provider(bin_dir, root)
            (root / "app.py").write_text("value = 2\n", encoding="utf-8")
            provider_dir = root / ".pixi"
            isolation = Isolation(read_only=(provider_dir,))
            gates = (
                Gate(name="task", phase="quick", task=PIXI_TASK),
                Gate(
                    name="writes-the-environment",
                    phase="quick",
                    command=f'echo tampered > "{provider_dir}/envs/default/tampered"',
                ),
            )

            if already_confined():
                profile = seatbelt_profile(isolation)
                self.assertIn("deny file-write*", profile)
                self.assertIn(str(provider_dir.resolve()), profile)
                self.assertNotIn("file-read*", profile)
                return

            path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            with patch.dict(os.environ, {"PATH": path}, clear=False):
                results = run_gates(
                    repo=root,
                    gates=gates,
                    out_dir=root / "logs",
                    isolation=isolation,
                    execution=EXECUTION,
                )

            task, writes = results
            self.assertTrue(task.passed, Path(task.stderr_path).read_text())
            self.assertFalse(writes.passed)
            self.assertFalse((provider_dir / "envs" / "default" / "tampered").exists())


if __name__ == "__main__":
    unittest.main()
