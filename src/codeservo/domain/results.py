"""What a measurement returns, whatever ran it."""

from __future__ import annotations

from dataclasses import dataclass, field


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
        return self.exit_code == 0 and not self.timed_out


@dataclass
class SensorResult:
    """One verdict a sensor reached, and what it saw to reach it."""

    name: str
    passed: bool
    summary: str
    details: dict = field(default_factory=dict)
