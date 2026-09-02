"""Reading a phase of gate results: what broke, and what to say about it.

A failing gate is something the candidate can be told about. A gate that
declared a structured result and could not say what it measured is not: it is
a fault of the sensor, and nothing about it is fed back.
"""

from __future__ import annotations

from enum import StrEnum

from ..domain.document import Unset
from ..evidence.journal import Journal
from ..runtime.process import tail
from ..sensors import observations
from ..sensors.gates import GateResult


class GatePhase(StrEnum):
    """Which measurement of a run one gate belonged to.

    The baseline measures the source repository before a candidate exists,
    and the two phases the constitution declares measure the candidate. An
    event names the occasion a gate was measured on rather than the phase a
    gate declares, so the baseline sits here beside the two `Phase` holds.
    """

    BASELINE = "baseline"
    QUICK = "quick"
    FULL = "full"


def sensor_faults(results: list[GateResult]) -> list[str]:
    """Gates whose document is a fault of the sensor rather than of the candidate.

    A gate that declared a structured result and could not say what it measured
    is broken: the exit code it also returned describes nothing anyone can act
    on, so it is never fed back as something to fix. The one thing a timeout
    excuses is a document that was never written; a document already written
    is judged like any other, whether or not the gate then ran out of time.
    """
    faults: list[str] = []
    for result in results:
        status = result.observation_status
        if isinstance(status, Unset) or status == observations.Classification.VALID:
            continue
        if status == observations.Classification.ABSENT and result.timed_out:
            continue
        faults.append(
            f"sensor error: gate {result.name}: {result.observation_error}"
        )
    return faults


def gate_feedback(results: list[GateResult]) -> str:
    """What a failing phase tells the actuator, unchanged from what it emitted."""
    chunks: list[str] = []
    for result in results:
        if result.passed:
            continue
        chunks.append(
            "\n".join(
                [
                    f"Gate {result.name} FAILED",
                    f"Command: {result.command}",
                    f"Exit code: {result.exit_code}",
                    "stdout (tail):",
                    tail(result.stdout_path),
                    "stderr (tail):",
                    tail(result.stderr_path),
                ]
            )
        )
    return "\n\n".join(chunks)


def record_gate_events(
    journal: Journal, phase: GatePhase, results: list[GateResult]
) -> None:
    """One event per gate of one phase, in the order the phase measured them."""
    for result in results:
        journal.record(
            "gate.finished",
            {
                "phase": phase,
                "name": result.name,
                "passed": result.passed,
                "result_sha256": result.result_sha256,
            },
        )
