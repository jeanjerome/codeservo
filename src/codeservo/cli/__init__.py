"""The command line entry point."""

from __future__ import annotations

from pathlib import Path

from .commands import control_change, init_repo, land_run, report_models, report_run
from .parser import build_parser

__all__ = ["build_parser", "main"]


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "init":
        raise SystemExit(init_repo(Path(args.repo).resolve()))
    if args.command == "verify-run":
        raise SystemExit(report_run(args.run_dir, args.json))
    if args.command == "land":
        raise SystemExit(land_run(args.run_dir, args.message, args.json))
    if args.command == "models":
        raise SystemExit(report_models(args.actuator, args.model, args.json))
    raise SystemExit(control_change(args))
