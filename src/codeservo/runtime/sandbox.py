"""The profile a confined process runs under, and what a record says of it.

Nothing here names a mechanism. A profile states what a process may not reach
and what it may only read; which mechanism enforces that is the host's answer,
given in `confinement`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..domain.document import Document


class SandboxError(RuntimeError):
    pass


class Mechanism(StrEnum):
    """What a record names as the confinement one process ran under.

    The first two are controller-owned profiles, applied by whichever
    mechanism the host carries and enforced whatever the confined process asks
    for. The third is what an actuator applies to itself when the controller
    applies nothing, and it is the backend's own sandbox rather than a profile
    of this package. A mechanism is named here when an adapter produces it, so
    this is the whole of what a record can hold.
    """

    MACOS_SANDBOX_EXEC = "macos-sandbox-exec"
    LINUX_BUBBLEWRAP = "linux-bubblewrap"
    CODEX_WORKSPACE_WRITE = "codex-workspace-write"


@dataclass(frozen=True, kw_only=True)
class IsolationEvidence(Document):
    """The confinement one process ran under, as the record states it."""

    mechanism: str
    denied_paths: tuple[str, ...]
    read_only_paths: tuple[str, ...]
    user_config_ignored: bool


@dataclass(frozen=True)
class Isolation:
    """Paths a confined process must not reach."""

    denied: tuple[Path, ...] = ()
    read_only: tuple[Path, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.denied and not self.read_only


def isolation_evidence(
    isolation: Isolation, mechanism: str
) -> IsolationEvidence:
    return IsolationEvidence(
        mechanism=mechanism,
        denied_paths=tuple(str(path.resolve()) for path in isolation.denied),
        read_only_paths=tuple(str(path.resolve()) for path in isolation.read_only),
        user_config_ignored=True,
    )
