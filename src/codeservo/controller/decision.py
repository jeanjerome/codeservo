"""The acceptance rules, applied mechanically to what the reviewer returned.

The reviewer is a sensor, and it is asked about the criteria the task left to
it. What it says about each of those, and the severity of each finding it
raises, are turned into a decision here, by rules the constitution fixed
before the run started. A criterion naming a gate is not among them: the gate
measured it, and a run only reaches the review once every gate has passed.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..domain.task import Criterion, reviewed_criteria

SATISFIED = "satisfied"
NOT_SATISFIED = "not_satisfied"
# The reviewer read the repository and found no evidence either way. That is
# not something the candidate is corrected for: nobody decided.
NOT_VERIFIABLE = "not_verifiable"


def review_faults(review: dict, task_criteria: Mapping[str, Criterion]) -> list[str]:
    """What the reviewer got wrong about the criteria it was asked to decide.

    A criterion reported twice, one the reviewer was asked about and omitted,
    or one the task never declared, is a fault of the review sensor and not
    of the candidate: nothing the actuator changes can correct it, so it is
    never fed back. A criterion the task declares and a gate decides is
    neither asked for nor unknown, so an answer about one is no fault.
    """
    faults: list[str] = []
    seen: set[str] = set()
    for item in review.get("criteria", []):
        criterion_id = str(item.get("id", ""))
        if criterion_id in seen:
            faults.append(f"review duplicated criterion {criterion_id}")
        seen.add(criterion_id)
    faults.extend(
        f"review missing criterion {criterion_id}"
        for criterion_id in reviewed_criteria(task_criteria)
        if criterion_id not in seen
    )
    faults.extend(
        f"review returned unknown criterion {extra}"
        for extra in sorted(seen - set(task_criteria))
    )
    return faults


def _place(finding: dict) -> str:
    path = finding.get("path")
    line = finding.get("line")
    if not isinstance(path, str) or not path:
        return "(no path)"
    if isinstance(line, int) and not isinstance(line, bool):
        return f"{path}:{line}"
    return path


def review_feedback(
    review: dict, task_criteria: Mapping[str, Criterion], blocking: tuple[str, ...]
) -> str:
    """What the reviewer objected to, spelled out for the actuator.

    Only what decided against the candidate is fed back: the criteria the
    reviewer was asked about and did not find satisfied, with the evidence it
    gave, and the findings whose severity the constitution declares blocking.
    A finding below that line does not stand between the candidate and
    acceptance, so it is not fed back as something to fix.
    """
    reported = {
        str(item.get("id", "")): item for item in review.get("criteria", [])
    }
    unsatisfied = [
        reported[criterion_id]
        for criterion_id in reviewed_criteria(task_criteria)
        if criterion_id in reported
        and str(reported[criterion_id].get("status", "")) != SATISFIED
    ]
    blocking_set = set(blocking)
    findings = [
        finding
        for finding in review.get("findings", [])
        if str(finding.get("severity", "")) in blocking_set
    ]
    if not unsatisfied and not findings:
        return ""
    lines = ["Review did not accept the candidate."]
    if unsatisfied:
        lines.append("Criteria not satisfied:")
        for item in unsatisfied:
            status = str(item.get("status", ""))
            evidence = str(item.get("evidence", "")).strip() or "no evidence given"
            lines.append(f"- {item.get('id')} ({status}): {evidence}")
    if findings:
        lines.append("Blocking findings:")
        for finding in findings:
            severity = str(finding.get("severity", ""))
            message = str(finding.get("message", "")).strip() or "no message"
            lines.append(f"- [{severity}] {_place(finding)}: {message}")
            evidence = str(finding.get("evidence", "")).strip()
            if evidence:
                lines.append(f"  evidence: {evidence}")
    return "\n".join(lines)


def review_decision(
    review: dict, task_criteria: Mapping[str, Criterion], blocking: tuple[str, ...]
) -> list[str]:
    """Why the review decided against the candidate, if it did.

    Only the criteria the reviewer was asked about are read here, and only
    what the candidate can be corrected for is a reason: a criterion it did
    not satisfy, and a finding the constitution declares blocking. A criterion
    the reviewer could not verify is nobody's verdict, and what it says about
    a criterion a gate decides is the gate's to settle; both are read by
    `review_escalations` instead.
    """
    reasons: list[str] = []
    seen: dict[str, str] = {}
    for item in review.get("criteria", []):
        criterion_id = str(item.get("id", ""))
        status = str(item.get("status", ""))
        if criterion_id in seen:
            reasons.append(f"review duplicated criterion {criterion_id}")
        seen[criterion_id] = status

    for criterion_id in reviewed_criteria(task_criteria):
        reported = seen.get(criterion_id)
        if reported is None:
            reasons.append(f"review missing criterion {criterion_id}")
        elif reported not in (SATISFIED, NOT_VERIFIABLE):
            reasons.append(f"criterion {criterion_id} is {reported}")

    extras = sorted(set(seen) - set(task_criteria))
    reasons.extend(f"review returned unknown criterion {extra}" for extra in extras)

    blocking_set = set(blocking)
    for finding in review.get("findings", []):
        severity = str(finding.get("severity", ""))
        if severity in blocking_set:
            message = str(finding.get("message", "blocking review finding"))
            reasons.append(f"{severity} finding: {message}")
    return reasons


def review_escalations(review: dict, task_criteria: Mapping[str, Criterion]) -> list[str]:
    """What the review leaves to a person, because no control can settle it.

    A criterion the reviewer was asked about and could not verify is one no
    gate decides and no reviewer could: the task named a verification nobody
    can perform. A criterion a gate decided that the reviewer reports as not
    satisfied is two sensors disagreeing, the deterministic one having said
    yes. Neither is fed back, because neither is the candidate's to correct;
    both are stated so the record says why the run did not decide.
    """
    reported = {
        str(item.get("id", "")): str(item.get("status", ""))
        for item in review.get("criteria", [])
    }
    escalations = [
        f"criterion {criterion_id} is {NOT_VERIFIABLE}"
        for criterion_id in reviewed_criteria(task_criteria)
        if reported.get(criterion_id) == NOT_VERIFIABLE
    ]
    escalations.extend(
        f"review contradicts gate {criterion.gate} on criterion {criterion_id}"
        for criterion_id, criterion in task_criteria.items()
        if criterion.gate is not None and reported.get(criterion_id) == NOT_SATISFIED
    )
    return escalations
