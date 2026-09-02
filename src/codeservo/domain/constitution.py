from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Phase(StrEnum):
    """When a gate is measured: on every iteration, or once at the end."""

    QUICK = "quick"
    FULL = "full"


class ResultFormat(StrEnum):
    """What a gate answers with beside its exit code.

    `EXIT_CODE` is the verdict alone; `CODESERVO_JSON` adds a document
    saying what the gate saw.
    """

    EXIT_CODE = "exit-code"
    CODESERVO_JSON = "codeservo-json"


@dataclass(frozen=True)
class ExecutionEnvironment:
    """The resolved execution environment a run measures through.

    Every path is relative to the repository root, so the same declaration
    names the source repository during the baseline and the isolated checkout
    afterwards, and the record stays readable wherever it is copied.
    """

    provider: str
    manifest: str
    lock: str
    environment: str = "default"


@dataclass(frozen=True)
class Gate:
    name: str
    phase: Phase
    command: str | None = None
    task: str | None = None
    timeout_seconds: int = 300
    baseline: bool = True
    sensor: str | None = None
    # What this gate answers with beside its exit code. Independent of the
    # phase, of naming a command or a task, of the baseline, and of an
    # external sensor.
    result_format: ResultFormat = ResultFormat.EXIT_CODE


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
    execution: ExecutionEnvironment | None = None

    def gates_for(self, phase: Phase) -> tuple[Gate, ...]:
        return tuple(g for g in self.gates if g.phase == phase)
