"""What each process of a run may reach, and what it may only read.

A run confines four kinds of process, and each one is described before it
starts. The actuator owns the candidate's files and nothing else about it.
A gate is handed the tree it measures and never the other one. The reviewer
reads the candidate and writes nowhere in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain.constitution import ExecutionEnvironment
from ..runtime.sandbox import Isolation, isolation_evidence
from ..workspace import pixi
from .document import GateIsolation

MECHANISM = "macos-sandbox-exec"


def protected_paths(
    tree: Path, execution: ExecutionEnvironment | None
) -> tuple[Path, ...]:
    """What a process may read but never write in the tree it works on.

    The Git metadata is the record of what the tree is, and the provider
    directory is the environment every measurement runs through: writing
    either one changes what a later reading reports without changing the
    files anyone declared. Both stay readable, because the controller, the
    gates and the actuator all read them. A constitution declaring no provider
    names no provider directory.
    """
    paths = [tree / ".git"]
    if execution is not None:
        paths.append(pixi.provider_directory(tree / execution.manifest))
    return tuple(paths)


@dataclass(frozen=True)
class Confinement:
    """The three profiles a run applies, and the one it derives from them."""

    actuator: Isolation
    source_gates: Isolation
    candidate_gates: Isolation
    candidate_protected: tuple[Path, ...]

    def reviewer(self, worktree: Path) -> Isolation:
        """The read-only reviewer's profile.

        The candidate's own protected paths lie inside a worktree the reviewer
        may not write at all, so naming them again says nothing more than the
        worktree already does.
        """
        return Isolation(
            denied=self.actuator.denied,
            read_only=(
                *(
                    path
                    for path in self.actuator.read_only
                    if not path.is_relative_to(worktree)
                ),
                worktree,
            ),
        )

    def gate_evidence(self) -> GateIsolation:
        return GateIsolation(
            source=isolation_evidence(self.source_gates, MECHANISM),
            candidate=isolation_evidence(self.candidate_gates, MECHANISM),
        )


def confinement(
    *,
    repo: Path,
    worktree: Path,
    run_dir: Path,
    state_root: Path,
    git_dir: Path,
    execution: ExecutionEnvironment | None,
) -> Confinement:
    """Build the profiles of one run.

    The actuator owns the candidate's files and nothing else about it: the
    checkout's Git metadata and the environment the controller installed into
    it stay readable, so every command it runs still works, and stay
    unwritable, so what a later measurement reads is what was actuated.

    Gates are controller-owned measurements: they read the frozen sensors and
    write nothing into the record they produce, and nothing into the metadata
    or the environment of the tree they measure.
    """
    candidate_protected = protected_paths(worktree, execution)
    return Confinement(
        actuator=Isolation(
            denied=(
                state_root / "runs",
                state_root / "sensors",
                state_root / ".git",
                git_dir,
            ),
            read_only=(repo, *candidate_protected),
        ),
        source_gates=Isolation(read_only=(run_dir, *protected_paths(repo, execution))),
        candidate_gates=Isolation(read_only=(run_dir, *candidate_protected)),
        candidate_protected=candidate_protected,
    )
