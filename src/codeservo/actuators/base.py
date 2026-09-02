from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

ACTUATOR_NAMES = ("claude", "codex")
DEFAULT_ACTUATOR = "claude"
ACTUATOR_ENV_VAR = "CODESERVO_ACTUATOR"


class ActuatorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Actuator:
    name: str
    version_command: tuple[str, ...]
    implement: Callable[..., dict]
    review: Callable[..., tuple[dict, dict]]
    describe_isolation: Callable[..., dict]


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
