import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.codex import CodexError, _isolate_command


class SensorIsolationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "requires macOS sandbox-exec")
    def test_denies_sensor_reads_but_allows_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sensors = root / "sensors"
            sensors.mkdir()
            secret = sensors / "contract.py"
            secret.write_text("hidden\n", encoding="utf-8")
            visible = root / "visible.txt"
            visible.write_text("visible\n", encoding="utf-8")
            evidence = root / "runs" / "evidence.json"
            evidence.parent.mkdir()
            evidence.write_text("unchanged\n", encoding="utf-8")

            denied = subprocess.run(
                _isolate_command(["/bin/cat", str(secret)], (sensors,)),
                capture_output=True,
                check=False,
            )
            allowed = subprocess.run(
                _isolate_command(["/bin/cat", str(visible)], (sensors,)),
                capture_output=True,
                check=False,
            )
            denied_write = subprocess.run(
                _isolate_command(
                    ["/usr/bin/touch", str(evidence)], (evidence.parent,)
                ),
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(0, denied.returncode)
            self.assertNotEqual(0, denied_write.returncode)
            self.assertEqual(0, allowed.returncode)
            self.assertEqual(b"visible\n", allowed.stdout)
            self.assertEqual("unchanged\n", evidence.read_text(encoding="utf-8"))

    def test_fails_closed_without_supported_operating_system(self) -> None:
        with patch("codeservo.codex.sys.platform", "linux"):
            with self.assertRaisesRegex(CodexError, "requires macOS sandbox-exec"):
                _isolate_command(["codex", "exec"], (Path("sensors"),))


if __name__ == "__main__":
    unittest.main()
