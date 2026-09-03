"""The feedback loop: actuate, measure, and either accept or say why not.

One iteration hands the actuator the task and whatever the last measurement
returned, then measures what it left behind: the scope and the quick gates,
then the full gates, then the independent review. The first of the three to
decide against the candidate writes the feedback the next iteration starts
from, and an iteration that all three let through is the accepted one. A gate
that passed may still decide against the candidate through a ratchet it
declares, read against the document the same gate wrote at the baseline. The
candidate is snapshotted at each boundary, so a phase that moved the tree it
was measuring is visible afterwards even where no confinement refused the
write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from ...actuators import ActuatorError
from ...actuators.prompts import implementer_prompt
from ...domain.constitution import Gate, Phase
from ...domain.document import Unset
from ...domain.task import Criterion, criteria_by_gate, reviewed_criteria
from ...evidence.digests import sha256_text
from ...runtime.sandbox import SandboxError
from ...sensors.gates import GateResult, run_gates
from ...sensors.observations import ObservationPathError
from ...sensors.scope import scope_sensor
from ..context import RunContext
from ..decision import SATISFIED
from ..document import Feedback, FileRecord, Iteration, ReviewBlock, ScopeResult
from ..environment import changed_environment
from ..errors import ControlFailure, Escalation, Rejection
from ..freeze import sensor_tampering
from ..gate_results import (
    GatePhase,
    gate_clause,
    gate_feedback,
    gate_reasons,
    record_gate_events,
    sensor_faults,
)
from ..inference import record_actuation
from ..ratchet import (
    BrokenRatchet,
    broken_ratchets,
    ratchet_clause,
    ratchet_feedback,
    ratchet_reasons,
)
from ..record import RunRecord
from ..snapshots import mutated, write_patch_snapshot
from .converged import Converged
from .full import measure_full
from .review import review_candidate


class Stage(StrEnum):
    """Which measurement of an iteration decided against the candidate."""

    QUICK = "quick"
    FULL = "full"
    REVIEW = "review"


@dataclass(frozen=True)
class IterationOutcome:
    """What one iteration reached: acceptance, or why not and what to feed back.

    `stage` names the measurement that decided against the candidate, and is
    what tells an exhausted budget apart: one spent on a gate is a rejection,
    one spent on the review alone is a person's to settle.
    """

    accepted: bool
    reasons: list[str]
    feedback: str
    stage: Stage | None = None


ACCEPTED = IterationOutcome(accepted=True, reasons=[], feedback="")


def converge(context: RunContext, record: RunRecord) -> None:
    """Iterate until an iteration is accepted, or until the budget is exhausted.

    A budget that runs out is not a failure of the candidate: what the last
    iteration decided against it is named beside the exhaustion, so the
    decision says where the loop stopped. Where it stopped decides how the
    run ends. A last iteration a gate, a ratchet or the scope refused is
    rejected. One that every deterministic control let through and the review
    alone objected to is escalated: the review is a sensor and not the final
    authority, and a person is.
    """
    outcome = IterationOutcome(accepted=False, reasons=[], feedback="")
    for iteration in range(1, context.request.max_iterations + 1):
        outcome = _iterate(context, record, iteration, outcome.feedback)
        if outcome.accepted:
            return

    record.record(
        "budget.exhausted", {"max_iterations": context.request.max_iterations}
    )
    reasons = [
        f"did not converge within {context.request.max_iterations} iterations",
        *outcome.reasons,
    ]
    if outcome.stage == Stage.REVIEW:
        raise Escalation(reasons)
    raise Rejection(reasons)


def _iterate(
    context: RunContext, record: RunRecord, iteration: int, feedback: str
) -> IterationOutcome:
    iteration_dir = context.run_dir / "iterations" / f"{iteration:02d}"
    record.attempt = Iteration(
        iteration=iteration,
        feedback_received=feedback,
        input_state=write_patch_snapshot(
            iteration_dir / "input.patch", context.worktree, context.base_commit
        ),
    )
    # However this iteration ends, the record holds it before the run acts on
    # what it says: every stage states what it reached on the record, so a
    # rejection leaves behind what happened up to it.
    try:
        _actuate(context, record, iteration_dir, iteration, feedback)
        quick = _measure(context, record, iteration_dir)
        converged = _quick_verdict(context, record, iteration_dir, quick)
        if isinstance(converged, IterationOutcome):
            return converged
        full = measure_full(context, record, converged, iteration_dir)
        if full.reasons:
            return _decided(
                record, iteration_dir, Stage.FULL, full.reasons, full.feedback
            )
        review = review_candidate(context, record, converged, full.gates, iteration_dir)
        if review.reasons:
            return _decided(
                record, iteration_dir, Stage.REVIEW, review.reasons, review.feedback
            )
        # Nothing is fed back from here: what follows is either acceptance or
        # a question no control answers, and the actuator corrects neither.
        record.attempt = replace(record.attempted(), controller_feedback=None)
        if review.escalations:
            raise Escalation(review.escalations)
        return ACCEPTED
    finally:
        record.keep()
        record.persist()


def _gates_clauses(
    phase: str, gates: Sequence[GateResult], broken: Sequence[BrokenRatchet]
) -> list[str]:
    passed = sum(1 for gate in gates if gate.passed)
    clauses = [f"{phase} gates: {passed} of {len(gates)} passed"]
    failed = [gate_clause(gate) for gate in gates if not gate.passed]
    if failed:
        clauses.append("failed: " + ", ".join(failed))
    if broken:
        clauses.append(ratchet_clause(broken))
    return clauses


def _review_clause(
    review: ReviewBlock, criteria: Mapping[str, Criterion], blocking: tuple[str, ...]
) -> str:
    """What one review decided, in one clause.

    Only the criteria the reviewer was asked about are counted. A criterion a
    gate decides is settled by the gate clause of the same line, and counting
    it here would say the review answered something it was never given.
    """
    result = review.result
    if isinstance(result, Unset):
        return "review: no answer"
    reported = {str(item.get("id", "")): item for item in result.get("criteria", [])}
    reviewed = reviewed_criteria(criteria)
    unsatisfied = [
        criterion_id
        for criterion_id in reviewed
        if str(reported.get(criterion_id, {}).get("status", "")) != SATISFIED
    ]
    findings = sum(
        1
        for finding in result.get("findings", [])
        if str(finding.get("severity", "")) in set(blocking)
    )
    parts = []
    if unsatisfied:
        parts.append(
            f"{len(unsatisfied)} of {len(reviewed)} reviewed criteria not"
            f" satisfied ({', '.join(unsatisfied)})"
        )
    parts.append(f"{findings} blocking finding{'' if findings == 1 else 's'}")
    return "review: " + ", ".join(parts)


def iteration_recap(
    iterations: Sequence[Iteration],
    criteria: Mapping[str, Criterion],
    blocking: tuple[str, ...],
    gates: Sequence[Gate] = (),
    baseline: Sequence[GateResult] = (),
) -> tuple[str, ...]:
    """One line per iteration so far: what each measurement said of it.

    The line names each failing gate with what its document summarised, each
    ratchet a passing gate broke with both values, and the review with what
    it decided against, so an actuator reading several of them sees what
    moved between attempts and what did not. An iteration the record holds
    without a measurement, which the loop never continues past, is said to be
    unmeasured rather than described.
    """
    lines: list[str] = []
    for entry in iterations:
        scope = entry.scope
        quick = entry.quick_gates
        if isinstance(scope, Unset) or isinstance(quick, Unset):
            lines.append(f"Iteration {entry.iteration}: not measured")
            continue
        clauses = [
            scope.summary,
            *_gates_clauses("quick", quick, broken_ratchets(gates, baseline, quick)),
        ]
        if not isinstance(entry.full_gates, Unset):
            full = entry.full_gates
            clauses.extend(
                _gates_clauses("full", full, broken_ratchets(gates, baseline, full))
            )
        if not isinstance(entry.review, Unset):
            clauses.append(_review_clause(entry.review, criteria, blocking))
        lines.append(f"Iteration {entry.iteration}: " + "; ".join(clauses))
    return tuple(lines)


def _actuate(
    context: RunContext,
    record: RunRecord,
    iteration_dir: Path,
    iteration: int,
    feedback: str,
) -> None:
    prompt = implementer_prompt(
        context.task,
        context.constitution,
        feedback,
        iteration_recap(
            record.document.iterations,
            context.task.criteria,
            context.constitution.review.blocking_severities,
            context.constitution.gates,
            record.baseline(),
        ),
    )
    prompt_path = iteration_dir / "prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    written = FileRecord(path=str(prompt_path), sha256=sha256_text(prompt))
    record.attempt = replace(record.attempted(), prompt=written)
    record.record(
        "actuator.started",
        {"iteration": iteration, "prompt_sha256": written.sha256},
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
        record.attempt = replace(record.attempted(), agent_error=str(exc))
        raise Rejection(str(exc)) from exc

    record.attempt = replace(record.attempted(), agent=agent)
    record.record(
        "actuator.finished",
        {
            "iteration": iteration,
            "exit_code": agent.exit_code,
            "result_sha256": agent.result_sha256,
        },
    )
    implementer = record_actuation(record.document.inference.implementer, agent)
    record.document = replace(
        record.document,
        inference=replace(record.document.inference, implementer=implementer),
    )
    record.record(
        "actuator.profile_observed",
        {
            "iteration": iteration,
            "model": implementer.observed.model,
            "provenance": implementer.provenance,
        },
    )
    record.attempt = replace(
        record.attempted(),
        actuator_state=write_patch_snapshot(
            iteration_dir / "actuator.patch", context.worktree, context.base_commit
        ),
    )
    if agent.exit_code != 0:
        raise Rejection(f"implementer exited with {agent.exit_code}")


def _measure(
    context: RunContext, record: RunRecord, iteration_dir: Path
) -> tuple[GateResult, ...]:
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
    observed_state = write_patch_snapshot(
        iteration_dir / "observed.patch", context.worktree, context.base_commit
    )
    entry = replace(
        record.attempted(),
        observed_state=observed_state,
        scope=ScopeResult(
            passed=scope.passed, summary=scope.summary, details=scope.details
        ),
        quick_gates=tuple(quick),
    )
    record.attempt = entry

    faults = sensor_faults(quick)
    if faults:
        raise Rejection(faults)

    control_failures = sensor_tampering(context.sensor_paths, context.sensor_evidence)
    environment, changed = changed_environment(
        record.document.environment, context.worktree, context.execution
    )
    record.document = replace(record.document, environment=environment)
    control_failures += changed
    # The two snapshots bracket the quick phase: what the actuator left
    # behind, and what the gates were measuring when they finished.
    control_failures += mutated(
        Phase.QUICK, _reached(entry.actuator_state), observed_state
    )
    if control_failures:
        raise Rejection(control_failures)
    return tuple(quick)


def _reached(state: FileRecord | Unset) -> FileRecord:
    """A snapshot the iteration has already taken.

    Every caller here runs after the stage that took it, so an unset snapshot
    would be a control failure of this module rather than a fact about the run.
    """
    if isinstance(state, Unset):
        raise ControlFailure("the iteration has taken no snapshot yet")
    return state


def _quick_verdict(
    context: RunContext,
    record: RunRecord,
    iteration_dir: Path,
    quick: tuple[GateResult, ...],
) -> Converged | IterationOutcome:
    """The candidate the quick phase lets through, or what it decided against.

    A gate that passed and broke a ratchet decides against the candidate the
    way a failing gate does: the phase is not converged, and the actuator is
    told both values.
    """
    entry = record.attempted()
    scope = entry.scope
    if isinstance(scope, Unset):
        raise ControlFailure("the iteration has measured no scope yet")
    broken = broken_ratchets(context.constitution.gates, record.baseline(), quick)
    if scope.passed and all(gate.passed for gate in quick) and not broken:
        return Converged(quick_gates=quick, state=_reached(entry.observed_state))

    criteria = criteria_by_gate(context.task.criteria)
    reasons = []
    parts = []
    if not scope.passed:
        reasons.append(f"scope: {scope.summary}")
        parts.append("Structural invariant failures:\n" + scope.summary)
    reasons.extend(gate_reasons(GatePhase.QUICK, quick, criteria))
    reasons.extend(ratchet_reasons(GatePhase.QUICK, broken))
    parts.append(gate_feedback(quick, criteria))
    parts.append(ratchet_feedback(broken))
    feedback = "\n\n".join(part for part in parts if part).strip()
    return _decided(record, iteration_dir, Stage.QUICK, reasons, feedback)


def _decided(
    record: RunRecord,
    iteration_dir: Path,
    stage: Stage,
    reasons: list[str],
    feedback: str,
) -> IterationOutcome:
    """Write what a stage decided against the candidate, for the next iteration.

    The feedback is written once, wherever in the iteration it came from.
    """
    feedback_path = iteration_dir / "controller-feedback.md"
    feedback_path.write_text(feedback, encoding="utf-8")
    emitted = Feedback(
        path=str(feedback_path),
        sha256=sha256_text(feedback),
        text=feedback,
    )
    record.attempt = replace(record.attempted(), controller_feedback=emitted)
    record.record(
        "feedback.emitted",
        {
            "iteration": record.attempted().iteration,
            "stage": stage,
            "sha256": emitted.sha256,
        },
    )
    return IterationOutcome(
        accepted=False, reasons=reasons, feedback=feedback, stage=stage
    )
