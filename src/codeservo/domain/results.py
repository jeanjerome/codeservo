"""What a measurement returns, whatever ran it."""

from __future__ import annotations

from dataclasses import dataclass, field


def succeeded(exit_code: int | None, timed_out: bool) -> bool:
    """Whether one command answered the way a passing measurement does.

    Said once, because a gate result carries the same two values as the
    command it was measured by and must not reach a different verdict from
    them.
    """
    return exit_code == 0 and not timed_out


@dataclass
class CommandResult:
    """One command the controller ran, and what it left behind."""

    name: str
    command: str
    exit_code: int | None
    duration_ms: int
    stdout_path: str
    stderr_path: str
    stdout_sha256: str
    stderr_sha256: str
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return succeeded(self.exit_code, self.timed_out)


@dataclass
class SensorResult:
    """One verdict a sensor reached, and what it saw to reach it."""

    name: str
    passed: bool
    summary: str
    details: dict = field(default_factory=dict)
