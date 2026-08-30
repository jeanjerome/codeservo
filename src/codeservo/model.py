from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Phase = Literal["quick", "full"]


@dataclass(frozen=True)
class Gate:
    name: str
    phase: Phase
    command: str
    timeout_seconds: int = 300
    baseline: bool = True
    sensor: str | None = None


@dataclass(frozen=True)
class ScopePolicy:
    protected: tuple[str, ...] = (".codeservo/**",)
    max_changed_files: int = 30
    max_diff_lines: int = 1000


@dataclass(frozen=True)
class ReviewPolicy:
    blocking_severities: tuple[str, ...] = ("blocker", "major")


@dataclass(frozen=True)
class Constitution:
    path: Path
    raw_text: str
    scope: ScopePolicy
    gates: tuple[Gate, ...]
    review: ReviewPolicy

    def gates_for(self, phase: Phase) -> tuple[Gate, ...]:
        return tuple(g for g in self.gates if g.phase == phase)


@dataclass
class CommandResult:
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
    name: str
    passed: bool
    summary: str
    details: dict = field(default_factory=dict)
