"""What each command does, and the exit status it reports it with."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from ..actuators.inventory import (
    ModelSelectionError,
    Speed,
    build_inventory,
    render_document,
    render_listing,
    write_inventory,
)
from ..controller import run
from ..controller.landing import LandingError, land
from ..domain.run import RunStatus
from ..domain.task import TaskError
from ..evidence.verify import (
    Verdict,
    VerificationError,
    render_report,
    verify_run,
)
from ..policies.constitution import ConstitutionError
from ..resources import constitution_example

# What the verification of a run directory reports through the exit status.
VERIFY_EXIT_STATUS = {
    Verdict.VALID: 0,
    Verdict.INVALID: 1,
    Verdict.INCOMPLETE: 2,
}

USAGE_ERROR = 2
UNREADABLE_RUN = 3

# What one controlled change reports through the exit status. An escalated run
# and a usage error share a status, and honestly so: in both nothing was
# decided, and a person reads what the controller printed.
RUN_EXIT_STATUS = {
    RunStatus.ACCEPTED: 0,
    RunStatus.REJECTED: 1,
    RunStatus.ESCALATED: 2,
}


def init_repo(repo: Path) -> int:
    target = repo / ".codeservo" / "constitution.toml"
    if target.exists():
        print(f"already exists: {target}", file=sys.stderr)
        return USAGE_ERROR
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(constitution_example(), target)
    print(target)
    return 0


def report_models(
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
        return USAGE_ERROR

    text = render_document(document)
    try:
        path = write_inventory(state_dir, text)
    except OSError as exc:
        print(f"codeservo: cannot write the inventory: {exc}", file=sys.stderr)
        return USAGE_ERROR

    if as_json:
        sys.stdout.write(text)
    else:
        sys.stdout.write(render_listing(document))
        print(f"inventory: {path}")
    return 0


def report_run(run_dir: Path, as_json: bool) -> int:
    """Report whether one run directory still holds what its record states.

    The verification reads: it creates nothing, changes nothing, and never
    rewrites the status the run recorded.
    """
    try:
        report = verify_run(run_dir)
    except VerificationError as exc:
        print(f"codeservo: {exc}", file=sys.stderr)
        return UNREADABLE_RUN

    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_report(report))
    return VERIFY_EXIT_STATUS[report["status"]]


def land_run(run_dir: Path, message: str | None, as_json: bool) -> int:
    """Land one accepted run, and report the commit it became."""
    try:
        landed = land(run_dir, message)
    except (LandingError, VerificationError) as exc:
        print(f"codeservo: {exc}", file=sys.stderr)
        return USAGE_ERROR
    document = {
        "run_id": landed.run_id,
        "repo": str(landed.repo),
        "commit": landed.commit,
        "register": str(landed.register),
        "findings": landed.findings,
    }
    if as_json:
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        print(f"landed run {landed.run_id} as {landed.commit} in {landed.repo}")
        print(f"{landed.findings} finding(s) registered in {landed.register}")
    return 0


def control_change(args) -> int:
    """Drive one controlled change, and report its decision."""
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
            effort=args.effort,
            speed=Speed(args.speed),
            review_actuator=args.review_actuator,
            review_effort=args.review_effort,
            review_speed=Speed(args.review_speed),
        )
    except (ConstitutionError, TaskError, RuntimeError, ValueError) as exc:
        print(f"codeservo: {exc}", file=sys.stderr)
        raise SystemExit(USAGE_ERROR) from exc

    print(json.dumps(result, indent=2, sort_keys=True))
    return RUN_EXIT_STATUS[RunStatus(result["status"])]
