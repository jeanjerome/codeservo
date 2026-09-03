from __future__ import annotations

import json
from collections.abc import Sequence

from ..domain.constitution import Constitution, Gate
from ..domain.task import Criterion, Task, reviewed_criteria


def _toml_string(value: str) -> str:
    """One string, spelled the way a TOML basic string spells it.

    JSON escapes almost identically, and differs exactly where the view has to
    parse: it spells a character outside the basic plane as a surrogate pair,
    which TOML reads as no scalar value at all, and it leaves U+007F literal,
    which a basic string cannot carry.
    """
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007F")


def _measurement(gate: Gate) -> tuple[str, ...]:
    """The one line naming what a gate measures.

    A gate declares a shell command or a provider task and never both, so one
    of the two names the measurement. A gate carrying a controller-owned
    acceptance sensor names neither here: the placeholder is all of that gate
    the actuator sees, whatever the gate itself declares. A gate naming
    nothing is one no constitution can carry, and the view asserts no
    declaration it was not given.
    """
    if gate.sensor is not None:
        return ('command = "<controller-owned sensor>"',)
    if gate.command is not None:
        return (f"command = {_toml_string(gate.command)}",)
    if gate.task is not None:
        return (f"task = {_toml_string(gate.task)}",)
    return ()


def _actuator_constitution(constitution: Constitution) -> str:
    protected = ", ".join(
        _toml_string(pattern) for pattern in constitution.scope.protected
    )
    lines = [
        "version = 1",
        "",
        "[scope]",
        f"protected = [{protected}]",
        f"max_changed_files = {constitution.scope.max_changed_files}",
        f"max_diff_lines = {constitution.scope.max_diff_lines}",
    ]
    for gate in constitution.gates:
        lines.extend(
            [
                "",
                "[[gate]]",
                f"name = {_toml_string(gate.name)}",
                f"phase = {_toml_string(gate.phase)}",
                *_measurement(gate),
                f"timeout_seconds = {gate.timeout_seconds}",
                f"baseline = {str(gate.baseline).lower()}",
            ]
        )
    severities = ", ".join(
        _toml_string(severity) for severity in constitution.review.blocking_severities
    )
    lines.extend(
        [
            "",
            "[review]",
            f"blocking_severities = [{severities}]",
        ]
    )
    return "\n".join(lines)


def _verified_by(criterion: Criterion) -> str:
    """The control one criterion names as the one that decides it."""
    if criterion.gate is None:
        return "verified by review"
    return f"verified by gate {criterion.gate}"


def _criteria(task: Task) -> str:
    """Every criterion, by its id, with what will decide it.

    The implementer is told which control answers each one, because a gate it
    can run and a reviewer it cannot are not corrected the same way.
    """
    return "\n".join(
        f"- {criterion.id} ({_verified_by(criterion)}): {criterion.text}"
        for criterion in task.criteria.values()
    )


def _reviewed(task: Task) -> str:
    """The criteria the reviewer is asked to decide.

    A task whose every criterion names a gate leaves the reviewer none, and
    the prompt says so rather than presenting an empty list: the reviewer
    still reports findings, and still returns the array the schema requires.
    """
    criteria = reviewed_criteria(task.criteria)
    if not criteria:
        return (
            "None. Every acceptance criterion of this task is decided by a gate."
            " Return an empty criteria array."
        )
    return "\n".join(
        f"- {criterion.id}: {criterion.text}" for criterion in criteria.values()
    )


def _gated(task: Task) -> str:
    """The criteria a gate has already decided, and the reviewer must not.

    They are shown because they say what the change was for, and named as
    settled because the run reached this review only after every gate passed.
    """
    gated = [
        criterion for criterion in task.criteria.values() if criterion.gate is not None
    ]
    if not gated:
        return ""
    lines = [
        "",
        "ACCEPTANCE CRITERIA A GATE DECIDES",
        "==================================",
        "The gate named beside each one measured it on this working tree and"
        " passed. Do not return a criteria entry for any of them. If you find"
        " something wrong with one, report it as a finding.",
        *(
            f"- {criterion.id} (gate {criterion.gate}): {criterion.text}"
            for criterion in gated
        ),
        "",
    ]
    return "\n".join(lines)


def _feedback_section(feedback: str, history: Sequence[str]) -> str:
    """What the controller has to say before this iteration acts.

    The first iteration has nothing to be told. A later one is told every
    iteration so far in one line each, so it can see what moved and what did
    not, and then the last measurement in full.
    """
    if not history:
        return "None. This is the first iteration."
    lines = ["Iterations so far:"]
    lines.extend(f"- {line}" for line in history)
    lines.extend(["", "Feedback from the previous iteration:", feedback])
    return "\n".join(lines)


def implementer_prompt(
    task: Task,
    constitution: Constitution,
    feedback: str,
    history: Sequence[str] = (),
) -> str:
    """The prompt one iteration of the implementer receives.

    `feedback` is what the previous iteration's measurement said, and
    `history` one rendered line per iteration so far, both empty on the first.
    """
    return f"""You are the ACTUATOR in a software control loop. You may modify the workspace, but you are not the controller and you do not decide when the task is complete.

Rules:
- Implement only the task below.
- Preserve repository instructions and architecture.
- Do not inspect or modify .codeservo/**; those files belong to the controller.
- Do not run gates marked baseline=false. They are independent acceptance sensors owned by the controller.
- Do not commit, push, create PRs, or change Git configuration.
- Prefer the smallest coherent change.
- The controller will run the authoritative gates. You may run checks yourself, but your claims are not evidence.
- Every acceptance criterion below is decided by its id, by the control it names: a gate the controller runs, or an independent reviewer. Satisfy all of them.

ACCEPTANCE CRITERIA
===================
{_criteria(task)}

TASK
====
{task.raw_text}

ACTUATOR VIEW OF FROZEN REPOSITORY CONSTITUTION
================================================
{_actuator_constitution(constitution)}

CONTROLLER FEEDBACK FROM THE PREVIOUS ITERATION
===============================================
{_feedback_section(feedback, history)}

Work directly in the current workspace. When you have made the best correction you can, stop.
"""


def reviewer_prompt(
    task: Task, constitution: Constitution, observations_json: str
) -> str:
    return f"""You are an independent REVIEW SENSOR. You are read-only. Do not modify files.

Review the current working tree against the frozen task and constitution. Inspect the actual diff and repository as needed. Do not trust the implementer's claims.

The task states which acceptance criteria you decide. For each one listed under ACCEPTANCE CRITERIA YOU DECIDE, return exactly one criteria entry using the exact criterion id, and return an entry for no other id. Mark it:
- satisfied: concrete repository evidence demonstrates it.
- not_satisfied: the implementation contradicts or misses it.
- not_verifiable: available repository evidence is insufficient.

Report concrete defects as findings. Severity meanings:
- blocker: unsafe/corrupting or fundamentally invalid change.
- major: task or correctness defect that should prevent acceptance.
- minor: non-blocking issue.

Do not invent style findings already covered by deterministic gates. Do not return an overall verdict; the controller computes it.

CONTROLLER OBSERVATIONS
=======================
The JSON below is controller-owned deterministic evidence rather than an implementer claim: it is what the controller itself measured when it ran the quick and full gates on this exact working tree, in the order it ran them. Every gate in it passed; a failing gate would have stopped the run before this review.

Use it as deterministic runtime fact you cannot reproduce yourself, and keep reading the repository for everything it does not show. A green gate set is a floor, not a criterion: it never establishes an acceptance criterion on its own.

BEGIN CONTROLLER OBSERVATIONS JSON
{observations_json}
END CONTROLLER OBSERVATIONS JSON

ACCEPTANCE CRITERIA YOU DECIDE
==============================
{_reviewed(task)}
{_gated(task)}
TASK
====
{task.raw_text}

FROZEN REPOSITORY CONSTITUTION
==============================
{constitution.raw_text}
"""
