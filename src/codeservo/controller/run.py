"""The control loop, from the frozen inputs to the decision.

Every step measures the repository and either lets the run continue or raises
the rejection that ends it. The record is closed in one place, whichever step
ended the run, so a decision can never be reached without being written.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.run import RunStatus
from ..workspace.git import is_clean
from .context import RunContext, RunRequest, prepare
from .errors import Escalation, Rejection
from .phases import (
    converge,
    create_candidate,
    freeze_execution_environment,
    measure_baseline,
    prepare_candidate_environment,
)
from .record import RunRecord

DEFAULT_MAX_ITERATIONS = 4
DEFAULT_AGENT_TIMEOUT_SECONDS = 1800


def run(
    *,
    repo_path: Path,
    task_path: Path,
    model: str,
    effort: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    review_model: str | None = None,
    agent_timeout_seconds: int = DEFAULT_AGENT_TIMEOUT_SECONDS,
    state_dir: Path | None = None,
    actuator: str | None = None,
    review_actuator: str | None = None,
    review_effort: str | None = None,
) -> dict:
    """Drive one controlled software change, and return the record it wrote.

    The implementer's model and effort are named by the caller; the reviewer's
    default to the same two, which is a resolution of the request and never a
    backend's own default. Whether each names a model its backend drives is
    settled before the run directory exists.
    """
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
            review_actuator=review_actuator,
            review_model=review_model if review_model is not None else model,
            review_effort=review_effort if review_effort is not None else effort,
        )
    )
    try:
        _verify_control_inputs(context)
        freeze_execution_environment(context, record)
        measure_baseline(context, record)
        create_candidate(context, record)
        prepare_candidate_environment(context, record)
        converge(context, record)
    except Rejection as rejection:
        return _close(context, record, RunStatus.REJECTED, rejection.reasons)
    except Escalation as escalation:
        return _close(context, record, RunStatus.ESCALATED, escalation.reasons)
    return _close(context, record, RunStatus.ACCEPTED, [])


def _verify_control_inputs(context: RunContext) -> None:
    """What must hold before a checkout or an agent process ever exists.

    A source repository holding uncommitted work ends the run here, because
    the base commit would then not describe the tree the baseline is about to
    measure.
    """
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
