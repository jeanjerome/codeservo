"""Freezing the execution environment, then installing it.

The environment is a control input, so it is frozen and resolved before
anything is measured: a lockfile that disagrees with the manifest, an
environment that does not exist, or a task no environment declares ends the
run there, with no checkout and no gate ever running. Where the tools are
installed depends on the provider: into the candidate after the checkout, for
one that keeps them in the tree it measures, or into the controller's own
directory before the baseline, for one that keeps them outside every tree.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ...sensors.gates import baseline_gates
from ...workspace.provider import Provider, ProviderError
from ..context import RunContext
from ..document import CandidateEnvironment
from ..environment import (
    candidate_digests,
    frozen_environment,
    install_candidate,
    resolved_environment,
)
from ..errors import ControlFailure, Rejection
from ..record import RunRecord


def freeze_execution_environment(context: RunContext, record: RunRecord) -> None:
    execution = context.execution
    provider = context.provider
    if execution is None or provider is None:
        return

    declared_tasks = tuple(
        gate.task for gate in context.constitution.gates if gate.task is not None
    )
    try:
        record.document = replace(
            record.document,
            environment=frozen_environment(
                context.repo, context.base_commit, execution
            ),
        )
        record.persist()
        resolved, source_prefix = resolved_environment(
            context.repo, context.run_dir, execution, declared_tasks, provider
        )
        record.document = replace(
            record.document,
            environment=record.document.environment.resolving(resolved),
        )
    except (ControlFailure, ProviderError) as exc:
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

    if provider.shared_installs:
        # The tools live in the controller's own directory, outside both
        # trees, so they are installed once, here, before the baseline
        # measures through them. The source tree is only read.
        _install(context, record, context.repo, provider)
        return

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
    """Prepare the isolated checkout to be measured through the environment.

    The candidate is prepared once the checkout exists and before anything
    actuates in it, so the first measurement already runs on the environment
    the lockfile pins instead of on whatever the host happens to offer. A
    provider that installed before the baseline installs nothing more here;
    the candidate's own provider files are digested now that they exist,
    because they are what every later recomputation is compared to.
    """
    execution = context.execution
    provider = context.provider
    if execution is None or provider is None:
        return

    if provider.shared_installs:
        installed = record.document.environment.candidate
        if not isinstance(installed, CandidateEnvironment):
            raise ControlFailure("the toolchain was never installed before the baseline")
        digests = candidate_digests(context.worktree, execution, provider)
        record.document = replace(
            record.document,
            environment=replace(
                record.document.environment,
                candidate=replace(
                    installed,
                    manifest_sha256=digests.manifest_sha256,
                    lock_sha256=digests.lock_sha256,
                    config_sha256=digests.config_sha256,
                ),
            ),
        )
        record.persist()
        return

    _install(context, record, context.worktree, provider)


def _install(
    context: RunContext, record: RunRecord, tree: Path, provider: Provider
) -> None:
    """Install the declared environment, and state what the installation did."""
    execution = context.execution
    if execution is None:
        return
    name = execution.environment
    try:
        candidate, diagnostic = install_candidate(tree, execution, provider)
    except ProviderError as exc:
        record.record("environment.prepared", {"environment": name, "exit_code": None})
        raise Rejection(str(exc)) from exc

    record.document = replace(
        record.document,
        environment=replace(record.document.environment, candidate=candidate),
    )
    record.record(
        "environment.prepared",
        {"environment": name, "exit_code": candidate.exit_code},
    )
    record.persist()

    if candidate.exit_code != 0:
        raise Rejection(
            f"execution environment: installing {name} into"
            f" {candidate.prefix_path} failed: {diagnostic}"
        )
    if not Path(candidate.prefix_path).is_dir():
        raise Rejection(
            f"execution environment: installing {name}"
            f" created no environment at {candidate.prefix_path}"
        )
