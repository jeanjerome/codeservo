import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.runtime import seatbelt
from codeservo.runtime.sandbox import Isolation
from isolation_harness import (
    already_confined,
    nested_seatbelt_exit_code,
    protected_gate_record,
)


class SeatbeltProfileTests(unittest.TestCase):
    def test_denied_paths_lose_reading_and_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            # A rule names where the path really is: on macOS a temporary
            # directory is reached through a symbolic link, and a rule on the
            # link would name nothing the process opens.
            named = Path(temp)

            profile = seatbelt.profile(Isolation(denied=(named,)))

            self.assertIn("(allow default)", profile)
            self.assertIn(
                f'(deny file-read* file-write* (subpath "{named.resolve()}"))',
                profile,
            )

    def test_read_only_paths_lose_writing_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            named = Path(temp)

            profile = seatbelt.profile(Isolation(read_only=(named,)))

            self.assertIn(
                f'(deny file-write* (subpath "{named.resolve()}"))', profile
            )
            self.assertNotIn("file-read*", profile)

    def test_a_quote_in_a_path_cannot_end_the_rule_it_sits_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            quoted = Path(temp) / 'a"b'
            quoted.mkdir()

            profile = seatbelt.profile(Isolation(denied=(quoted,)))

            self.assertIn('a\\"b', profile)


class SeatbeltAvailabilityTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "states what this host has")
    def test_macos_carries_the_mechanism(self) -> None:
        self.assertIsNone(seatbelt.unusable())

    def test_another_operating_system_says_why_it_cannot(self) -> None:
        with patch("codeservo.runtime.seatbelt.sys.platform", "linux"):
            self.assertEqual("sandbox-exec is macOS only", seatbelt.unusable())


class SeatbeltFaultTests(unittest.TestCase):
    """What separates a command that never ran from one that ran and failed."""

    def _stderr(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "stderr.log"
        path.write_text(text, encoding="utf-8")
        return path

    def test_an_exit_code_the_command_owns_is_never_a_fault(self) -> None:
        reported = self._stderr("FAILED (failures=1)\n")

        self.assertIsNone(seatbelt.fault(1, reported))
        self.assertIsNone(seatbelt.fault(os.EX_OSERR, reported))

    def test_the_report_sandbox_exec_writes_is_what_names_a_fault(self) -> None:
        for code in seatbelt.DID_NOT_RUN:
            with self.subTest(exit_code=code):
                reported = self._stderr(
                    "sandbox-exec: syntax error: expecting ')'\n\nBacktrace: \n"
                )

                self.assertEqual(
                    "sandbox-exec: syntax error: expecting ')'",
                    seatbelt.fault(code, reported),
                )

    def test_a_missing_stream_reports_no_fault(self) -> None:
        self.assertIsNone(seatbelt.fault(os.EX_OSERR, None))
        self.assertIsNone(seatbelt.fault(os.EX_OSERR, Path("/nonexistent/stderr")))


@unittest.skipUnless(sys.platform == "darwin", "requires macOS sandbox-exec")
class SeatbeltDenialTests(unittest.TestCase):
    def _run(self, command: list[str], isolation: Isolation) -> subprocess.CompletedProcess:
        with seatbelt.applied(command, isolation) as application:
            return subprocess.run(
                application.command, capture_output=True, check=False
            )

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
                profile = seatbelt.profile(isolation)
                self.assertIn("deny file-read* file-write*", profile)
                self.assertIn(str(sensors.resolve()), profile)
                self.assertIn(str(evidence.parent.resolve()), profile)
                self.assertEqual("visible\n", visible.read_text(encoding="utf-8"))
                self.assertEqual("unchanged\n", evidence.read_text(encoding="utf-8"))
                return

            denied = self._run(["/bin/cat", str(secret)], isolation)
            allowed = self._run(["/bin/cat", str(visible)], isolation)
            denied_write = self._run(["/usr/bin/touch", str(evidence)], isolation)

            self.assertNotEqual(0, denied.returncode)
            self.assertNotEqual(0, denied_write.returncode)
            self.assertEqual(0, allowed.returncode)
            self.assertEqual(b"visible\n", allowed.stdout)
            self.assertEqual("unchanged\n", evidence.read_text(encoding="utf-8"))

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
                profile = seatbelt.profile(isolation)
                self.assertIn("deny file-write*", profile)
                self.assertIn(str(source.resolve()), profile)
                self.assertEqual("value = 1\n", tracked.read_text(encoding="utf-8"))
                return

            read = self._run(["/bin/cat", str(tracked)], isolation)
            write = self._run(["/usr/bin/touch", str(source / "new.py")], isolation)

            self.assertEqual(0, read.returncode)
            self.assertEqual(b"value = 1\n", read.stdout)
            self.assertNotEqual(0, write.returncode)
            self.assertFalse((source / "new.py").exists())


if __name__ == "__main__":
    unittest.main()
