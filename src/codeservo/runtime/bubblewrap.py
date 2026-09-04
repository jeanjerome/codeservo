"""The Linux mechanism: a mount namespace built by `bubblewrap`.

Where a seatbelt profile denies over `(allow default)`, bubblewrap builds up
what a process can see, so the same profile is written as a transparent bind
of the whole filesystem and then the rules that take things back. Everything
below was measured rather than read off the manual, because the two mechanisms
agree on what a profile means and on almost nothing about how to say it.

The order carries meaning: a later rule wins, so read-only paths are emitted
before denials, and the reverse order loses a denial that sits inside a
read-only tree. A denial is an empty directory bound read-only, never a
tmpfs, because a tmpfs accepts the write and tells the process it succeeded.
A denied file is `/dev/null`, because a directory cannot be bound over a file
and `/dev/null` refuses the read outright.

Nothing here binds over a path that is not there. bubblewrap creates a missing
mount point, and it creates it on the real filesystem: binding over an absent
`.git` would leave an empty one behind, and the confinement would have changed
the directory it exists to keep untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .confinement import ConfinedCommand
from .sandbox import Isolation, Mechanism, SandboxError

BINARY = "bwrap"
MECHANISM = Mechanism.LINUX_BUBBLEWRAP
# Present on every POSIX host, so a profile that fails to apply is the only
# thing this can report.
PROBE = ("/bin/sh", "-c", "")


def unusable() -> str | None:
    """Why this host cannot apply a bubblewrap profile, or nothing.

    A stock Ubuntu 24.04 restricts unprivileged user namespaces through
    AppArmor and installs no profile for `bwrap`, which then fails setting up
    its uid map. So the mechanism is established by applying one, never
    inferred from the platform name or from the binary being installed.
    """
    if sys.platform != "linux":
        return "bubblewrap is Linux only"
    binary = shutil.which(BINARY)
    if binary is None:
        return f"{BINARY} is not on PATH"
    probe = subprocess.run(
        [binary, "--dev-bind", "/", "/", "--", *PROBE],
        capture_output=True,
        check=False,
    )
    if probe.returncode == 0:
        return None
    reported = probe.stderr.decode("utf-8", errors="replace").strip().splitlines()
    said = reported[0] if reported else f"it exited {probe.returncode}"
    return f"{BINARY} cannot apply a profile here: {said}"


def _rules(isolation: Isolation, empty: Path) -> list[str]:
    """The profile, as the arguments that build it, in the order they apply.

    A read-only path that is not there is a fault: the run named a tree, a
    record or an environment it works through, and one of them is missing. A
    denied path that is not there holds nothing to deny, and the alternative
    would be to create it.
    """
    rules: list[str] = []
    for path in isolation.read_only:
        resolved = path.resolve()
        if not resolved.exists():
            raise SandboxError(f"the profile reads a path that is not there: {resolved}")
        rules += ["--ro-bind", str(resolved), str(resolved)]
    for path in isolation.denied:
        resolved = path.resolve()
        if not resolved.exists():
            continue
        source = os.devnull if resolved.is_file() else str(empty)
        rules += ["--ro-bind", source, str(resolved)]
    return rules


def _reported(status_fd: int) -> list[dict]:
    """What bubblewrap wrote about the process it started.

    The descriptor is read without waiting for the end of the stream: the
    process has completed, so everything it will ever say is already there,
    and the writing end is still held open by this one.
    """
    chunks = []
    while True:
        try:
            block = os.read(status_fd, 4096)
        except BlockingIOError:
            break
        if not block:
            break
        chunks.append(block)
    messages = []
    for line in b"".join(chunks).decode("utf-8", errors="replace").splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            messages.append(message)
    return messages


def _first_line(stderr: Path | None) -> str:
    if stderr is None or not stderr.is_file():
        return ""
    with stderr.open("r", encoding="utf-8", errors="replace") as stream:
        return stream.readline().strip()


def fault(status_fd: int, stderr: Path | None) -> str | None:
    """What stopped the command from running under the profile, or nothing.

    `--json-status-fd` says twice what `--info-fd` says once, and only the
    second message is worth anything here: the child's pid is written before
    the mounts are applied, so a profile that then fails still reported one.
    An exit code is written when the command has run and exited, and nothing
    else establishes that it did — a profile bubblewrap could not apply and a
    measurement that failed both leave the caller holding an exit code of 1.
    """
    if any("exit-code" in message for message in _reported(status_fd)):
        return None
    return _first_line(stderr) or f"{BINARY} reported no command that ran"


@contextmanager
def applied(command: list[str], isolation: Isolation) -> Iterator[ConfinedCommand]:
    """The command inside a namespace that lasts exactly as long as it runs.

    The empty directory every denial is bound from, and the descriptor
    bubblewrap reports through, both live for the length of this block: the
    profile is not a string the command carries, so it has to be held.
    """
    with tempfile.TemporaryDirectory(prefix="codeservo-denied-") as empty:
        rules = _rules(isolation, Path(empty))
        status_read, status_write = os.pipe()
        os.set_blocking(status_read, False)
        try:
            yield ConfinedCommand(
                command=[
                    BINARY,
                    "--dev-bind",
                    "/",
                    "/",
                    "--die-with-parent",
                    *rules,
                    "--json-status-fd",
                    str(status_write),
                    "--",
                    *command,
                ],
                pass_fds=(status_write,),
                fault=lambda _exit_code, stderr: fault(status_read, stderr),
            )
        finally:
            os.close(status_read)
            os.close(status_write)
