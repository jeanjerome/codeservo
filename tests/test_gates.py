import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.gates import run_gates
from codeservo.model import Gate
from codeservo.sandbox import Isolation


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
