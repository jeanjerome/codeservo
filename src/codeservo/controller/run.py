"""The control loop, from the frozen inputs to the decision.

Every step measures the repository and either lets the run continue or raises
the rejection that ends it. The record is closed in one place, whichever step
ended the run, so a decision can never be reached without being written.
"""

from __future__ import annotations

from pathlib import Path

from ..actuators.inventory import DEFAULT_SPEED, Speed
from ..domain.run import RunStatus
from ..workspace.git import is_clean
from .context import RunContext, RunRequest, prepare
from .errors import Rejection
from .inference import contradicted_profiles
from .phases import (
    converge,
    create_candidate,
    freeze_execution_environment,
    measure_baseline,
    measure_full,
    prepare_candidate_environment,
    review_candidate,
)
from .record import RunRecord

DEFAULT_MAX_ITERATIONS = 4
DEFAULT_AGENT_TIMEOUT_SECONDS = 1800


def run(
    *,
    repo_path: Path,
    task_path: Path,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    model: str | None = None,
    review_model: str | None = None,
    agent_timeout_seconds: int = DEFAULT_AGENT_TIMEOUT_SECONDS,
    state_dir: Path | None = None,
    actuator: str | None = None,
    effort: str | None = None,
    speed: Speed = DEFAULT_SPEED,
    review_actuator: str | None = None,
    review_effort: str | None = None,
    review_speed: Speed = DEFAULT_SPEED,
) -> dict:
    """Drive one controlled software change, and return the record it wrote."""
    context, record = prepare(
        RunRequest(
            repo_path=repo_path,
            task_path=task_path,
            max_iterations=max_iterations,
            agent_timeout_seconds=agent_timeout_seconds,
            state_dir=state_dir,
            actuator=actuator,
            model=model,
            effort=effort,
            speed=speed,
            review_actuator=review_actuator,
            review_model=review_model,
            review_effort=review_effort,
            review_speed=review_speed,
        )
    )
    try:
        _verify_control_inputs(context)
        freeze_execution_environment(context, record)
        measure_baseline(context, record)
        create_candidate(context, record)
        prepare_candidate_environment(context, record)
        accepted = converge(context, record)
        full = measure_full(context, record, accepted)
        reasons = review_candidate(context, record, accepted, full)
    except Rejection as rejection:
        return _close(context, record, RunStatus.REJECTED, rejection.reasons)
    return _close(
        context,
        record,
        RunStatus.ACCEPTED if not reasons else RunStatus.REJECTED,
        reasons,
    )


def _verify_control_inputs(context: RunContext) -> None:
    """What must hold before a checkout or an agent process ever exists.

    Each profile is a control input, so a request the inventory of its own
    backend contradicts ends the run here. So does a source repository holding
    uncommitted work, because the base commit would then not describe the tree
    the baseline is about to measure.
    """
    contradicted = contradicted_profiles(context.inference)
    if contradicted:
        raise Rejection(contradicted)
    if not is_clean(context.repo):
        raise Rejection("source repository is not clean")


def _close(
    context: RunContext, record: RunRecord, status: RunStatus, reasons: list[str]
) -> dict:
    return record.close(
        status,
        reasons,
        worktree=context.worktree,
        base_commit=context.base_commit,
    )
