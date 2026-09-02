"""Frozen sensors, and what says one of them moved."""

import tempfile
import unittest
from pathlib import Path

from codeservo.controller.freeze import altered_sensors
from codeservo.evidence.digests import sha256_path


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

            self.assertEqual([], altered_sensors(paths, evidence))

    def test_reports_a_snapshot_a_gate_wrote_into(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths, evidence = self._frozen(Path(temp))
            (paths["acceptance"] / "__pycache__").mkdir()
            (paths["acceptance"] / "__pycache__" / "contract.pyc").write_bytes(b"\x00")

            self.assertEqual(["acceptance"], altered_sensors(paths, evidence))


if __name__ == "__main__":
    unittest.main()
