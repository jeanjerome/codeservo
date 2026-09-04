"""The macOS mechanism: a seatbelt profile applied by `sandbox-exec`.

The profile is a deny list over `(allow default)`, so what it does not name
stays reachable. Two exit codes say the command never ran at all, and both
were measured rather than assumed: a profile `sandbox-exec` cannot parse ends
at `EX_DATAERR`, an executable it cannot start at `EX_OSERR`, and in both
cases it writes its own report on stderr and the command is never reached. A
command exiting either code itself writes nothing there, which is what
separates the two readings.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .confinement import ConfinedCommand
from .sandbox import Isolation, Mechanism

BINARY = "/usr/bin/sandbox-exec"
MECHANISM = Mechanism.MACOS_SANDBOX_EXEC
REPORT_PREFIX = "sandbox-exec: "
DID_NOT_RUN = (os.EX_DATAERR, os.EX_OSERR)


def unusable() -> str | None:
    """Why this host cannot apply a seatbelt profile, or nothing."""
    if sys.platform != "darwin":
        return "sandbox-exec is macOS only"
    if not Path(BINARY).is_file():
        return f"{BINARY} is missing"
    return None


def _escape(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')


def profile(isolation: Isolation) -> str:
    rules = "".join(
        f'(deny file-read* file-write* (subpath "{_escape(path)}"))'
        for path in isolation.denied
    )
    rules += "".join(
        f'(deny file-write* (subpath "{_escape(path)}"))'
        for path in isolation.read_only
    )
    return f"(version 1)(allow default){rules}"


def _first_line(stderr: Path | None) -> str:
    if stderr is None or not stderr.is_file():
        return ""
    with stderr.open("r", encoding="utf-8", errors="replace") as stream:
        return stream.readline().strip()


def fault(exit_code: int, stderr: Path | None) -> str | None:
    """What stopped the command from running under the profile, or nothing.

    This is the reading `applied` hands the caller. Both codes are ones the
    measured command could exit with itself, so the report `sandbox-exec`
    writes when it gives up is what separates the two cases.
    """
    if exit_code not in DID_NOT_RUN:
        return None
    reported = _first_line(stderr)
    return reported if reported.startswith(REPORT_PREFIX) else None


@contextmanager
def applied(command: list[str], isolation: Isolation) -> Iterator[ConfinedCommand]:
    """The command under a profile `sandbox-exec` carries on its own argv."""
    yield ConfinedCommand(
        command=[BINARY, "-p", profile(isolation), *command],
        pass_fds=(),
        fault=fault,
    )
