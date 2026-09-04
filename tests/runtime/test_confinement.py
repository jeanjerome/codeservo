import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeservo.runtime.confinement import (
    MECHANISMS,
    ConfinedCommand,
    confined,
    host_confiner,
    load_confiner,
    mechanism,
)
from codeservo.runtime.sandbox import Isolation, Mechanism, SandboxError
from isolation_harness import already_confined


class HostConfinerTests(unittest.TestCase):
    def setUp(self) -> None:
        host_confiner.cache_clear()
        self.addCleanup(host_confiner.cache_clear)

    def test_this_host_applies_a_mechanism_and_names_it(self) -> None:
        # Nothing below is evidence if no mechanism answers: a suite that
        # skipped here would report a confinement it never exercised.
        self.assertIn(mechanism(), tuple(Mechanism))
        self.assertEqual(host_confiner().mechanism, mechanism())

    @unittest.skipUnless(sys.platform == "darwin", "the mechanism is the host's")
    def test_macos_answers_with_its_seatbelt(self) -> None:
        self.assertEqual(Mechanism.MACOS_SANDBOX_EXEC, mechanism())

    def test_a_host_without_a_mechanism_refuses_rather_than_running_free(
        self,
    ) -> None:
        with patch(
            "codeservo.runtime.seatbelt.unusable", return_value="not this host"
        ):
            with patch(
                "codeservo.runtime.bubblewrap.unusable", return_value="nor this one"
            ):
                with self.assertRaises(SandboxError) as refused:
                    host_confiner()

        self.assertIn("no confinement mechanism on this host", str(refused.exception))
        self.assertIn("macos-sandbox-exec: not this host", str(refused.exception))
        self.assertIn("linux-bubblewrap: nor this one", str(refused.exception))

    def test_every_mechanism_a_record_can_hold_has_an_adapter_or_a_producer(
        self,
    ) -> None:
        for name in MECHANISMS:
            with self.subTest(mechanism=name):
                self.assertEqual(name, load_confiner(name).mechanism)

    def test_a_mechanism_no_adapter_answers_for_is_refused_by_name(self) -> None:
        with self.assertRaisesRegex(SandboxError, "unknown confinement mechanism"):
            load_confiner("linux-landlock")


class ConfinedCommandTests(unittest.TestCase):
    def test_an_empty_profile_leaves_the_command_and_asks_for_no_mechanism(
        self,
    ) -> None:
        with patch("codeservo.runtime.confinement.host_confiner") as never:
            with confined(["codex", "exec"], Isolation()) as application:
                self.assertEqual(["codex", "exec"], application.command)
                self.assertEqual((), application.pass_fds)
                # No mechanism applied, so no exit code can mean one failed.
                application.confirm(71)

        never.assert_not_called()

    def test_a_profile_that_could_not_be_applied_stops_the_run(self) -> None:
        confinement = ConfinedCommand(
            command=["true"], pass_fds=(), fault=lambda _code, _stderr: "no namespace"
        )

        with self.assertRaisesRegex(SandboxError, "did not run confined"):
            confinement.confirm(1)

    @unittest.skipUnless(sys.platform == "darwin", "runs the host's own mechanism")
    @unittest.skipIf(already_confined(), "macOS refuses a seatbelt inside another")
    def test_a_command_that_could_not_start_is_not_a_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stderr_path = root / "stderr.log"
            isolation = Isolation(read_only=(root,))

            with confined(["/nonexistent-command"], isolation) as application:
                with stderr_path.open("wb") as stderr:
                    completed = subprocess.run(
                        application.command,
                        stdout=subprocess.DEVNULL,
                        stderr=stderr,
                        check=False,
                    )
                # The command never ran, so its exit code says nothing about
                # the tree, and reading it as a failed measurement would.
                with self.assertRaises(SandboxError) as refused:
                    application.confirm(completed.returncode, stderr_path)

            self.assertNotEqual(0, completed.returncode)
            self.assertIn("sandbox-exec", str(refused.exception))

    @unittest.skipUnless(sys.platform == "darwin", "runs the host's own mechanism")
    @unittest.skipIf(already_confined(), "macOS refuses a seatbelt inside another")
    def test_a_command_exiting_that_same_code_itself_is_a_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stderr_path = root / "stderr.log"
            isolation = Isolation(read_only=(root,))

            with confined(["/bin/sh", "-c", "exit 71"], isolation) as application:
                with stderr_path.open("wb") as stderr:
                    completed = subprocess.run(
                        application.command,
                        stdout=subprocess.DEVNULL,
                        stderr=stderr,
                        check=False,
                    )
                application.confirm(completed.returncode, stderr_path)

            self.assertEqual(71, completed.returncode)
            self.assertEqual("", stderr_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
