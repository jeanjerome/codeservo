"""The command line, and nothing about what each command does."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..actuators import ACTUATOR_ENV_VAR, DEFAULT_ACTUATOR, Backend, Speed
from ..actuators.inventory import DEFAULT_SPEED
from ..controller.run import DEFAULT_AGENT_TIMEOUT_SECONDS, DEFAULT_MAX_ITERATIONS

PROGRAM = "codeservo"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROGRAM)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="add a starter repository constitution")
    init.add_argument("repo", nargs="?", default=".")

    execute = sub.add_parser("run", help="run one controlled software change")
    execute.add_argument("--repo", default=".")
    execute.add_argument("--task", required=True)
    execute.add_argument(
        "--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS
    )
    execute.add_argument("--model")
    execute.add_argument("--review-model")
    execute.add_argument(
        "--effort",
        help=(
            "reasoning effort the implementer backend applies "
            "(levels are backend-specific; unset leaves the backend default)"
        ),
    )
    execute.add_argument(
        "--speed",
        choices=tuple(Speed),
        default=DEFAULT_SPEED,
        help=f"speed tier the implementer backend applies (default: {DEFAULT_SPEED})",
    )
    execute.add_argument(
        "--agent-timeout-seconds", type=int, default=DEFAULT_AGENT_TIMEOUT_SECONDS
    )
    execute.add_argument(
        "--actuator",
        choices=tuple(Backend),
        help=(
            "agent CLI proposing the change, and reviewing it unless "
            "--review-actuator names another one "
            f"(default: ${ACTUATOR_ENV_VAR}, else {DEFAULT_ACTUATOR})"
        ),
    )
    execute.add_argument(
        "--review-actuator",
        choices=tuple(Backend),
        help=(
            "agent CLI running the read-only review "
            "(default: the resolved --actuator)"
        ),
    )
    execute.add_argument(
        "--review-effort",
        help=(
            "reasoning effort the reviewer backend applies "
            "(levels are backend-specific; unset leaves the backend default)"
        ),
    )
    execute.add_argument(
        "--review-speed",
        choices=tuple(Speed),
        default=DEFAULT_SPEED,
        help=f"speed tier the reviewer backend applies (default: {DEFAULT_SPEED})",
    )
    execute.add_argument(
        "--state-dir",
        type=Path,
        help=(
            "store sensors, run evidence, and working trees outside the target "
            "repository"
        ),
    )

    landing = sub.add_parser(
        "land", help="apply an accepted run's patch to the repository it measured"
    )
    landing.add_argument("run_dir", type=Path)
    landing.add_argument(
        "--message", help="the subject of the integration commit (default names the run)"
    )
    landing.add_argument(
        "--json", action="store_true", help="write what landed as a document to stdout"
    )

    verify = sub.add_parser(
        "verify-run", help="verify one run directory against the record it holds"
    )
    verify.add_argument("run_dir", type=Path)
    verify.add_argument(
        "--json", action="store_true", help="write the report document to stdout"
    )

    models = sub.add_parser(
        "models", help="report the models a backend advertises on this machine"
    )
    models.add_argument(
        "--actuator",
        choices=tuple(Backend),
        help="report one backend instead of every known one",
    )
    models.add_argument("--model", help="report one model of the selected backend")
    models.add_argument(
        "--json", action="store_true", help="write the inventory document to stdout"
    )
    models.add_argument(
        "--state-dir",
        type=Path,
        help="store the inventory outside the target repository",
    )
    return parser
