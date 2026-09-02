"""The contract a backend answers, and the loader that resolves one by name.

An actuator proposes a change and, in the other role, reviews one. Everything
specific to a command-line tool — its flags, its configuration keys, the
fields of its event stream — stays inside that tool's adapter. What crosses
this boundary is declared here, so a controller reading an actuation record
and an adapter writing one are held to the same shape.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..domain.document import Document
from ..runtime.sandbox import Isolation, IsolationEvidence
from .inventory import Backend, Speed

# What a run drives when nothing names a backend, and where it looks first.
DEFAULT_ACTUATOR = Backend.CLAUDE
ACTUATOR_ENV_VAR = "CODESERVO_ACTUATOR"


class ActuatorError(RuntimeError):
    pass


@dataclass(frozen=True, kw_only=True)
class ObservedProfile(Document):
    """What a backend reported about the profile it applied to itself.

    A field the backend did not name stays empty, which is why every one of
    them starts that way. Nothing is filled in from the request, so an
    absence here is an absence in the record.
    """

    model: str | None = None
    effort: str | None = None
    speed: str | None = None


class ReportedProfile(Protocol):
    """What a backend reported about the call it just made.

    Both roles report the same two things: the configuration the command
    actually carried, and what the session then said about itself. An adapter
    records more than the controller reads, so what crosses the boundary is
    stated as what is read and never as the whole of what was written.
    """

    @property
    def native(self) -> dict[str, Any]: ...
    @property
    def observed(self) -> ObservedProfile: ...


class Actuation(ReportedProfile, Protocol):
    """What every backend's actuation carries for the control loop.

    An adapter records more than this — the command it built, the streams it
    kept, what the session billed. These are what the controller reads, so
    they are what a backend is held to whichever tool answered.
    """

    @property
    def exit_code(self) -> int: ...
    @property
    def result_sha256(self) -> str: ...


class ReviewMeta(ReportedProfile, Protocol):
    """What every backend's review carries for the control loop."""

    @property
    def meta_sha256(self) -> str: ...


class Implement(Protocol):
    """Propose a change inside the candidate, under the given confinement."""

    def __call__(
        self,
        *,
        worktree: Path,
        prompt: str,
        out_dir: Path,
        model: str | None,
        timeout_seconds: int,
        isolation: Isolation,
        effort: str | None,
        speed: Speed,
    ) -> Actuation: ...


class Review(Protocol):
    """Read the candidate and answer against the frozen review schema."""

    def __call__(
        self,
        *,
        worktree: Path,
        prompt: str,
        schema_path: Path,
        out_dir: Path,
        model: str | None,
        timeout_seconds: int,
        isolation: Isolation,
        effort: str | None,
        speed: Speed,
    ) -> tuple[dict[str, Any], ReviewMeta]: ...


class DescribeIsolation(Protocol):
    """State the confinement this backend applies, before it applies it."""

    def __call__(self, isolation: Isolation) -> IsolationEvidence: ...


@dataclass(frozen=True)
class Actuator:
    name: Backend
    version_command: tuple[str, ...]
    implement: Implement
    review: Review
    describe_isolation: DescribeIsolation


def default_actuator_name() -> Backend:
    requested = os.environ.get(ACTUATOR_ENV_VAR, "").strip() or DEFAULT_ACTUATOR
    try:
        return Backend(requested)
    except ValueError:
        known = ", ".join(Backend)
        raise ActuatorError(
            f"{ACTUATOR_ENV_VAR}={requested!r} is not one of {known}"
        ) from None


def load_actuator(name: str) -> Actuator:
    if name == Backend.CLAUDE:
        from . import claude_code

        return Actuator(
            name=Backend.CLAUDE,
            version_command=("claude", "--version"),
            implement=claude_code.run_implementer,
            review=claude_code.run_reviewer,
            describe_isolation=claude_code.describe_isolation,
        )
    if name == Backend.CODEX:
        from . import codex

        return Actuator(
            name=Backend.CODEX,
            version_command=("codex", "--version"),
            implement=codex.run_implementer,
            review=codex.run_reviewer,
            describe_isolation=codex.describe_isolation,
        )
    raise ActuatorError(f"unknown actuator: {name}")
