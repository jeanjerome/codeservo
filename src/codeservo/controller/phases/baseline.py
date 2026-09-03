"""What the source repository already satisfies, before any candidate exists.

The baseline measures the operator's tree. A gate that fails there says
nothing about a change nobody has made yet, and a gate that writes there has
already broken the tree the run is a change to.
"""

from __future__ import annotations

from dataclasses import replace

from ...sensors.gates import baseline_gates, run_gates
from ...sensors.observations import ObservationPathError
from ...workspace.git import create_worktree, is_clean
from ..context import RunContext
from ..errors import Rejection
from ..gate_results import GatePhase, record_gate_events, sensor_faults
from ..record import RunRecord


def measure_baseline(context: RunContext, record: RunRecord) -> None:
    try:
        baseline = run_gates(
            repo=context.repo,
            gates=baseline_gates(context.constitution),
            out_dir=context.run_dir / "baseline",
            isolation=context.confinement.source_gates,
            execution=context.execution,
            run_dir=context.run_dir,
            provider=context.provider,
        )
    except ObservationPathError as exc:
        raise Rejection(str(exc)) from exc

    record.document = replace(record.document, baseline=tuple(baseline))
    record_gate_events(record.journal, GatePhase.BASELINE, baseline)
    record.record(
        "baseline.finished",
        {
            "passed": all(gate.passed for gate in baseline),
            "gate_count": len(baseline),
        },
    )
    record.persist()

    # A broken sensor ends the run here, before anything about the candidate
    # is evaluated: the decision never reports one gate as both.
    faults = sensor_faults(baseline)
    if faults:
        raise Rejection(faults)
    if not all(gate.passed for gate in baseline):
        raise Rejection("baseline gate failed")
    if not is_clean(context.repo):
        raise Rejection("baseline gate mutated the source repository")


def create_candidate(context: RunContext, record: RunRecord) -> None:
    """Create the isolated shallow checkout the run actuates in."""
    create_worktree(context.repo, context.worktree, context.base_commit)
    record.document = replace(record.document, worktree=str(context.worktree))
    record.record("workspace.ready", {"base_commit": context.base_commit})
    record.persist()
