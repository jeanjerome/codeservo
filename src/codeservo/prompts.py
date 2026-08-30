from __future__ import annotations

import json

from .model import Constitution
from .task import Task


def _actuator_constitution(constitution: Constitution) -> str:
    lines = [
        "version = 1",
        "",
        "[scope]",
        f"protected = {json.dumps(list(constitution.scope.protected))}",
        f"max_changed_files = {constitution.scope.max_changed_files}",
        f"max_diff_lines = {constitution.scope.max_diff_lines}",
    ]
    for gate in constitution.gates:
        command = gate.command if gate.sensor is None else "<controller-owned sensor>"
        lines.extend(
            [
                "",
                "[[gate]]",
                f"name = {json.dumps(gate.name)}",
                f"phase = {json.dumps(gate.phase)}",
                f"command = {json.dumps(command)}",
                f"timeout_seconds = {gate.timeout_seconds}",
                f"baseline = {str(gate.baseline).lower()}",
            ]
        )
    lines.extend(
        [
            "",
            "[review]",
            "blocking_severities = "
            + json.dumps(list(constitution.review.blocking_severities)),
        ]
    )
    return "\n".join(lines)


def implementer_prompt(task: Task, constitution: Constitution, feedback: str) -> str:
    return f"""You are the ACTUATOR in a software control loop. You may modify the workspace, but you are not the controller and you do not decide when the task is complete.

Rules:
- Implement only the task below.
- Preserve repository instructions and architecture.
- Do not inspect or modify .codeservo/**; those files belong to the controller.
- Do not run gates marked baseline=false. They are independent acceptance sensors owned by the controller.
- Do not commit, push, create PRs, or change Git configuration.
- Prefer the smallest coherent change.
- The controller will run the authoritative gates. You may run checks yourself, but your claims are not evidence.

TASK
====
{task.raw_text}

ACTUATOR VIEW OF FROZEN REPOSITORY CONSTITUTION
================================================
{_actuator_constitution(constitution)}

CONTROLLER FEEDBACK FROM THE PREVIOUS ITERATION
===============================================
{feedback or "None. This is the first iteration."}

Work directly in the current workspace. When you have made the best correction you can, stop.
"""


def reviewer_prompt(task: Task, constitution: Constitution) -> str:
    criteria = "\n".join(f"- {key}: {value}" for key, value in task.criteria.items())
    return f"""You are an independent REVIEW SENSOR. You are read-only. Do not modify files.

Review the current working tree against the frozen task and constitution. Inspect the actual diff and repository as needed. Do not trust the implementer's claims.

For each acceptance criterion, return exactly one criteria entry using the exact criterion id. Mark it:
- satisfied: concrete repository evidence demonstrates it.
- not_satisfied: the implementation contradicts or misses it.
- not_verifiable: available repository evidence is insufficient.

Report concrete defects as findings. Severity meanings:
- blocker: unsafe/corrupting or fundamentally invalid change.
- major: task or correctness defect that should prevent acceptance.
- minor: non-blocking issue.

Do not invent style findings already covered by deterministic gates. Do not return an overall verdict; the controller computes it.

ACCEPTANCE CRITERIA
===================
{criteria}

TASK
====
{task.raw_text}

FROZEN REPOSITORY CONSTITUTION
==============================
{constitution.raw_text}
"""
