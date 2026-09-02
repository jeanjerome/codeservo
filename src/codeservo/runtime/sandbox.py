from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


class SandboxError(RuntimeError):
    pass


class IsolationEvidence(TypedDict):
    """The confinement one process ran under, as the record states it."""

    mechanism: str
    denied_paths: list[str]
    read_only_paths: list[str]
    user_config_ignored: bool


@dataclass(frozen=True)
class Isolation:
    """Paths a confined process must not reach."""

    denied: tuple[Path, ...] = ()
    read_only: tuple[Path, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.denied and not self.read_only


def _escape(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')


def seatbelt_profile(isolation: Isolation) -> str:
    rules = "".join(
        f'(deny file-read* file-write* (subpath "{_escape(path)}"))'
        for path in isolation.denied
    )
    rules += "".join(
        f'(deny file-write* (subpath "{_escape(path)}"))'
        for path in isolation.read_only
    )
    return f"(version 1)(allow default){rules}"


def seatbelt_command(command: list[str], isolation: Isolation) -> list[str]:
    """Confine a command with a macOS seatbelt profile enforcing isolation."""
    if isolation.empty:
        return command
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise SandboxError("mechanical isolation requires macOS sandbox-exec")
    return ["/usr/bin/sandbox-exec", "-p", seatbelt_profile(isolation), *command]


def isolation_evidence(
    isolation: Isolation, mechanism: str
) -> IsolationEvidence:
    return {
        "mechanism": mechanism,
        "denied_paths": [str(path.resolve()) for path in isolation.denied],
        "read_only_paths": [str(path.resolve()) for path in isolation.read_only],
        "user_config_ignored": True,
    }
