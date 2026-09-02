"""The acceptance rules, applied mechanically to what the reviewer returned.

The reviewer is a sensor. What it says about each criterion of the task, and
the severity of each finding it raises, are turned into a decision here, by
rules the constitution fixed before the run started.
"""

from __future__ import annotations

SATISFIED = "satisfied"


def review_decision(
    review: dict, task_criteria: dict[str, str], blocking: tuple[str, ...]
) -> list[str]:
    reasons: list[str] = []
    seen: dict[str, str] = {}
    for item in review.get("criteria", []):
        criterion_id = str(item.get("id", ""))
        status = str(item.get("status", ""))
        if criterion_id in seen:
            reasons.append(f"review duplicated criterion {criterion_id}")
        seen[criterion_id] = status

    for criterion_id in task_criteria:
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
