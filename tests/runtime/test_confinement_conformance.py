"""What a profile means, held against whichever mechanism the host carries.

These are the properties the controller relies on, written once and run
against the adapter this host answers with. Two mechanisms say them
differently — one denies over an allowed default, the other builds up a mount
namespace — and the point of the port is that a run cannot tell.

A refusal is only evidence when the same profile also permits something, so
every denial below sits beside a reading that must succeed under the same
profile. A profile that was never applied refuses everything for the wrong
reason and would read exactly like a confinement that works.
"""

import os
import subprocess
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

from codeservo.runtime.confinement import confined, host_confiner
from codeservo.runtime.sandbox import Isolation, SandboxError
from isolation_harness import already_confined


def _refused() -> str | None:
    """What applying a profile here answers with, when it does not apply."""
    with tempfile.TemporaryDirectory() as measured, tempfile.TemporaryDirectory() as kept:
        root = Path(measured).resolve()
        stderr = Path(kept) / "stderr.log"
        try:
            with confined(
                ["/bin/sh", "-c", "exit 0"], Isolation(read_only=(root,))
            ) as taken:
                with stderr.open("wb") as stream:
                    completed = subprocess.run(
                        taken.command,
                        stdout=subprocess.DEVNULL,
                        stderr=stream,
                        check=False,
                        pass_fds=taken.pass_fds,
                    )
                taken.confirm(completed.returncode, stderr)
        except SandboxError as refused:
            return str(refused)
    if completed.returncode != 0:
        return f"a profile did not apply here: exit {completed.returncode}"
    return None


@lru_cache(maxsize=1)
def unnestable() -> str | None:
    """Why this process cannot apply a profile of its own, or nothing.

    A suite measuring a confinement has to apply one, and a process already
    carrying one cannot always apply another: macOS refuses a seatbelt inside
    a seatbelt, which is what this suite meets when it is itself a gate.
    bubblewrap nests. The answer is measured rather than derived from the
    platform name.

    Only that limit is a reason to skip. A profile that fails to apply in a
    process carrying none is a fault in the mechanism, and a host with no
    mechanism at all is a failure of its own, so neither answers here: the
    tests below then run and report what they meet.
    """
    try:
        host_confiner()
    except SandboxError:
        return None
    reason = _refused()
    return reason if reason is not None and already_confined() else None


@unittest.skipIf(unnestable() is not None, unnestable() or "")
class ConfinementConformanceTests(unittest.TestCase):
    """The contract every mechanism behind the port answers to."""

    def setUp(self) -> None:
        measured = tempfile.TemporaryDirectory()
        self.addCleanup(measured.cleanup)
        kept = tempfile.TemporaryDirectory()
        self.addCleanup(kept.cleanup)
        # The logs live outside every rule, so what a test reads back is never
        # what a rule under test decided.
        self.logs = Path(kept.name).resolve()

        root = Path(measured.name).resolve()
        self.free = root / "free"
        self.free.mkdir()
        (self.free / "app.py").write_text("value = 1\n", encoding="utf-8")
        self.readable = root / "readable"
        (self.readable / "deep").mkdir(parents=True)
        (self.readable / "head.txt").write_text("head\n", encoding="utf-8")
        (self.readable / "deep" / "buried.txt").write_text("buried\n", encoding="utf-8")
        self.hidden = root / "hidden"
        (self.hidden / "inner").mkdir(parents=True)
        (self.hidden / "contract.py").write_text("assert True\n", encoding="utf-8")
        (self.hidden / "inner" / "buried.py").write_text("buried\n", encoding="utf-8")

        self.isolation = Isolation(denied=(self.hidden,), read_only=(self.readable,))

    def _shell(self, script: str) -> tuple[int, str]:
        """Run a script under the profile, and return its code and its output."""
        stdout = self.logs / "stdout.log"
        stderr = self.logs / "stderr.log"
        with confined(["/bin/sh", "-c", script], self.isolation) as taken:
            with stdout.open("wb") as out, stderr.open("wb") as err:
                completed = subprocess.run(
                    taken.command,
                    stdout=out,
                    stderr=err,
                    check=False,
                    pass_fds=taken.pass_fds,
                )
            # A refusal below means nothing unless the command actually ran.
            taken.confirm(completed.returncode, stderr)
        return completed.returncode, stdout.read_text(encoding="utf-8")

    def _permits(self, subject: str, script: str) -> str:
        exit_code, output = self._shell(script)
        self.assertEqual(0, exit_code, f"{subject} was refused")
        return output

    def _refuses(self, subject: str, script: str) -> None:
        exit_code, _ = self._shell(script)
        self.assertNotEqual(0, exit_code, f"{subject} went through")

    def test_a_path_no_rule_names_is_read_and_written(self) -> None:
        self.assertEqual(
            "value = 1\n", self._permits("reading it", f"cat {self.free}/app.py")
        )
        self._permits("writing it", f"echo 2 > {self.free}/written")
        self.assertTrue((self.free / "written").is_file())

    def test_the_exit_code_a_command_answers_with_is_the_one_read_back(self) -> None:
        exit_code, _ = self._shell("exit 7")

        self.assertEqual(7, exit_code)

    def test_a_read_only_path_is_read_to_its_depth(self) -> None:
        self.assertEqual(
            "head\n", self._permits("reading it", f"cat {self.readable}/head.txt")
        )
        self.assertEqual(
            "buried\n",
            self._permits("reading in depth", f"cat {self.readable}/deep/buried.txt"),
        )

    def test_every_way_of_writing_a_read_only_path_is_refused(self) -> None:
        for subject, script in (
            ("overwriting a file", f"echo x > {self.readable}/head.txt"),
            ("creating a file", f"echo x > {self.readable}/made.txt"),
            ("creating one in depth", f"echo x > {self.readable}/deep/made.txt"),
            ("creating a directory", f"mkdir {self.readable}/made"),
            ("deleting a file", f"rm -f {self.readable}/head.txt"),
            ("renaming a file", f"mv {self.readable}/head.txt {self.readable}/moved"),
            ("changing a mode", f"chmod 777 {self.readable}/head.txt"),
            ("creating a symlink", f"ln -s /etc/passwd {self.readable}/link"),
        ):
            with self.subTest(subject=subject):
                self._refuses(subject, script)

        self.assertEqual("head\n", (self.readable / "head.txt").read_text())
        self.assertEqual(
            ["deep", "head.txt"],
            sorted(path.name for path in self.readable.iterdir()),
            "a refused write left something behind",
        )

    def test_no_byte_of_a_denied_path_is_obtainable(self) -> None:
        for subject, script in (
            ("reading a denied file", f"cat {self.hidden}/contract.py"),
            ("reading one in depth", f"cat {self.hidden}/inner/buried.py"),
        ):
            with self.subTest(subject=subject):
                self._refuses(subject, script)

        _, listed = self._shell(f"ls -A {self.hidden}")
        self.assertNotIn("contract.py", listed)

    def test_writing_a_denied_path_is_refused_and_the_host_keeps_its_own(self) -> None:
        self._refuses("writing into it", f"echo x > {self.hidden}/written")

        self.assertFalse((self.hidden / "written").exists())
        self.assertEqual("assert True\n", (self.hidden / "contract.py").read_text())

    def test_a_symlink_made_under_the_profile_does_not_reach_what_it_names(
        self,
    ) -> None:
        self._refuses(
            "reading through a symlink made inside",
            f"ln -sfn {self.hidden} {self.free}/late && cat {self.free}/late/contract.py",
        )

    def test_a_descriptor_opened_before_the_profile_still_carries_the_stream(
        self,
    ) -> None:
        # This is how a gate writes its logs into the run directory it may not
        # open: the controller opens the file, the confined process inherits it.
        carried = self.readable / "inherited.log"
        opened = os.open(str(carried), os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        try:
            with confined(["/bin/sh", "-c", "echo measured"], self.isolation) as taken:
                subprocess.run(
                    taken.command,
                    stdout=opened,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    pass_fds=taken.pass_fds,
                )
        finally:
            os.close(opened)

        self.assertEqual("measured\n", carried.read_text(encoding="utf-8"))
        self._refuses(
            "opening the same file from inside", f"echo x > {self.readable}/opened.log"
        )


if __name__ == "__main__":
    unittest.main()
