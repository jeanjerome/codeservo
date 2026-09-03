"""Holding the candidate's measurements to the baseline's, metric by metric.

A ratchet is a rule over two documents of one gate: what the gate wrote about
the source tree at the baseline, and what it wrote about the candidate. The
controller holds both already, so the comparison is a policy over
observations it owns, and no adapter has to reconstruct the state before the
change to compare against it. The exit code stays the gate's verdict; a
ratchet is the controller's own reading of what a passing gate reported.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.constitution import Direction, Gate
from ..sensors.gates import GateResult
from .gate_results import GatePhase, observed


@dataclass(frozen=True)
class BrokenRatchet:
    """One metric that moved against the direction its gate declared."""

    gate: str
    metric: str
    direction: Direction
    baseline: float
    candidate: float


def holds(direction: Direction, baseline: float, candidate: float) -> bool:
    """Whether a value moved the way a ratchet allows. An unchanged one always did."""
    if direction == Direction.AT_MOST:
        return candidate <= baseline
    return candidate >= baseline


def broken_ratchets(
    gates: Sequence[Gate],
    baseline: Sequence[GateResult],
    candidate: Sequence[GateResult],
) -> list[BrokenRatchet]:
    """Every declared ratchet the candidate broke, in the constitution's order.

    A ratchet is read over a gate that passed: a failing gate has already
    decided against the candidate, and its document describes a different
    amount of work. It is silent when either document lacks the metric,
    because comparing with a value nobody measured would be a verdict no
    measurement produced. That silence is safe only while the adapter writing
    the metric is a protected path, which is the constitution's to declare.
    """
    reference = {result.name: observed(result) for result in baseline}
    measured = {result.name: result for result in candidate}
    broken: list[BrokenRatchet] = []
    for gate in gates:
        result = measured.get(gate.name)
        if not gate.ratchets or result is None or not result.passed:
            continue
        before = reference.get(gate.name)
        after = observed(result)
        if before is None or after is None:
            continue
        for ratchet in gate.ratchets:
            if ratchet.metric not in before.metrics or ratchet.metric not in after.metrics:
                continue
            was = before.metrics[ratchet.metric]
            now = after.metrics[ratchet.metric]
            if not holds(ratchet.direction, was, now):
                broken.append(
                    BrokenRatchet(
                        gate=gate.name,
                        metric=ratchet.metric,
                        direction=ratchet.direction,
                        baseline=was,
                        candidate=now,
                    )
                )
    return broken


def _number(value: float) -> str:
    """A metric as the document spelled it: an integer stays one."""
    return json.dumps(value)


def _movement(broken: BrokenRatchet) -> str:
    """Both values and the direction, which is all a correction needs."""
    return (
        f"{broken.metric} {_number(broken.candidate)} on the candidate,"
        f" {_number(broken.baseline)} on the baseline, must be {broken.direction}"
    )


def ratchet_reasons(phase: GatePhase, broken: Sequence[BrokenRatchet]) -> list[str]:
    """Why a phase decided against a candidate every gate of it let through."""
    return [f"{phase} gate {item.gate} ratchet broken: {_movement(item)}" for item in broken]


def ratchet_feedback(broken: Sequence[BrokenRatchet]) -> str:
    """What a broken ratchet tells the actuator, one block per gate."""
    chunks: list[str] = []
    for gate in dict.fromkeys(item.gate for item in broken):
        lines = [f"Gate {gate} passed but broke a ratchet"]
        lines.extend(f"- {_movement(item)}" for item in broken if item.gate == gate)
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def ratchet_clause(broken: Sequence[BrokenRatchet]) -> str:
    """The broken ratchets of one phase, in the clause a recap line carries."""
    return "ratchet broken: " + ", ".join(
        f"{item.gate} {item.metric} {_number(item.candidate)}"
        f" vs {_number(item.baseline)}"
        for item in broken
    )
