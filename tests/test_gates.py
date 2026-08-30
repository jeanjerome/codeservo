import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.gates import run_gates
from codeservo.model import Gate


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


if __name__ == "__main__":
    unittest.main()
