import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.gates import gate_command, run_gates
from codeservo.model import ExecutionEnvironment, Gate
from codeservo.sandbox import Isolation, seatbelt_profile
from harness import PIXI_TASK, write_provider
from isolation_harness import (
    already_confined,
    nested_seatbelt_exit_code,
    protected_gate_record,
)


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

            self.assertTrue(all(result["passed"] for result in results))

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

            self.assertTrue(all(result["passed"] for result in results))

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

            self.assertTrue(results[0]["passed"])

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
            self.assertTrue(task["passed"], Path(task["stderr_path"]).read_text())
            self.assertTrue(shell["passed"])
            # A shell gate is built and run exactly as it was before.
            self.assertEqual("test -f app.py", shell["command"])
            self.assertEqual(
                f"pixi run --as-is --clean-env --no-config"
                f" --manifest-path '{root / 'pyproject.toml'}'"
                f" --environment 'default' '{PIXI_TASK}'",
                task["command"],
            )


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
            self.assertTrue(reads["passed"])
            self.assertFalse(writes["passed"])
            self.assertFalse((run_dir / "tampered.txt").exists())
            self.assertEqual(
                "assert True\n",
                Path(reads["stdout_path"]).read_text(encoding="utf-8"),
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

            self.assertFalse(results[0]["passed"])
            self.assertEqual("assert True\n", contract.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
