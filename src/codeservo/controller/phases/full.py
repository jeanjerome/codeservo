"""The full gate set, measured once the candidate has passed the quick one.

A full gate that fails is fed back like a quick one: the phase is slower, not
more final. What ends the run from here is a control failure, never a
failing gate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ...domain.constitution import Phase
from ...sensors.gates import GateResult, run_gates
from ...sensors.observations import ObservationPathError
from ..context import RunContext
from ..environment import changed_environment
from ..errors import Rejection
from ..freeze import sensor_tampering
from ..gate_results import GatePhase, gate_feedback, record_gate_events, sensor_faults
from ..record import RunRecord
from ..snapshots import mutated, write_patch_snapshot
from .converged import Converged


@dataclass(frozen=True)
class FullOutcome:
    """What the full gates said: the results, and what to feed back if any failed."""

    gates: tuple[GateResult, ...]
    reasons: list[str]
    feedback: str


def measure_full(
    context: RunContext, record: RunRecord, accepted: Converged, iteration_dir: Path
) -> FullOutcome:
    try:
        full = run_gates(
            repo=context.worktree,
            gates=context.constitution.gates_for(Phase.FULL),
            out_dir=iteration_dir / "full",
            sensor_paths=context.sensor_paths,
            isolation=context.confinement.candidate_gates,
            execution=context.execution,
            run_dir=context.run_dir,
        )
    except ObservationPathError as exc:
        raise Rejection(str(exc)) from exc

    full_state = write_patch_snapshot(
        iteration_dir / "full.patch", context.worktree, context.base_commit
    )
    record.attempt = replace(
        record.attempted(), full_gates=tuple(full), full_gate_state=full_state
    )
    record_gate_events(record.journal, GatePhase.FULL, full)
    record.persist()

    faults = sensor_faults(full)
    if faults:
        raise Rejection(faults)

    control_failures = sensor_tampering(context.sensor_paths, context.sensor_evidence)
    environment, changed = changed_environment(
        record.document.environment, context.worktree, context.execution
    )
    record.document = replace(record.document, environment=environment)
    control_failures += changed
    # The candidate as the quick phase left it, against the candidate the full
    # gates have just finished measuring.
    control_failures += mutated(Phase.FULL, accepted.state, full_state)
    if control_failures:
        raise Rejection(control_failures)

    reasons = [f"full gate {gate.name} failed" for gate in full if not gate.passed]
    return FullOutcome(
        gates=tuple(full),
        reasons=reasons,
        feedback=gate_feedback(full) if reasons else "",
    )
