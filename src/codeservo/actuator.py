from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ACTUATOR_NAMES = ("claude", "codex")
DEFAULT_ACTUATOR = "claude"
ACTUATOR_ENV_VAR = "CODESERVO_ACTUATOR"


class ActuatorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Isolation:
    """Paths an actuator process must not reach, whatever the backend is."""

    denied: tuple[Path, ...] = ()
    read_only: tuple[Path, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.denied and not self.read_only


@dataclass(frozen=True)
class Actuator:
    name: str
    version_command: tuple[str, ...]
    implement: Callable[..., dict]
    review: Callable[..., tuple[dict, dict]]
    describe_isolation: Callable[[Isolation], dict]


def default_actuator_name() -> str:
    name = os.environ.get(ACTUATOR_ENV_VAR, "").strip() or DEFAULT_ACTUATOR
    if name not in ACTUATOR_NAMES:
        raise ActuatorError(
            f"{ACTUATOR_ENV_VAR}={name!r} is not one of {', '.join(ACTUATOR_NAMES)}"
        )
    return name


def load_actuator(name: str) -> Actuator:
    if name == "claude":
        from . import claude_code

        return Actuator(
            name="claude",
            version_command=("claude", "--version"),
            implement=claude_code.run_implementer,
            review=claude_code.run_reviewer,
            describe_isolation=claude_code.describe_isolation,
        )
    if name == "codex":
        from . import codex

        return Actuator(
            name="codex",
            version_command=("codex", "--version"),
            implement=codex.run_implementer,
            review=codex.run_reviewer,
            describe_isolation=codex.describe_isolation,
        )
    raise ActuatorError(f"unknown actuator: {name}")


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
        raise ActuatorError("mechanical actuator isolation requires macOS sandbox-exec")
    return ["/usr/bin/sandbox-exec", "-p", seatbelt_profile(isolation), *command]


def isolation_evidence(isolation: Isolation, mechanism: str) -> dict:
    return {
        "mechanism": mechanism,
        "denied_paths": [str(path.resolve()) for path in isolation.denied],
        "read_only_paths": [str(path.resolve()) for path in isolation.read_only],
        "user_config_ignored": True,
    }
