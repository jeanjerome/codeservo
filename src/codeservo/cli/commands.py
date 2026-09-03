"""What each command does, and the exit status it reports it with."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from ..actuators.catalogue import (
    Backend,
    Catalogue,
    CatalogueError,
    Model,
    load_catalogue,
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


def _catalogue_document(catalogue: Catalogue, models: list[Model]) -> dict:
    return {
        "version": catalogue.version,
        "priced_at": catalogue.priced_at,
        "basis": catalogue.basis,
        "models": [
            {
                "backend": model.backend,
                "id": model.id,
                "positioning": model.positioning,
                "source": model.source,
                "price_per_million_tokens": (
                    None
                    if model.price is None
                    else {
                        "input": model.price.input,
                        "cached_input": model.price.cached_input,
                        "cache_write": dict(model.price.cache_write),
                        "output": model.price.output,
                    }
                ),
            }
            for model in models
        ],
    }


def _model_line(model: Model) -> str:
    if model.price is None:
        priced = "unpriced"
    else:
        writes = ", ".join(
            f"{duration} {price:g}" for duration, price in model.price.cache_write.items()
        )
        priced = (
            f"input {model.price.input:g}  cached {model.price.cached_input:g}"
            f"  cache write {writes}  output {model.price.output:g}"
        )
    return f"  {model.backend}  {model.id}  {priced}  {model.positioning}"


def report_models(actuator: str | None, model: str | None, as_json: bool) -> int:
    """List the models a run may request, from the catalogue and nothing else.

    No agent starts and no provider cache is read: what a run may name is
    what this package publishes, at the prices it was read with.
    """
    try:
        catalogue = load_catalogue()
        models = list(catalogue.models)
        if actuator is not None:
            models = list(catalogue.models_for(Backend(actuator)))
        if model is not None:
            if actuator is None:
                raise CatalogueError("--model names one backend's model, so it requires --actuator")
            models = [catalogue.lookup(Backend(actuator), model)]
    except (CatalogueError, ValueError) as exc:
        print(f"codeservo: {exc}", file=sys.stderr)
        return USAGE_ERROR

    if as_json:
        print(json.dumps(_catalogue_document(catalogue, models), indent=2, sort_keys=True))
        return 0
    print(
        f"catalogue version {catalogue.version}, {catalogue.basis},"
        f" priced {catalogue.priced_at or 'undated'}, USD per million tokens"
    )
    for entry in models:
        print(_model_line(entry))
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
            model=args.model,
            effort=args.effort,
            max_iterations=args.max_iterations,
            review_model=args.review_model,
            agent_timeout_seconds=args.agent_timeout_seconds,
            state_dir=args.state_dir,
            actuator=args.actuator,
            review_actuator=args.review_actuator,
            review_effort=args.review_effort,
        )
    except (ConstitutionError, TaskError, RuntimeError, ValueError) as exc:
        print(f"codeservo: {exc}", file=sys.stderr)
        raise SystemExit(USAGE_ERROR) from exc

    print(json.dumps(result, indent=2, sort_keys=True))
    return RUN_EXIT_STATUS[RunStatus(result["status"])]
