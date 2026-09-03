"""Test reports in JUnit XML, projected onto the observation contract.

A gate declaring `junit-xml` names, as a glob relative to the tree it
measures, the reports its tool writes; `reports.py` finds the ones this
measurement produced and this module reads them, so a suite that already
reports this way needs no adapter. Nothing here belongs to one tool: the shape
is the one Surefire, Gradle, pytest, Jest and their kin all write, a
`testsuite` of `testcase` elements, alone or under a `testsuites` root.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .observations import SCHEMA_VERSION, Finding, Observation, Severity, Status
from .reports import ReportFault, one_line, read_report, unique

# The two markup constructs a report never needs and an entity attack does.
_DECLARATIONS = (b"<!DOCTYPE", b"<!ENTITY")


class Outcome(StrEnum):
    """What one test case's element says happened to it."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Case:
    """One `testcase` element: who it is, how it ended, where it points."""

    classname: str
    name: str
    outcome: Outcome
    message: str
    path: str | None
    line: int | None


@dataclass(frozen=True)
class Report:
    """One report file, with the seconds its suites declare and their cases."""

    path: str
    seconds: float
    cases: tuple[Case, ...]


def _location(element: ElementTree.Element) -> tuple[str | None, int | None]:
    """Where a case points, when its element says so and says it in the tree."""
    path = element.get("file")
    if path is not None:
        path = path.strip()
        if not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
            path = None
    line: int | None = None
    declared = element.get("line")
    if declared is not None and declared.strip().isdigit() and int(declared) >= 1:
        line = int(declared)
    return path, line


def _case(element: ElementTree.Element) -> Case:
    outcome = Outcome.PASSED
    message = ""
    for tag, ended in (
        ("failure", Outcome.FAILED),
        ("error", Outcome.ERROR),
        ("skipped", Outcome.SKIPPED),
    ):
        verdict = element.find(tag)
        if verdict is not None:
            outcome = ended
            message = one_line(verdict.get("message")) or one_line(verdict.text)
            if not message:
                message = one_line(verdict.get("type")) or str(ended)
            break
    path, line = _location(element)
    return Case(
        classname=(element.get("classname") or "").strip(),
        name=(element.get("name") or "").strip(),
        outcome=outcome,
        message=message,
        path=path,
        line=line,
    )


def _seconds(suite: ElementTree.Element) -> float:
    declared = (suite.get("time") or "").strip()
    try:
        return max(float(declared), 0.0) if declared else 0.0
    except ValueError:
        return 0.0


def parse_report(raw: bytes, where: str) -> Report:
    """One report as its tool wrote it, or what makes the file not one.

    A refusal names the file and what was expected of it, because a report
    the reader cannot make sense of is a fault of the measurement, never a
    verdict on the candidate.
    """
    if any(marker in raw for marker in _DECLARATIONS):
        raise ReportFault(f"{where} declares a DTD or an entity, which no report does")
    try:
        root = ElementTree.fromstring(raw)  # noqa: S314 — no DTD, no entity, bounded
    except ElementTree.ParseError as exc:
        raise ReportFault(f"{where} is not well-formed XML: {exc}") from None
    if root.tag not in ("testsuite", "testsuites"):
        raise ReportFault(
            f"{where} is not a JUnit report: its root element is {root.tag}"
        )
    suites = list(root.iter("testsuite"))
    return Report(
        path=where,
        seconds=round(sum(_seconds(suite) for suite in suites), 3),
        cases=tuple(
            _case(case) for suite in suites for case in suite.findall("testcase")
        ),
    )


def project(
    reports: list[Report], *, sensor: str, passed: bool, pattern: str, left: int
) -> Observation:
    """The reports, as the document every reader of an observation expects.

    The status is the verdict the exit code reached, because the controller
    wrote this document and contradicts nothing it decided. The counts are
    what the reports list, case by case, and every failed or errored case is
    a finding naming the case and what its tool said in one line.
    """
    cases = [case for report in reports for case in report.cases]
    counted = dict.fromkeys(Outcome, 0)
    for case in cases:
        counted[case.outcome] += 1
    taken: set[str] = set()
    findings = tuple(
        Finding(
            id=unique(f"{case.classname}.{case.name}".strip("."), taken),
            severity=Severity.MAJOR,
            path=case.path,
            line=case.line,
            message=f"{case.outcome}: {case.message}",
        )
        for case in cases
        if case.outcome in (Outcome.FAILED, Outcome.ERROR)
    )
    if reports:
        summary = (
            f"{len(cases)} tests, {counted[Outcome.FAILED]} failures,"
            f" {counted[Outcome.ERROR]} errors, {counted[Outcome.SKIPPED]} skipped"
            f" in {len(reports)} report{'s' if len(reports) != 1 else ''}"
        )
    else:
        summary = f"no test report matching {pattern} was written"
    if left:
        summary += f"; {left} left from an earlier measurement, not read"
    return Observation(
        schema_version=SCHEMA_VERSION,
        sensor=sensor,
        status=Status.PASSED if passed else Status.FAILED,
        summary=summary,
        findings=findings,
        metrics={
            "tests": len(cases),
            "failures": counted[Outcome.FAILED],
            "errors": counted[Outcome.ERROR],
            "skipped": counted[Outcome.SKIPPED],
            "seconds": round(sum(report.seconds for report in reports), 3),
        },
    )


def projection(
    tree: Path,
    written: list[str],
    *,
    sensor: str,
    passed: bool,
    pattern: str,
    left: int,
) -> Observation:
    """Read every report this measurement wrote, and project them as one."""
    reports = [
        parse_report(read_report(tree, relative), relative) for relative in written
    ]
    return project(reports, sensor=sensor, passed=passed, pattern=pattern, left=left)
