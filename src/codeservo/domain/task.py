"""What one run is asked to change, and what decides each acceptance criterion.

A criterion is an id, a statement about repository state, and the control
that answers it. Left implicit, that control is the reviewer, which inherits
every criterion including the ones a gate already measures. Naming it in the
criterion keeps the two apart: a criterion a gate decides is answered by a
measurement, and the reviewer is never asked for an opinion on it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

CRITERION_RE = re.compile(r"^\s*-\s*\[([A-Z][A-Z0-9_-]*)\]\s+(.+?)\s*$")

# The verification a criterion names, at the end of its line and nowhere
# else: `{review}` or `{gate: unit}`. What precedes it is the criterion.
VERIFICATION_RE = re.compile(r"^(?P<text>.*?)\s*\{(?P<body>[^{}]*)\}$")
GATE_RE = re.compile(r"^gate\s*:\s*(?P<gate>.*)$")
# A trailing group opening on one of the two words was meant to be a
# verification, so a spelling of one this reader does not know is refused
# rather than kept as criterion text and quietly left to the reviewer.
VERIFICATION_WORD_RE = re.compile(r"^(gate|review)\b", re.IGNORECASE)


class Verification(StrEnum):
    """What decides one acceptance criterion.

    A gate measures it, or the review sensor is asked about it. A criterion
    naming neither is reviewed, which is what every criterion written before
    the distinction existed already meant.
    """

    GATE = "gate"
    REVIEW = "review"


@dataclass(frozen=True)
class Criterion:
    """One acceptance criterion, and the control that decides it."""

    id: str
    text: str
    # The gate that decides it, or nothing when the reviewer does.
    gate: str | None = None

    @property
    def verification(self) -> Verification:
        return Verification.REVIEW if self.gate is None else Verification.GATE


@dataclass(frozen=True)
class Task:
    path: Path
    raw_text: str
    criteria: dict[str, Criterion]


class TaskError(ValueError):
    pass


def reviewed_criteria(criteria: Mapping[str, Criterion]) -> dict[str, Criterion]:
    """The criteria the review sensor is asked to decide, in the task's order."""
    return {
        criterion_id: criterion
        for criterion_id, criterion in criteria.items()
        if criterion.verification == Verification.REVIEW
    }


def criteria_by_gate(criteria: Mapping[str, Criterion]) -> dict[str, tuple[str, ...]]:
    """Which criteria each gate decides, keyed by the gate they name.

    A gate a criterion names is one the constitution has to declare, and
    nothing here knows what it declares: this says what the task asked of
    each name, and the run holds the two to each other.
    """
    gated: dict[str, tuple[str, ...]] = {}
    for criterion_id, criterion in criteria.items():
        if criterion.gate is not None:
            gated[criterion.gate] = (*gated.get(criterion.gate, ()), criterion_id)
    return gated


def _no_gate(criterion_id: str) -> TaskError:
    return TaskError(
        f"criterion {criterion_id} names no gate to verify it:"
        f" write {{{Verification.GATE}: <gate>}}"
    )


def _verification(criterion_id: str, stated: str) -> tuple[str, str | None]:
    """One criterion line, split into what it states and what verifies it."""
    match = VERIFICATION_RE.match(stated)
    if match is None:
        return stated, None
    body = match.group("body").strip()
    named = GATE_RE.match(body)
    if named is not None:
        gate = named.group("gate").strip()
        if not gate:
            raise _no_gate(criterion_id)
        return match.group("text").strip(), gate
    if body in Verification:
        if Verification(body) == Verification.REVIEW:
            return match.group("text").strip(), None
        raise _no_gate(criterion_id)
    if VERIFICATION_WORD_RE.match(body):
        raise TaskError(
            f"criterion {criterion_id}: {{{body}}} is not a verification:"
            f" write {{{Verification.REVIEW}}} or {{{Verification.GATE}: <gate>}}"
        )
    # Braces the criterion itself carries, and the reviewer decides it.
    return stated, None


def _criterion(criterion_id: str, stated: str) -> Criterion:
    """One criterion, and the single verification it is allowed to name."""
    text, gate = _verification(criterion_id, stated)
    # A verification behind the one just read would be left in the criterion
    # text, where it reads as a declaration and decides nothing.
    if text != stated and _verification(criterion_id, text)[0] != text:
        raise TaskError(f"criterion {criterion_id} names two verifications")
    if not text:
        raise TaskError(f"criterion {criterion_id} states nothing")
    return Criterion(id=criterion_id, text=text, gate=gate)


def load_task(path: Path) -> Task:
    if not path.is_file():
        raise TaskError(f"task file does not exist: {path}")
    raw = path.read_text(encoding="utf-8")
    criteria: dict[str, Criterion] = {}
    for line in raw.splitlines():
        match = CRITERION_RE.match(line)
        if not match:
            continue
        criterion_id, stated = match.groups()
        if criterion_id in criteria:
            raise TaskError(f"duplicate acceptance criterion: {criterion_id}")
        criteria[criterion_id] = _criterion(criterion_id, stated)
    if not criteria:
        raise TaskError("task must contain at least one '- [AC1] ...' acceptance criterion")
    return Task(path=path, raw_text=raw, criteria=criteria)
