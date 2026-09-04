import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from codeservo.runtime import bubblewrap
from codeservo.runtime.sandbox import Isolation, SandboxError


def _argv(isolation: Isolation) -> list[str]:
    """The command line one application builds, for a command of no interest."""
    with bubblewrap.applied(["/bin/sh", "-c", "true"], isolation) as application:
        return list(application.command)


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def test_the_filesystem_is_bound_through_before_any_rule(self) -> None:
        argv = _argv(Isolation())

        self.assertEqual(
            ["bwrap", "--dev-bind", "/", "/", "--die-with-parent"], argv[:5]
        )
        self.assertIn("--", argv)
        self.assertEqual(["/bin/sh", "-c", "true"], argv[argv.index("--") + 1 :])

    def test_a_read_only_rule_comes_before_a_denial(self) -> None:
        tree = self.root / "tree"
        (tree / ".git").mkdir(parents=True)

        argv = _argv(Isolation(denied=(tree / ".git",), read_only=(tree,)))

        # A later rule wins, so the denial has to be emitted second: reversed,
        # the read-only tree would put the metadata back.
        self.assertLess(argv.index(str(tree)), argv.index(str(tree / ".git")))

    def test_a_denied_directory_is_bound_from_an_empty_one(self) -> None:
        sensors = self.root / "sensors"
        sensors.mkdir()
        (sensors / "contract.py").write_text("assert True\n", encoding="utf-8")

        with bubblewrap.applied(["true"], Isolation(denied=(sensors,))) as taken:
            destination = taken.command.index(str(sensors))
            source = Path(taken.command[destination - 1])

            self.assertEqual("--ro-bind", taken.command[destination - 2])
            self.assertNotEqual(sensors, source)
            # What the process sees in its place holds nothing, which is the
            # denial: a tmpfs there would take the write and report a success.
            self.assertEqual([], list(source.iterdir()))

        # The profile is not a string the command carries, so what it is built
        # from lasts exactly as long as the command does.
        self.assertFalse(source.exists())

    def test_a_denied_file_is_bound_from_the_null_device(self) -> None:
        pointer = self.root / ".git"
        pointer.write_text("gitdir: /elsewhere\n", encoding="utf-8")

        argv = _argv(Isolation(denied=(pointer,)))

        self.assertEqual(os.devnull, argv[argv.index(str(pointer)) - 1])

    def test_a_denied_path_that_is_not_there_holds_nothing_and_is_not_made(
        self,
    ) -> None:
        absent = self.root / "sensors"

        argv = _argv(Isolation(denied=(absent,)))

        self.assertNotIn(str(absent), argv)
        # Binding over it would create it, on the real filesystem, in the very
        # directory the profile exists to leave alone.
        self.assertFalse(absent.exists())

    def test_a_read_only_path_that_is_not_there_is_refused_by_name(self) -> None:
        absent = self.root / "worktree"

        with self.assertRaises(SandboxError) as refused:
            _argv(Isolation(read_only=(absent,)))

        self.assertIn(str(absent), str(refused.exception))

    def test_the_status_channel_is_named_and_passed_through(self) -> None:
        with bubblewrap.applied(["true"], Isolation()) as application:
            argv = application.command
            named = argv[argv.index("--json-status-fd") + 1]

            self.assertEqual((int(named),), application.pass_fds)


class FaultTests(unittest.TestCase):
    """What separates a command that ran from a profile that never applied."""

    def _channel(self, *messages: dict) -> int:
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        os.set_blocking(read_fd, False)
        for message in messages:
            os.write(write_fd, (json.dumps(message) + "\n").encode("utf-8"))
        os.close(write_fd)
        return read_fd

    def test_an_exit_code_is_what_says_the_command_ran(self) -> None:
        channel = self._channel({"child-pid": 7}, {"exit-code": 1})

        self.assertIsNone(bubblewrap.fault(channel, None))

    def test_a_child_pid_alone_says_nothing_about_the_mounts(self) -> None:
        # bubblewrap writes the pid before it applies them, so a profile that
        # then failed has still reported one.
        channel = self._channel({"child-pid": 7})

        self.assertEqual(
            "bwrap reported no command that ran", bubblewrap.fault(channel, None)
        )

    def test_what_bubblewrap_wrote_names_the_fault_when_it_wrote_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stderr = Path(temp) / "stderr.log"
            stderr.write_text(
                "bwrap: setting up uid map: Permission denied\n", encoding="utf-8"
            )
            channel = self._channel({"child-pid": 7})

            self.assertEqual(
                "bwrap: setting up uid map: Permission denied",
                bubblewrap.fault(channel, stderr),
            )

    def test_a_silent_channel_is_not_read_as_a_command_that_ran(self) -> None:
        self.assertIsNotNone(bubblewrap.fault(self._channel(), None))


class AvailabilityTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "linux", "states what another host lacks")
    def test_another_operating_system_says_why_it_cannot(self) -> None:
        self.assertEqual("bubblewrap is Linux only", bubblewrap.unusable())

    def test_a_linux_host_without_the_binary_says_so(self) -> None:
        with patch("codeservo.runtime.bubblewrap.sys.platform", "linux"):
            with patch("codeservo.runtime.bubblewrap.shutil.which", return_value=None):
                self.assertEqual("bwrap is not on PATH", bubblewrap.unusable())

    def test_a_binary_that_cannot_apply_a_profile_reports_what_it_said(self) -> None:
        refusal = Mock(
            returncode=1, stderr=b"bwrap: setting up uid map: Permission denied\n"
        )
        with patch("codeservo.runtime.bubblewrap.sys.platform", "linux"):
            with patch(
                "codeservo.runtime.bubblewrap.shutil.which", return_value="/usr/bin/bwrap"
            ):
                with patch(
                    "codeservo.runtime.bubblewrap.subprocess.run", return_value=refusal
                ):
                    self.assertEqual(
                        "bwrap cannot apply a profile here:"
                        " bwrap: setting up uid map: Permission denied",
                        bubblewrap.unusable(),
                    )


if __name__ == "__main__":
    unittest.main()
