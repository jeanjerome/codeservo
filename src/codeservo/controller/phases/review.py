"""The independent semantic review, and what the run is allowed to tell it.

The reviewer is a read-only sensor. It is handed the task, the constitution
and an immutable summary of the gates that passed, and nothing about where
the controller keeps the record or the candidate. It reviews one iteration's
candidate and is told nothing of the iterations before it, its own earlier
answers included: a finding that does not recur is then a measurement.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ...actuators import ActuatorError
from ...actuators.prompts import reviewer_prompt
from ...domain.constitution import Constitution, Phase
from ...domain.document import Unset
from ...evidence.digests import sha256_json, sha256_text
from ...resources import review_schema
from ...runtime.process import tail
from ...runtime.sandbox import SandboxError
from ...sensors.gates import GateResult
from ..context import RunContext
from ..decision import review_decision, review_faults, review_feedback
from ..document import FileRecord, ReviewBlock
from ..errors import ControlFailure, Rejection
from ..inference import record_actuation
from ..record import RunRecord
from .converged import Converged

# The shape of the bundle handed to the reviewer. It versions its own shape.
OBSERVATIONS_SCHEMA_VERSION = 1

REDACTED = "<redacted>"

REPOSITORY_GATE = "repository_gate"
EXTERNAL_SENSOR = "external_sensor"


def observed_tail(path: str, locations: tuple) -> str:
    """Bounded gate output with controller-owned locations removed.

    The reviewer is told what a gate emitted, never where the controller keeps
    the record or the candidate.
    """
    text = tail(path)
    # Longest first, so a location nested in another is redacted whole.
    for location in sorted(locations, key=lambda item: len(str(item)), reverse=True):
        text = text.replace(str(location), REDACTED)
    return text


def review_observations(
    constitution: Constitution,
    quick: Sequence[GateResult],
    full: Sequence[GateResult],
    locations: tuple,
) -> dict:
    """The successful gate measurements handed to the read-only reviewer.

    Classification comes from the frozen constitution, so a repository gate
    cannot present itself as an external acceptance sensor by naming itself one.
    """
    sensors = {gate.name: gate.sensor for gate in constitution.gates}
    gates: list[dict] = []
    for phase, results in ((Phase.QUICK, quick), (Phase.FULL, full)):
        for result in results:
            sensor = sensors.get(result.name)
            gates.append(
                {
                    "phase": phase,
                    "name": result.name,
                    "kind": REPOSITORY_GATE if sensor is None else EXTERNAL_SENSOR,
                    "sensor": sensor,
                    "passed": result.passed,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "duration_ms": result.duration_ms,
                    "stdout_sha256": result.stdout_sha256,
                    "stderr_sha256": result.stderr_sha256,
                    "result_sha256": result.result_sha256,
                    "stdout_tail": observed_tail(result.stdout_path, locations),
                    "stderr_tail": observed_tail(result.stderr_path, locations),
                }
            )
    return {"schema_version": OBSERVATIONS_SCHEMA_VERSION, "gates": gates}


@dataclass(frozen=True)
class ReviewOutcome:
    """What the review decided against, if anything, and what to feed back."""

    reasons: list[str]
    feedback: str


def review_candidate(
    context: RunContext,
    record: RunRecord,
    accepted: Converged,
    full: Sequence[GateResult],
    iteration_dir: Path,
) -> ReviewOutcome:
    # Deterministic runtime evidence the read-only reviewer cannot produce
    # itself, built only once every gate passed and every sensor is intact.
    bundle = review_observations(
        context.constitution,
        accepted.quick_gates,
        full,
        (context.run_dir, context.worktree),
    )
    # Serialized once: the prompted bytes are the hashed bytes.
    bundle_json = json.dumps(
        bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )

    prompt_text = reviewer_prompt(context.task, context.constitution, bundle_json)
    prompt_path = iteration_dir / "review" / "prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_text, encoding="utf-8")

    # Recorded before the reviewer runs, so a reviewer failure cannot erase the
    # observations it was given. The reviewer is a read-only sensor: it reads
    # the candidate and writes nothing into it. The adapter denies those writes
    # itself; this describes the confinement it runs under.
    iteration = record.attempted().iteration
    record.attempt = replace(
        record.attempted(),
        review=ReviewBlock(
            prompt=FileRecord(
                path=str(prompt_path), sha256=sha256_text(prompt_text)
            ),
            observations=bundle,
            observations_sha256=sha256_text(bundle_json),
            isolation=context.reviewer.describe_isolation(
                context.confinement.reviewer(context.worktree)
            ),
        ),
    )
    record.persist()

    try:
        review, meta = context.reviewer.review(
            worktree=context.worktree,
            prompt=prompt_text,
            schema_path=review_schema(),
            out_dir=iteration_dir / "review",
            model=context.request.review_model,
            timeout_seconds=context.request.agent_timeout_seconds,
            isolation=context.confinement.actuator,
            effort=context.request.review_effort,
            speed=context.request.review_speed,
        )
    except (ActuatorError, SandboxError) as exc:
        raise Rejection(str(exc)) from exc

    answered = replace(
        _answered(record),
        result=review,
        result_sha256=sha256_json(review),
        meta=meta,
    )
    reviewer = record_actuation(record.document.inference.reviewer, meta)
    record.attempt = replace(record.attempted(), review=answered)
    record.document = replace(
        record.document,
        inference=replace(record.document.inference, reviewer=reviewer),
    )
    record.record(
        "review.finished",
        {
            "iteration": iteration,
            "result_sha256": answered.result_sha256,
            "meta_sha256": meta.meta_sha256,
        },
    )
    record.record(
        "review.profile_observed",
        {
            "iteration": iteration,
            "model": reviewer.observed.model,
            "provenance": reviewer.provenance,
        },
    )
    criteria = context.task.criteria
    blocking = context.constitution.review.blocking_severities
    reasons = review_decision(review, criteria, blocking)
    # A review that misreported the criteria it was asked to decide is a
    # broken sensor, and the candidate is not given another attempt on it.
    if review_faults(review, criteria):
        raise Rejection(reasons)
    return ReviewOutcome(
        reasons=reasons,
        feedback=review_feedback(review, criteria, blocking) if reasons else "",
    )


def _answered(record: RunRecord) -> ReviewBlock:
    """The review block, opened before the reviewer was invoked."""
    block = record.attempted().review
    if isinstance(block, Unset):
        raise ControlFailure("the reviewer was invoked before it was recorded")
    return block
