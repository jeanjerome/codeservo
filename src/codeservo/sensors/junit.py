"""Test reports in JUnit XML, read from where the tool wrote them.

A gate declaring `junit-xml` names, as a glob relative to the tree it
measures, the reports its tool writes; the controller reads them and projects
them onto the observation every other consumer reads, so a suite that already
reports this way needs no adapter. Nothing here belongs to one tool: the shape
is the one Surefire, Gradle, pytest, Jest and their kin all write, a
`testsuite` of `testcase` elements, alone or under a `testsuites` root.

What is read is what this measurement wrote. The files matching the pattern
are listed before the gate runs and again after it, and a report the gate left
exactly as it found it — same size, same modification time — is not this
measurement's: a module the build skipped keeps its old report, and the run
must not count it. Nothing is deleted to make that so. The tree is read and
never written, in the source repository as in the checkout.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .observations import SCHEMA_VERSION, Finding, Observation, Severity, Status

# A report larger than this is not one a test reporter wrote.
REPORT_SIZE_LIMIT = 64 * 1024 * 1024

# The two markup constructs a report never needs and an entity attack does.
_DECLARATIONS = (b"<!DOCTYPE", b"<!ENTITY")

# A file as listed before and after a measurement: its size and the
# nanosecond it was last written.
Listing = dict[str, tuple[int, int]]


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


class ReportFault(ValueError):
    """What a file matching the pattern is not: a JUnit report."""


def list_reports(tree: Path, pattern: str) -> Listing:
    """Every file the pattern matches under the tree, with size and write time.

    A file the pattern reaches through a link leaving the tree is not under
    it and is not listed.
    """
    root = tree.resolve()
    listing: Listing = {}
    for path in sorted(tree.glob(pattern)):
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        stat = path.stat()
        listing[path.relative_to(tree).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return listing


def written_reports(
    tree: Path, pattern: str, before: Listing
) -> tuple[list[str], list[str]]:
    """The reports this measurement wrote, and those it left as it found them.

    A file is this measurement's when it did not exist before the gate ran or
    when its size or write time moved while the gate ran. One the gate never
    touched is listed second, so a caller can say it was there and not read.
    """
    after = list_reports(tree, pattern)
    written = [path for path, entry in after.items() if before.get(path) != entry]
    left = [path for path, entry in after.items() if before.get(path) == entry]
    return written, left


def _one_line(text: str | None) -> str:
    """The first non-empty line of a text, its whitespace collapsed."""
    for line in (text or "").splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed
    return ""


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
            message = _one_line(verdict.get("message")) or _one_line(verdict.text)
            if not message:
                message = _one_line(verdict.get("type")) or str(ended)
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
    if len(raw) > REPORT_SIZE_LIMIT:
        raise ReportFault(f"{where} is larger than {REPORT_SIZE_LIMIT} bytes")
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


def read_report(tree: Path, relative: str) -> Report:
    return parse_report((tree / relative).read_bytes(), relative)


def _unique(identifier: str, taken: set[str]) -> str:
    """The identifier, or the first numbered variant nobody holds yet.

    A scenario a feature declares twice yields two cases of one name, and a
    finding is one thing seen once.
    """
    candidate = identifier
    ordinal = 2
    while candidate in taken:
        candidate = f"{identifier}#{ordinal}"
        ordinal += 1
    taken.add(candidate)
    return candidate


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
            id=_unique(f"{case.classname}.{case.name}".strip("."), taken),
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


def render(observation: Observation) -> bytes:
    """The document as the record keeps it: one canonical JSON text."""
    return (
        json.dumps(observation.to_document(), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
