"""Coverage tracefiles in LCOV, projected onto the observation contract.

A gate declaring `lcov` names, as a glob relative to the tree it measures, the
tracefiles its tool writes; `reports.py` finds the ones this measurement
produced and this module reads them. LCOV is the line-oriented tracefile
`lcov` defined and that coverage.py, Jest, c8, nyc, cargo-llvm-cov, gcovr and
their kin all write, so a suite already measuring coverage needs no adapter.

Read on coverage.py 7.16.0 rather than assumed: `SF:` names the source file
relative to where the tool ran, `DA:<line>,<count>` carries no checksum,
`BRDA:<line>,<block>,<branch>,<taken>` writes `-` for a branch never taken and
a branch identifier that is a phrase rather than a number, `FN:` carries three
fields where older producers write two, every record ends with
`end_of_record`, and the summary lines a record may carry — `LF`, `LH`, `BRF`,
`BRH`, `FNF`, `FNH` — reproduce exactly the records they summarise and are
absent for a family the file has no record of. The counts here are therefore
the records and never those summaries: what a tool measured is what it listed,
and a total is something this reader can compute rather than something it has
to be told.

Two readings matter more than the counts. A tracefile ending inside a record
is one its tool was still writing when it died, and reading it would report a
coverage taken over part of the tree as if it were the whole; that is refused.
And what the same file says twice is one measurement, not two: LCOV merges a
line, a branch and a function by summing what each record says of it, so a
tracefile holding two records for one file, or two tracefiles covering it,
count it once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .observations import SCHEMA_VERSION, Finding, Observation, Severity, Status
from .reports import ReportFault, read_report, unique

# What ends one source file's record. A tracefile that stops before one is a
# tracefile its tool did not finish.
RECORD_END = "end_of_record"

# What a branch that was never reached is written as, where a count would be.
NEVER_TAKEN = "-"


@dataclass(frozen=True)
class FileCoverage:
    """What one record says about one source file, before anything is merged.

    The three mappings are keyed by what LCOV identifies each thing with, so
    merging two records of the same file is summing them key by key.
    """

    declared: str
    path: str | None
    lines: dict[int, int] = field(default_factory=dict)
    branches: dict[str, int] = field(default_factory=dict)
    functions: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    """One tracefile, record by record and in the order it wrote them."""

    path: str
    files: tuple[FileCoverage, ...]


@dataclass(frozen=True)
class Counted:
    """How much of one family was instrumented, and how much of it was reached."""

    found: int
    covered: int

    @property
    def missing(self) -> int:
        return self.found - self.covered

    @property
    def percent(self) -> float | None:
        """The share reached, or nothing to report where nothing was found."""
        return round(100.0 * self.covered / self.found, 2) if self.found else None


def _source_path(declared: str, tree: Path) -> str | None:
    """Where a record's source file is, as a path under the measured tree.

    A tool naming its files relative to where it ran has already written what
    the record wants. One naming them absolutely is read against the tree, and
    a file outside it is counted like any other while pointing nowhere: the
    totals are what the tool measured, and a finding points only where the
    record points inside the tree.
    """
    if not declared:
        return None
    if PurePosixPath(declared).is_absolute():
        try:
            return Path(declared).resolve().relative_to(tree.resolve()).as_posix()
        except ValueError:
            return None
    if ".." in PurePosixPath(declared).parts:
        return None
    return PurePosixPath(declared).as_posix()


def _count(value: str, what: str, where: str, number: int) -> int:
    """One execution count, a value that is not one being refused.

    A count decides how much was covered, so a line carrying something else is
    a tracefile this reader cannot count rather than one it reads past.
    """
    if value == NEVER_TAKEN:
        return 0
    try:
        return max(int(value), 0)
    except ValueError:
        raise ReportFault(
            f"{where}:{number} names no execution count for {what}: {value!r}"
        ) from None


class _Record:
    """One record being read, from its `SF:` line to `end_of_record`."""

    def __init__(self, declared: str) -> None:
        self.declared = declared
        self.lines: dict[int, int] = {}
        self.branches: dict[str, int] = {}
        self.functions: dict[str, int] = {}

    def read(self, prefix: str, value: str, where: str, number: int) -> None:
        """One line of the record, or nothing where it says nothing counted.

        A prefix this reader does not know is a field the format grew and
        this reading does not count, so it is passed over rather than refused.
        """
        if prefix == "DA":
            self._line(value, where, number)
        elif prefix == "BRDA":
            self._branch(value, where, number)
        elif prefix == "FN":
            self._declare(value, where, number)
        elif prefix == "FNDA":
            self._call(value, where, number)

    def _line(self, value: str, where: str, number: int) -> None:
        # `DA:<line>,<count>` and, where a producer writes one, a checksum
        # after it, which says nothing about what was executed.
        parts = value.split(",")
        if len(parts) < 2:
            raise ReportFault(f"{where}:{number} names no line and count: DA:{value}")
        line = _count(parts[0].strip(), "a line number", where, number)
        self.lines[line] = self.lines.get(line, 0) + _count(
            parts[1].strip(), "a line", where, number
        )

    def _branch(self, value: str, where: str, number: int) -> None:
        # `BRDA:<line>,<block>,<branch>,<taken>`, where the branch is an
        # identifier a producer may spell as a phrase, so the count is read
        # from the end and the identifier is whatever lies before it.
        head, separator, taken = value.rpartition(",")
        if not separator:
            raise ReportFault(f"{where}:{number} names no branch: BRDA:{value}")
        self.branches[head.strip()] = self.branches.get(head.strip(), 0) + _count(
            taken.strip(), "a branch", where, number
        )

    def _declare(self, value: str, where: str, number: int) -> None:
        # `FN:<line>,<name>`, and `FN:<start>,<end>,<name>` since lcov 2. The
        # name is what identifies the function either way.
        name = value.rpartition(",")[2].strip()
        if not name:
            raise ReportFault(f"{where}:{number} names no function: FN:{value}")
        self.functions.setdefault(name, 0)

    def _call(self, value: str, where: str, number: int) -> None:
        # `FNDA:<count>,<name>`.
        count, separator, name = value.partition(",")
        if not separator or not name.strip():
            raise ReportFault(f"{where}:{number} names no function: FNDA:{value}")
        self.functions[name.strip()] = self.functions.get(name.strip(), 0) + _count(
            count.strip(), "a function", where, number
        )

    def measured(self, tree: Path) -> FileCoverage:
        return FileCoverage(
            declared=self.declared,
            path=_source_path(self.declared, tree),
            lines=self.lines,
            branches=self.branches,
            functions=self.functions,
        )


def parse_report(raw: bytes, where: str, *, tree: Path) -> Report:
    """One tracefile as its tool wrote it, or what makes the file not one.

    A refusal names the file, the line and what was expected of it, because a
    tracefile the reader cannot make sense of is a fault of the measurement,
    never a verdict on the candidate.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ReportFault(f"{where} is not valid UTF-8") from None
    files: list[FileCoverage] = []
    current: _Record | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        entry = line.strip()
        if not entry:
            continue
        if entry == RECORD_END:
            if current is None:
                raise ReportFault(f"{where}:{number} ends a record none began")
            files.append(current.measured(tree))
            current = None
            continue
        prefix, separator, value = entry.partition(":")
        if prefix == "SF" and separator:
            if current is not None:
                raise ReportFault(
                    f"{where}:{number} begins a record while the one for"
                    f" {current.declared} has not ended"
                )
            current = _Record(value.strip())
        elif current is not None and separator:
            current.read(prefix, value, where, number)
    if current is not None:
        raise ReportFault(
            f"{where} ends inside the record for {current.declared},"
            " so its tool did not finish writing it"
        )
    if not files:
        raise ReportFault(f"{where} names no source file, so it measured nothing")
    return Report(path=where, files=tuple(files))


def merged(reports: list[Report]) -> dict[str, FileCoverage]:
    """Every source file the reports cover, each counted once.

    LCOV merges what two records say about one line, one branch or one
    function by summing them, so a file two records or two tracefiles both
    cover is one file covered by both and never two files.
    """
    files: dict[str, FileCoverage] = {}
    for report in reports:
        for measured in report.files:
            held = files.get(measured.declared)
            if held is None:
                files[measured.declared] = FileCoverage(
                    declared=measured.declared,
                    path=measured.path,
                    lines=dict(measured.lines),
                    branches=dict(measured.branches),
                    functions=dict(measured.functions),
                )
                continue
            for key, count in measured.lines.items():
                held.lines[key] = held.lines.get(key, 0) + count
            for name, count in measured.branches.items():
                held.branches[name] = held.branches.get(name, 0) + count
            for name, count in measured.functions.items():
                held.functions[name] = held.functions.get(name, 0) + count
    return files


def _counted(counts: list[dict]) -> Counted:
    found = sum(len(entry) for entry in counts)
    covered = sum(1 for entry in counts for count in entry.values() if count > 0)
    return Counted(found=found, covered=covered)


def project(
    reports: list[Report], *, sensor: str, passed: bool, pattern: str, left: int
) -> Observation:
    """The tracefiles, as the document every reader of an observation expects.

    The status is the verdict the exit code reached, because the controller
    wrote this document and contradicts nothing it decided. The three families
    are counted whether or not the tool instrumented them, so a ratchet on
    what was found catches instrumentation being turned off, and a share is
    reported only where something was found to divide by. A file the tool
    instrumented and no test reached at all is a finding, which needs no
    threshold to state.
    """
    files = merged(reports)
    lines = _counted([entry.lines for entry in files.values()])
    branches = _counted([entry.branches for entry in files.values()])
    functions = _counted([entry.functions for entry in files.values()])
    taken: set[str] = set()
    findings = tuple(
        Finding(
            id=unique(f"uncovered:{entry.path or entry.declared}", taken),
            severity=Severity.INFO,
            path=entry.path,
            line=None,
            message=(
                f"no line covered of {len(entry.lines)} instrumented"
                f" in {entry.declared}"
            ),
        )
        for entry in sorted(files.values(), key=lambda entry: entry.declared)
        if entry.lines and not any(count > 0 for count in entry.lines.values())
    )

    metrics: dict[str, float] = {
        "files": len(files),
        "lines": lines.found,
        "lines_covered": lines.covered,
        "lines_missing": lines.missing,
        "branches": branches.found,
        "branches_covered": branches.covered,
        "branches_missing": branches.missing,
        "functions": functions.found,
        "functions_covered": functions.covered,
        "functions_missing": functions.missing,
    }
    for name, counted in (
        ("line_coverage", lines),
        ("branch_coverage", branches),
        ("function_coverage", functions),
    ):
        if counted.percent is not None:
            metrics[name] = counted.percent

    if not reports:
        summary = f"no coverage report matching {pattern} was written"
    else:
        stated = [
            f"{counted.percent:.2f} percent of {counted.found} {what}"
            for what, counted in (
                ("lines", lines),
                ("branches", branches),
                ("functions", functions),
            )
            if counted.percent is not None
        ]
        measured = ", ".join(stated) if stated else "nothing instrumented"
        summary = (
            f"{measured} over {len(files)} file{'s' if len(files) != 1 else ''}"
            f" in {len(reports)} report{'s' if len(reports) != 1 else ''}"
        )
    if left:
        summary += f"; {left} left from an earlier measurement, not read"
    return Observation(
        schema_version=SCHEMA_VERSION,
        sensor=sensor,
        status=Status.PASSED if passed else Status.FAILED,
        summary=summary,
        findings=findings,
        metrics=metrics,
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
    """Read every tracefile this measurement wrote, and project them as one."""
    reports = [
        parse_report(read_report(tree, relative), relative, tree=tree)
        for relative in written
    ]
    return project(reports, sensor=sensor, passed=passed, pattern=pattern, left=left)
