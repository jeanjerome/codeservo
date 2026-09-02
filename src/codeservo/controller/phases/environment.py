"""Freezing the execution environment, then installing it into the candidate.

The environment is a control input, so it is frozen and resolved before
anything is measured: a lockfile that disagrees with the manifest, an
environment that does not exist, or a task no environment declares ends the
run there, with no checkout and no gate ever running.
"""

from __future__ import annotations

from pathlib import Path

from ...sensors.gates import baseline_gates
from ...workspace import pixi
from ..context import RunContext
from ..environment import frozen_environment, install_candidate, resolved_environment
from ..errors import ControlFailure, Rejection
from ..record import RunRecord


def freeze_execution_environment(context: RunContext, record: RunRecord) -> None:
    execution = context.execution
    if execution is None:
        return

    declared_tasks = tuple(
        gate.task for gate in context.constitution.gates if gate.task is not None
    )
    try:
        record["environment"] = frozen_environment(
            context.repo, context.base_commit, execution
        )
        record.persist()
        resolved, source_prefix = resolved_environment(
            context.repo, context.run_dir, execution, declared_tasks
        )
        record["environment"].update(resolved)
    except (ControlFailure, pixi.ProviderError) as exc:
        record.record(
            "environment.validated",
            {
                "provider": execution.provider,
                "environment": execution.environment,
                "passed": False,
            },
        )
        raise Rejection(str(exc)) from exc

    record.record(
        "environment.validated",
        {
            "provider": execution.provider,
            "environment": execution.environment,
            "passed": True,
        },
    )
    record.persist()

    # The source repository is the operator's tree: the controller prepares
    # the candidate and never this one, and writes nothing here. A baseline
    # gate that measures through the provider therefore needs an environment
    # that is already installed, and the run says so rather than creating one.
    measured_at_source = any(
        gate.task is not None for gate in baseline_gates(context.constitution)
    )
    if measured_at_source and not Path(source_prefix).is_dir():
        raise Rejection(
            "execution environment: environment"
            f" {execution.environment} is not installed in"
            f" the source repository: {source_prefix} does not exist"
        )


def prepare_candidate_environment(context: RunContext, record: RunRecord) -> None:
    """Install the declared environment into the isolated checkout.

    The candidate is prepared once the checkout exists and before anything
    actuates in it, so the first measurement already runs on the environment
    the lockfile pins instead of on whatever the host happens to offer.
    """
    execution = context.execution
    if execution is None:
        return

    name = execution.environment
    try:
        candidate, diagnostic = install_candidate(context.worktree, execution)
    except pixi.ProviderError as exc:
        record.record("environment.prepared", {"environment": name, "exit_code": None})
        raise Rejection(str(exc)) from exc

    record["environment"]["candidate"] = candidate
    record.record(
        "environment.prepared",
        {"environment": name, "exit_code": candidate["exit_code"]},
    )
    record.persist()

    if candidate["exit_code"] != 0:
        raise Rejection(
            f"execution environment: installing {name} into"
            f" the candidate failed: {diagnostic}"
        )
    if not Path(candidate["prefix_path"]).is_dir():
        raise Rejection(
            f"execution environment: installing {name}"
            f" created no environment at {candidate['prefix_path']}"
        )
