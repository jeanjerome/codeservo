"""What this host provides, and what a run would find missing.

A run refuses to start where no mechanism can confine a process, where the
agent CLI it names is not installed, or where it would measure through a
provider that is not there — and it refuses at the point where it finds out,
after a run directory exists. This says the same things before anything is
frozen, and says what would answer each one.

Every reading is taken by asking rather than by inferring: the mechanism by
applying a profile, a tool by running it, a repository by reading what it
declares.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..actuators import Backend, load_actuator
from ..controller.context import DEFAULT_STATE_DIRECTORY
from ..policies.constitution import ConstitutionError, load_constitution
from ..runtime.confinement import host_confiner
from ..runtime.sandbox import SandboxError
from ..workspace.provider import ProviderError, load_provider

REQUIRED_PYTHON = (3, 12)
PROBE_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Reading:
    """One thing a run reaches for, what this host answered, and what would fix it.

    `required` says whether a run cannot start without it. A reading that is
    not required is reported the same way and decides nothing: an actuator
    that is absent is a choice narrowed, not a host that cannot run.
    """

    subject: str
    answer: str
    holds: bool
    required: bool = True
    remedy: str = ""


def _answered(command: tuple[str, ...]) -> str | None:
    """The first line a tool answers, or nothing when it does not answer."""
    if shutil.which(command[0]) is None:
        return None
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            check=False,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    said = (completed.stdout or completed.stderr).strip().splitlines()
    return said[0] if said else ""


def _python() -> Reading:
    running = ".".join(str(part) for part in sys.version_info[:3])
    return Reading(
        subject="python",
        answer=running,
        holds=sys.version_info[:2] >= REQUIRED_PYTHON,
        remedy="run CodeServo on Python "
        + ".".join(str(part) for part in REQUIRED_PYTHON)
        + " or later",
    )


def _git() -> Reading:
    said = _answered(("git", "--version"))
    return Reading(
        subject="git",
        answer=said or "not installed",
        holds=said is not None,
        remedy="install Git: every tree a run works on is a checkout",
    )


def _confinement() -> Reading:
    """The mechanism this host applies, established by applying one."""
    try:
        mechanism = host_confiner().mechanism
    except SandboxError as refused:
        return Reading(
            subject="confinement",
            answer=str(refused),
            holds=False,
            remedy=(
                "install bubblewrap and allow unprivileged user namespaces"
                " (kernel.apparmor_restrict_unprivileged_userns is 1 on"
                " Ubuntu 24.04, and its bubblewrap package installs no"
                " AppArmor profile of its own)"
                if sys.platform == "linux"
                else "a run executes only where a profile can be applied"
            ),
        )
    return Reading(subject="confinement", answer=mechanism, holds=True)


def _actuators() -> list[Reading]:
    """What each backend answers, and whether any of them answers at all.

    A version is what a CLI being installed looks like. It says nothing about
    a session being authenticated, which only a call would establish and which
    this command does not make.
    """
    readings = []
    answered = []
    for backend in Backend:
        said = _answered(load_actuator(backend).version_command)
        if said is not None:
            answered.append(backend)
        readings.append(
            Reading(
                subject=f"actuator {backend}",
                answer=said or "not installed",
                holds=said is not None,
                required=False,
                remedy=f"install the {backend} CLI to drive a run with it",
            )
        )
    readings.append(
        Reading(
            subject="an actuator answers",
            answer=", ".join(answered) if answered else "none",
            holds=bool(answered),
            remedy="a run needs one of the agent CLIs above, authenticated",
        )
    )
    return readings


def _state_directory(state_dir: Path | None) -> Reading:
    root = (
        state_dir.resolve()
        if state_dir is not None
        else (Path.home() / DEFAULT_STATE_DIRECTORY).resolve()
    )
    existing = root
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    writable = os.access(existing, os.W_OK)
    return Reading(
        subject="state directory",
        answer=f"{root} ({'writable' if writable else 'not writable'})",
        holds=writable,
        remedy=f"a run writes its sensors, worktrees and records under {root}",
    )


def _repository(repo: Path) -> list[Reading]:
    """What the target repository declares, and whether a run could start on it."""
    inside = _answered(("git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"))
    if inside != "true":
        return [
            Reading(
                subject="repository",
                answer=f"{repo} is not a Git work tree",
                holds=False,
                remedy="a run measures a checkout and names the commit it started from",
            )
        ]
    readings = [Reading(subject="repository", answer=str(repo), holds=True)]

    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    readings.append(
        Reading(
            subject="clean tree",
            answer="clean" if not dirty else f"{len(dirty)} uncommitted paths",
            holds=not dirty,
            remedy="commit or stash: what is uncommitted is not what a run measures",
        )
    )

    try:
        constitution = load_constitution(repo)
    except ConstitutionError as refused:
        readings.append(
            Reading(
                subject="constitution",
                answer=str(refused),
                holds=False,
                remedy="write .codeservo/constitution.toml, or run codeservo init",
            )
        )
        return readings
    gates = ", ".join(gate.name for gate in constitution.gates) or "none"
    readings.append(
        Reading(
            subject="constitution",
            answer=f"{len(constitution.gates)} gates: {gates}",
            holds=bool(constitution.gates),
            remedy="a constitution declaring no gate lets everything through",
        )
    )

    execution = constitution.execution
    if execution is not None:
        try:
            provider = load_provider(execution.provider, Path(repo))
        except ProviderError as refused:
            readings.append(
                Reading(
                    subject="provider",
                    answer=str(refused),
                    holds=False,
                    remedy="declare a provider this controller has an adapter for",
                )
            )
        else:
            installed = shutil.which(provider.name) is not None
            readings.append(
                Reading(
                    subject="provider",
                    answer=f"{provider.name}"
                    + ("" if installed else " is not installed"),
                    holds=installed,
                    remedy=f"install {provider.name}: every gate measures through it",
                )
            )
    return readings


def readings(repo: Path | None = None, state_dir: Path | None = None) -> list[Reading]:
    """Everything this host answers, in the order a run reaches for it."""
    found = [_python(), _git(), _confinement()]
    found += _actuators()
    found.append(_state_directory(state_dir))
    if repo is not None:
        found += _repository(repo.resolve())
    return found
