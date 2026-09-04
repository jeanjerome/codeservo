"""Which mechanism confines a process on this host, and whether it did.

A profile says what a process may not reach. The mechanism that enforces it
belongs to the host, not to the run: a target repository declares its execution
provider, and never its confinement, because a candidate that could name the
mechanism holding it would be negotiating its own cage.

A mechanism is established by asking it, never inferred from the platform name,
and a host with none refuses the run rather than executing it unconfined.

The exit code cannot say whether a profile was applied, because that code
belongs to the measured command: a mechanism that could not start the command
and a gate that legitimately failed can end on the same number. So each
adapter answers separately whether the command ran under the profile at all,
and a command that did not run is a fault that stops the run rather than a
verdict about the tree.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .sandbox import Isolation, Mechanism, SandboxError

# What stopped a command from running, or nothing. A mechanism reads it from
# the exit code and from what it wrote on stderr, both of which it owns.
Fault = Callable[[int, Path | None], str | None]


@dataclass(frozen=True)
class ConfinedCommand:
    """A command ready to run, and the reading that says whether it ran.

    `command` is what the caller starts and `pass_fds` what it must let
    through to it. `confirm` is called once the process has completed, and
    never after a timeout: a killed process leaves the same silence behind as
    a profile that was never applied, and the run already knows it timed out.
    """

    command: list[str]
    pass_fds: tuple[int, ...]
    fault: Fault

    def confirm(self, exit_code: int, stderr: Path | None = None) -> None:
        reported = self.fault(exit_code, stderr)
        if reported is not None:
            raise SandboxError(f"the command did not run confined: {reported}")


@dataclass(frozen=True)
class Confiner:
    """One mechanism, as the three things the controller asks of it."""

    mechanism: str
    unusable: Callable[[], str | None]
    applied: Callable[[list[str], Isolation], AbstractContextManager[ConfinedCommand]]


def _unconfined(_exit_code: int, _stderr: Path | None) -> str | None:
    """An empty profile applies no mechanism, so nothing can have failed."""
    return None


def load_confiner(mechanism: str) -> Confiner:
    if mechanism == Mechanism.MACOS_SANDBOX_EXEC:
        from . import seatbelt

        return Confiner(
            mechanism=seatbelt.MECHANISM,
            unusable=seatbelt.unusable,
            applied=seatbelt.applied,
        )
    if mechanism == Mechanism.LINUX_BUBBLEWRAP:
        from . import bubblewrap

        return Confiner(
            mechanism=bubblewrap.MECHANISM,
            unusable=bubblewrap.unusable,
            applied=bubblewrap.applied,
        )
    raise SandboxError(f"unknown confinement mechanism: {mechanism}")


# Tried in this order, and the first one this host can apply is the one it uses.
MECHANISMS: tuple[str, ...] = (
    Mechanism.MACOS_SANDBOX_EXEC,
    Mechanism.LINUX_BUBBLEWRAP,
)


@lru_cache(maxsize=1)
def host_confiner() -> Confiner:
    """The mechanism this host applies, or why it has none.

    Establishing this costs a reading per mechanism, so it is done once and
    kept. A test that changes what the host answers clears the cache.
    """
    refused = []
    for name in MECHANISMS:
        confiner = load_confiner(name)
        unusable = confiner.unusable()
        if unusable is None:
            return confiner
        refused.append(f"{name}: {unusable}")
    raise SandboxError(
        "no confinement mechanism on this host -- " + "; ".join(refused)
    )


def mechanism() -> str:
    """What a record names as the confinement of this host's processes."""
    return host_confiner().mechanism


@contextmanager
def confined(command: list[str], isolation: Isolation) -> Iterator[ConfinedCommand]:
    """The command under this host's mechanism, for as long as it runs.

    An empty profile confines nothing, so the command is left as it is and
    no mechanism is asked for: that is what lets a host with none still run
    the commands a run makes outside every profile.
    """
    if isolation.empty:
        yield ConfinedCommand(command=command, pass_fds=(), fault=_unconfined)
        return
    with host_confiner().applied(command, isolation) as application:
        yield application
