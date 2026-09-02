"""What ran a run: the controller, the two backends, and the host tooling.

Every value here is read from the tool that answers it. A command that fails
reports a diagnostic and not the value asked for, so it is recorded as
unavailable rather than guessed at.
"""

from __future__ import annotations

import platform
import subprocess

from .. import __version__
from ..actuators import Actuator
from ..resources import SOURCE_ROOT
from .document import RuntimeMetadata

UNAVAILABLE = "unavailable"


def command_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return UNAVAILABLE
    # A command that failed reports a diagnostic, not the value asked for.
    if completed.returncode != 0:
        return UNAVAILABLE
    output = completed.stdout.strip() or completed.stderr.strip()
    return output.splitlines()[0] if output else UNAVAILABLE


def runtime_metadata(
    actuator: Actuator,
    reviewer: Actuator,
    model: str | None,
    review_model: str | None,
) -> RuntimeMetadata:
    """Name the two backends a run drives, and the CLI each one answered with.

    Both roles are named even when a single backend serves them, so a record
    never leaves the reviewing backend to be inferred from the implementing one.
    """
    actuator_version = command_version(list(actuator.version_command))
    return {
        "codeservo_version": __version__,
        "codeservo_commit": command_version(
            ["git", "-C", str(SOURCE_ROOT), "rev-parse", "HEAD"]
        ),
        "actuator": actuator.name,
        "actuator_version": actuator_version,
        "review_actuator": reviewer.name,
        "review_actuator_version": (
            actuator_version
            if reviewer.version_command == actuator.version_command
            else command_version(list(reviewer.version_command))
        ),
        "implementer_model": model or f"{actuator.name}-default",
        "reviewer_model": review_model or f"{reviewer.name}-default",
        "python_version": platform.python_version(),
        "git_version": command_version(["git", "--version"]),
    }
