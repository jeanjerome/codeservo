import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.runtime.sandbox import (
    Isolation,
    SandboxError,
    isolation_evidence,
    seatbelt_command,
    seatbelt_profile,
)
from isolation_harness import (
    already_confined,
    nested_seatbelt_exit_code,
    protected_gate_record,
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

            if already_confined():
                self.assertEqual(os.EX_OSERR, nested_seatbelt_exit_code())
                self.assertTrue(protected_gate_record().is_dir())
                profile = seatbelt_profile(isolation)
                self.assertIn("deny file-read* file-write*", profile)
                self.assertIn(str(sensors.resolve()), profile)
                self.assertIn(str(evidence.parent.resolve()), profile)
                self.assertEqual("visible\n", visible.read_text(encoding="utf-8"))
                self.assertEqual("unchanged\n", evidence.read_text(encoding="utf-8"))
                return

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

            if already_confined():
                self.assertEqual(os.EX_OSERR, nested_seatbelt_exit_code())
                self.assertTrue(protected_gate_record().is_dir())
                profile = seatbelt_profile(isolation)
                self.assertIn("deny file-write*", profile)
                self.assertIn(str(source.resolve()), profile)
                self.assertEqual("value = 1\n", tracked.read_text(encoding="utf-8"))
                return

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
        with patch("codeservo.runtime.sandbox.sys.platform", "linux"):
            with self.assertRaisesRegex(SandboxError, "requires macOS sandbox-exec"):
                seatbelt_command(["codex", "exec"], Isolation(denied=(Path("s"),)))


class IsolationEvidenceTests(unittest.TestCase):
    def test_reports_absolute_paths_for_both_denial_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            evidence = isolation_evidence(
                Isolation(denied=(root / "sensors",), read_only=(root / "repo",)),
                "macos-sandbox-exec",
            )

            self.assertEqual("macos-sandbox-exec", evidence.mechanism)
            self.assertEqual((str(root / "sensors"),), evidence.denied_paths)
            self.assertEqual((str(root / "repo"),), evidence.read_only_paths)
            self.assertTrue(evidence.user_config_ignored)
            # The record carries JSON arrays, whatever the document holds.
            self.assertEqual(
                [str(root / "sensors")], evidence.to_document()["denied_paths"]
            )


if __name__ == "__main__":
    unittest.main()
