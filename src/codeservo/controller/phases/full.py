"""The full gate set, measured once the candidate has stopped changing.

Nothing is fed back from here. The quick phase is where a candidate is given
another attempt; the full phase either confirms what converged or ends the
run.
"""

from __future__ import annotations

from ...domain.constitution import Phase
from ...sensors.gates import GateResult, run_gates
from ...sensors.observations import ObservationPathError
from ..context import RunContext
from ..environment import changed_environment
from ..errors import Rejection
from ..freeze import sensor_tampering
from ..gate_results import GatePhase, record_gate_events, sensor_faults
from ..record import RunRecord
from ..snapshots import mutated, write_patch_snapshot
from .iteration import Converged


def measure_full(
    context: RunContext, record: RunRecord, accepted: Converged
) -> list[GateResult]:
    try:
        full = run_gates(
            repo=context.worktree,
            gates=context.constitution.gates_for(Phase.FULL),
            out_dir=context.run_dir / "full",
            sensor_paths=context.sensor_paths,
            isolation=context.confinement.candidate_gates,
            execution=context.execution,
            run_dir=context.run_dir,
        )
    except ObservationPathError as exc:
        raise Rejection(str(exc)) from exc

    record.document["full_gates"] = full
    record_gate_events(record.journal, GatePhase.FULL, full)
    full_state = write_patch_snapshot(
        context.run_dir / "full.patch", context.worktree, context.base_commit
    )
    record.document["full_gate_state"] = full_state
    record.persist()

    faults = sensor_faults(full)
    if faults:
        raise Rejection(faults)

    reasons = sensor_tampering(context.sensor_paths, context.sensor_evidence)
    reasons += changed_environment(
        record.document["environment"], context.worktree, context.execution
    )
    # The candidate as the quick phase left it, against the candidate the full
    # gates have just finished measuring.
    reasons += mutated(Phase.FULL, accepted.state, full_state)
    if not all(gate["passed"] for gate in full):
        reasons.append("full gate failed")
    if reasons:
        raise Rejection(reasons)
    return full
