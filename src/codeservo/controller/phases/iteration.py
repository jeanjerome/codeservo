"""The feedback loop: actuate, measure, and either converge or say why not.

One iteration hands the actuator the task and whatever the last measurement
returned, then measures what it left behind. The candidate is snapshotted at
each boundary, so a phase that moved the tree it was measuring is visible
afterwards even where no confinement refused the write.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...actuators import ActuatorError
from ...actuators.prompts import implementer_prompt
from ...domain.constitution import Phase
from ...evidence.digests import sha256_text
from ...runtime.sandbox import SandboxError
from ...sensors.gates import GateResult, run_gates
from ...sensors.observations import ObservationPathError
from ...sensors.scope import scope_sensor
from ..context import RunContext
from ..document import Feedback, FileRecord, Iteration
from ..environment import changed_environment
from ..errors import Rejection
from ..freeze import sensor_tampering
from ..gate_results import (
    GatePhase,
    gate_feedback,
    record_gate_events,
    sensor_faults,
)
from ..inference import record_actuation
from ..record import RunRecord
from ..snapshots import mutated, write_patch_snapshot


@dataclass(frozen=True)
class Converged:
    """The candidate the quick phase accepted, and what it looked like then."""

    quick_gates: list[GateResult]
    state: FileRecord


@dataclass(frozen=True)
class IterationOutcome:
    """What one iteration reached: an accepted candidate, or what to feed back."""

    accepted: Converged | None
    feedback: str


def converge(context: RunContext, record: RunRecord) -> Converged:
    """Iterate until the quick gates pass, or until the budget is exhausted."""
    feedback = ""
    for iteration in range(1, context.request.max_iterations + 1):
        outcome = _iterate(context, record, iteration, feedback)
        if outcome.accepted is not None:
            return outcome.accepted
        feedback = outcome.feedback

    record.record(
        "budget.exhausted", {"max_iterations": context.request.max_iterations}
    )
    raise Rejection(
        "quick gates did not converge within"
        f" {context.request.max_iterations} iterations"
    )


def _iterate(
    context: RunContext, record: RunRecord, iteration: int, feedback: str
) -> IterationOutcome:
    iteration_dir = context.run_dir / "iterations" / f"{iteration:02d}"
    entry: Iteration = {
        "iteration": iteration,
        "feedback_received": feedback,
        "input_state": write_patch_snapshot(
            iteration_dir / "input.patch", context.worktree, context.base_commit
        ),
    }
    # However this iteration ends, the record holds it before the run acts on
    # what it says.
    try:
        _actuate(context, record, iteration_dir, iteration, feedback, entry)
        quick = _measure(context, record, iteration_dir, entry)
        return _verdict(record, iteration_dir, entry, quick)
    finally:
        record.document["iterations"].append(entry)
        record.persist()


def _actuate(
    context: RunContext,
    record: RunRecord,
    iteration_dir: Path,
    iteration: int,
    feedback: str,
    entry: Iteration,
) -> None:
    prompt = implementer_prompt(context.task, context.constitution, feedback)
    prompt_path = iteration_dir / "prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    entry["prompt"] = {"path": str(prompt_path), "sha256": sha256_text(prompt)}
    record.record(
        "actuator.started",
        {"iteration": iteration, "prompt_sha256": entry["prompt"]["sha256"]},
    )

    try:
        agent = context.implementer.implement(
            worktree=context.worktree,
            prompt=prompt,
            out_dir=iteration_dir / "agent",
            model=context.request.model,
            timeout_seconds=context.request.agent_timeout_seconds,
            isolation=context.confinement.actuator,
            effort=context.request.effort,
            speed=context.request.speed,
        )
    except (ActuatorError, SandboxError) as exc:
        entry["agent_error"] = str(exc)
        raise Rejection(str(exc)) from exc

    entry["agent"] = agent
    record.record(
        "actuator.finished",
        {
            "iteration": iteration,
            "exit_code": agent.exit_code,
            "result_sha256": agent.result_sha256,
        },
    )
    implementer = context.inference["implementer"]
    record_actuation(implementer, agent)
    record.record(
        "actuator.profile_observed",
        {
            "iteration": iteration,
            "model": implementer["observed"].model,
            "provenance": implementer["provenance"],
        },
    )
    entry["actuator_state"] = write_patch_snapshot(
        iteration_dir / "actuator.patch", context.worktree, context.base_commit
    )
    if agent.exit_code != 0:
        raise Rejection(f"implementer exited with {agent.exit_code}")


def _measure(
    context: RunContext, record: RunRecord, iteration_dir: Path, entry: Iteration
) -> list[GateResult]:
    scope = scope_sensor(
        context.worktree, context.base_commit, context.constitution.scope
    )
    try:
        quick = run_gates(
            repo=context.worktree,
            gates=context.constitution.gates_for(Phase.QUICK),
            out_dir=iteration_dir / "quick",
            sensor_paths=context.sensor_paths,
            isolation=context.confinement.candidate_gates,
            execution=context.execution,
            run_dir=context.run_dir,
        )
    except ObservationPathError as exc:
        raise Rejection(str(exc)) from exc

    record_gate_events(record.journal, GatePhase.QUICK, quick)
    entry["observed_state"] = write_patch_snapshot(
        iteration_dir / "observed.patch", context.worktree, context.base_commit
    )
    entry["scope"] = {
        "passed": scope.passed,
        "summary": scope.summary,
        "details": scope.details,
    }
    entry["quick_gates"] = quick

    faults = sensor_faults(quick)
    if faults:
        raise Rejection(faults)

    control_failures = sensor_tampering(context.sensor_paths, context.sensor_evidence)
    control_failures += changed_environment(
        record.document["environment"], context.worktree, context.execution
    )
    # The two snapshots bracket the quick phase: what the actuator left
    # behind, and what the gates were measuring when they finished.
    control_failures += mutated(
        Phase.QUICK, entry["actuator_state"], entry["observed_state"]
    )
    if control_failures:
        raise Rejection(control_failures)
    return quick


def _verdict(
    record: RunRecord,
    iteration_dir: Path,
    entry: Iteration,
    quick: list[GateResult],
) -> IterationOutcome:
    if entry["scope"]["passed"] and all(gate.passed for gate in quick):
        entry["controller_feedback"] = None
        return IterationOutcome(
            accepted=Converged(quick_gates=quick, state=entry["observed_state"]),
            feedback="",
        )

    parts = []
    if not entry["scope"]["passed"]:
        parts.append("Structural invariant failures:\n" + entry["scope"]["summary"])
    parts.append(gate_feedback(quick))
    feedback = "\n\n".join(part for part in parts if part).strip()

    feedback_path = iteration_dir / "controller-feedback.md"
    feedback_path.write_text(feedback, encoding="utf-8")
    emitted: Feedback = {
        "path": str(feedback_path),
        "sha256": sha256_text(feedback),
        "text": feedback,
    }
    entry["controller_feedback"] = emitted
    record.record(
        "feedback.emitted",
        {"iteration": entry["iteration"], "sha256": emitted["sha256"]},
    )
    return IterationOutcome(accepted=None, feedback=feedback)
