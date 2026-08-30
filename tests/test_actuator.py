import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.actuator import (
    ACTUATOR_ENV_VAR,
    ActuatorError,
    Isolation,
    default_actuator_name,
    isolation_evidence,
    load_actuator,
    seatbelt_command,
)


class SeatbeltTests(unittest.TestCase):
    def test_leaves_command_unchanged_without_isolation(self) -> None:
        self.assertEqual(
            ["codex", "exec"], seatbelt_command(["codex", "exec"], Isolation())
        )

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
            isolation = Isolation(denied=(sensors, evidence.parent))

            denied = subprocess.run(
                seatbelt_command(["/bin/cat", str(secret)], isolation),
                capture_output=True,
                check=False,
            )
            allowed = subprocess.run(
                seatbelt_command(["/bin/cat", str(visible)], isolation),
                capture_output=True,
                check=False,
            )
            denied_write = subprocess.run(
                seatbelt_command(["/usr/bin/touch", str(evidence)], isolation),
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(0, denied.returncode)
            self.assertNotEqual(0, denied_write.returncode)
            self.assertEqual(0, allowed.returncode)
            self.assertEqual(b"visible\n", allowed.stdout)
            self.assertEqual("unchanged\n", evidence.read_text(encoding="utf-8"))

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS sandbox-exec")
    def test_read_only_paths_stay_readable_and_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "repo"
            source.mkdir()
            tracked = source / "app.py"
            tracked.write_text("value = 1\n", encoding="utf-8")
            isolation = Isolation(read_only=(source,))

            read = subprocess.run(
                seatbelt_command(["/bin/cat", str(tracked)], isolation),
                capture_output=True,
                check=False,
            )
            write = subprocess.run(
                seatbelt_command(
                    ["/usr/bin/touch", str(source / "new.py")], isolation
                ),
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, read.returncode)
            self.assertEqual(b"value = 1\n", read.stdout)
            self.assertNotEqual(0, write.returncode)
            self.assertFalse((source / "new.py").exists())

    def test_fails_closed_without_supported_operating_system(self) -> None:
        with patch("codeservo.actuator.sys.platform", "linux"):
            with self.assertRaisesRegex(ActuatorError, "requires macOS sandbox-exec"):
                seatbelt_command(["codex", "exec"], Isolation(denied=(Path("s"),)))


class IsolationEvidenceTests(unittest.TestCase):
    def test_reports_absolute_paths_for_both_denial_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            evidence = isolation_evidence(
                Isolation(denied=(root / "sensors",), read_only=(root / "repo",)),
                "macos-sandbox-exec",
            )

            self.assertEqual("macos-sandbox-exec", evidence["mechanism"])
            self.assertEqual([str(root / "sensors")], evidence["denied_paths"])
            self.assertEqual([str(root / "repo")], evidence["read_only_paths"])
            self.assertTrue(evidence["user_config_ignored"])


class ActuatorSelectionTests(unittest.TestCase):
    def test_loads_every_declared_actuator(self) -> None:
        for name in ("claude", "codex"):
            with self.subTest(actuator=name):
                self.assertEqual(name, load_actuator(name).name)

    def test_rejects_unknown_actuator(self) -> None:
        with self.assertRaisesRegex(ActuatorError, "unknown actuator: gpt"):
            load_actuator("gpt")

    def test_environment_selects_the_default_actuator(self) -> None:
        with patch.dict(os.environ, {ACTUATOR_ENV_VAR: "codex"}, clear=False):
            self.assertEqual("codex", default_actuator_name())

    def test_default_actuator_is_claude(self) -> None:
        with patch.dict(os.environ, {ACTUATOR_ENV_VAR: ""}, clear=False):
            self.assertEqual("claude", default_actuator_name())

    def test_rejects_unknown_actuator_in_the_environment(self) -> None:
        with patch.dict(os.environ, {ACTUATOR_ENV_VAR: "gpt"}, clear=False):
            with self.assertRaisesRegex(ActuatorError, "is not one of"):
                default_actuator_name()


if __name__ == "__main__":
    unittest.main()
