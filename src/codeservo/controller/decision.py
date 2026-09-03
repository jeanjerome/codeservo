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

    Only the criteria the reviewer was asked about are read here. What it
    says about a criterion a gate decides is kept in the record and decides
    nothing: the gate is the authority on it, and a review that contradicts a
    green gate is a disagreement between two sensors rather than a verdict.
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
        elif reported != SATISFIED:
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
