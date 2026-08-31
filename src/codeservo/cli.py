from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .actuator import ACTUATOR_ENV_VAR, ACTUATOR_NAMES, DEFAULT_ACTUATOR
from .config import ConstitutionError
from .controller import run
from .models import (
    ModelSelectionError,
    build_inventory,
    render_document,
    render_listing,
    write_inventory,
)
from .task import TaskError


def _init_repo(repo: Path) -> int:
    target = repo / ".codeservo" / "constitution.toml"
    if target.exists():
        print(f"already exists: {target}", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).with_name("constitution.example.toml")
    shutil.copyfile(template, target)
    print(target)
    return 0


def _models(
    actuator: str | None,
    model: str | None,
    as_json: bool,
    state_dir: Path | None,
) -> int:
    """Report the models a backend advertises locally.

    The report reads provider caches and nothing else: no agent starts, no
    cache is refreshed, and an unreadable cache leaves the exit status at 0.
    """
    try:
        document = build_inventory(actuator=actuator, model=model)
    except ModelSelectionError as exc:
        print(f"codeservo: {exc}", file=sys.stderr)
        return 2

    text = render_document(document)
    try:
        path = write_inventory(state_dir, text)
    except OSError as exc:
        print(f"codeservo: cannot write the inventory: {exc}", file=sys.stderr)
        return 2

    if as_json:
        sys.stdout.write(text)
    else:
        sys.stdout.write(render_listing(document))
        print(f"inventory: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codeservo")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="add a starter repository constitution")
    init.add_argument("repo", nargs="?", default=".")

    execute = sub.add_parser("run", help="run one controlled software change")
    execute.add_argument("--repo", default=".")
    execute.add_argument("--task", required=True)
    execute.add_argument("--max-iterations", type=int, default=4)
    execute.add_argument("--model")
    execute.add_argument("--review-model")
    execute.add_argument("--agent-timeout-seconds", type=int, default=1800)
    execute.add_argument(
        "--actuator",
        choices=ACTUATOR_NAMES,
        help=(
            "agent CLI proposing and reviewing the change "
            f"(default: ${ACTUATOR_ENV_VAR}, else {DEFAULT_ACTUATOR})"
        ),
    )
    execute.add_argument(
        "--state-dir",
        type=Path,
        help=(
            "store sensors, run evidence, and working trees outside the target "
            "repository"
        ),
    )

    models = sub.add_parser(
        "models", help="report the models a backend advertises on this machine"
    )
    models.add_argument(
        "--actuator",
        choices=ACTUATOR_NAMES,
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


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "init":
        raise SystemExit(_init_repo(Path(args.repo).resolve()))
    if args.command == "models":
        raise SystemExit(
            _models(args.actuator, args.model, args.json, args.state_dir)
        )

    try:
        result = run(
            repo_path=Path(args.repo),
            task_path=Path(args.task),
            max_iterations=args.max_iterations,
            model=args.model,
            review_model=args.review_model,
            agent_timeout_seconds=args.agent_timeout_seconds,
            state_dir=args.state_dir,
            actuator=args.actuator,
        )
    except (ConstitutionError, TaskError, RuntimeError, ValueError) as exc:
        print(f"codeservo: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["status"] == "ACCEPTED" else 1)


if __name__ == "__main__":
    main()
