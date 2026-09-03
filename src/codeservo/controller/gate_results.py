"""Reading a phase of gate results: what broke, and what to say about it.

A failing gate is something the candidate can be told about. A gate that
declared a structured result and could not say what it measured is not: it is
a fault of the sensor, and nothing about it is fed back.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path

from ..domain.document import Unset
from ..evidence.journal import Journal
from ..runtime.process import tail
from ..sensors import observations
from ..sensors.gates import GateResult

# How many findings of one gate the feedback spells out. A lint gate can raise
# hundreds on one tree; the actuator is told the first of them and how many
# more there are, the way the tool's own output is told as a tail.
FINDINGS_FED_BACK = 40


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


def sensor_faults(results: Sequence[GateResult]) -> list[str]:
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


def _observed(result: GateResult) -> observations.Observation | None:
    """The document a gate wrote, when it wrote a valid one.

    The record keeps the bytes the gate produced, already held to the
    contract when the gate finished; reading them back here cannot fail on a
    shape, so it is the one place the document is parsed twice.
    """
    if result.observation_status != observations.Classification.VALID:
        return None
    if not isinstance(result.observation_path, str):
        return None
    return observations.Observation.parse(Path(result.observation_path).read_bytes())


def _finding_line(finding: observations.Finding) -> str:
    """One finding, as a place and a message the actuator can act on."""
    if finding.path is None:
        where = "(no path)"
    elif finding.line is None:
        where = finding.path
    else:
        where = f"{finding.path}:{finding.line}"
    return f"- [{finding.severity}] {where}: {finding.message}"


def observation_feedback(document: observations.Observation) -> list[str]:
    """What a gate's own document says, spelled out for the actuator.

    The summary, the findings with the place each one names, and the metrics
    come before the tool's raw output because they are what the adapter kept
    of that output. A finding carries one line of what the tool said, so the
    raw output stays behind it: a traceback is where a correction is found.
    """
    lines = [f"Summary: {document.summary}"]
    if document.findings:
        shown = document.findings[:FINDINGS_FED_BACK]
        lines.append(f"Findings ({len(document.findings)}):")
        lines.extend(_finding_line(finding) for finding in shown)
        left = len(document.findings) - len(shown)
        if left:
            lines.append(f"- ... and {left} more")
    else:
        lines.append("Findings: none")
    if document.metrics:
        rendered = " ".join(
            f"{key}={json.dumps(document.metrics[key])}"
            for key in sorted(document.metrics)
        )
        lines.append(f"Metrics: {rendered}")
    return lines


def gate_clause(result: GateResult) -> str:
    """One failing gate in one clause: its name, and what it said in a line.

    A gate that wrote a valid document is named with its summary; one that
    answered with its exit code alone, or a summary it left empty, with how
    it ended.
    """
    document = _observed(result)
    if document is not None and document.summary:
        return f"{result.name} ({document.summary})"
    if result.timed_out:
        return f"{result.name} (timed out)"
    return f"{result.name} (exit code {result.exit_code})"


def gate_reasons(
    phase: GatePhase,
    results: Sequence[GateResult],
    criteria: Mapping[str, Sequence[str]],
) -> list[str]:
    """Why a phase decided against the candidate: the gates that failed.

    A gate a criterion named is the control that decides it, so a failing one
    names what it leaves unsatisfied. The record then says which acceptance
    criterion a run stopped on, and not only which measurement.
    """
    reasons: list[str] = []
    for result in results:
        if result.passed:
            continue
        decided = criteria.get(result.name, ())
        unsatisfied = f": {', '.join(decided)} not satisfied" if decided else ""
        reasons.append(f"{phase} gate {result.name} failed{unsatisfied}")
    return reasons


def gate_feedback(
    results: Sequence[GateResult], criteria: Mapping[str, Sequence[str]]
) -> str:
    """What a failing phase tells the actuator.

    A gate that wrote a valid document is reported through that document
    first, then through the tail of what it printed. A gate answering with its
    exit code alone is reported through its output, unchanged from what it
    emitted. A gate an acceptance criterion named is reported with that
    criterion, because passing it is what the task asked for.
    """
    chunks: list[str] = []
    for result in results:
        if result.passed:
            continue
        lines = [
            f"Gate {result.name} FAILED",
            f"Command: {result.command}",
            f"Exit code: {result.exit_code}",
        ]
        decided = criteria.get(result.name, ())
        if decided:
            lines.append(
                "Acceptance criteria this gate decides: " + ", ".join(decided)
            )
        document = _observed(result)
        if document is not None:
            lines.extend(observation_feedback(document))
        lines.extend(
            [
                "stdout (tail):",
                tail(result.stdout_path),
                "stderr (tail):",
                tail(result.stderr_path),
            ]
        )
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def record_gate_events(
    journal: Journal, phase: GatePhase, results: Sequence[GateResult]
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
