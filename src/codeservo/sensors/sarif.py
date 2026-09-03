"""Static analysis results in SARIF, projected onto the observation contract.

A gate declaring `sarif` names, as a glob relative to the tree it measures,
the files its tool writes; `reports.py` finds the ones this measurement
produced and this module reads them. SARIF is the OASIS interchange format
that linters, type checkers and security scanners emit, so a tool already
speaking it needs no adapter in the target repository.

Read on ruff 0.12.12 rather than assumed: the document declares
`version: "2.1.0"`, carries one entry in `runs`, names its tool under
`tool.driver`, gives every result an explicit `level` and an absolute
`file://` URI, declares no `defaultConfiguration` on any rule, and on a clean
tree writes a valid document with an empty `results` array and exit code 0.
The rest of what this reader handles is in the format and not in that one
producer: a document may hold several runs, a result may omit its level, name
a relative URI, carry no location at all, be suppressed, or report something
other than a failure. Each of those is read the way the specification defines
it, and a document whose version this reader was never measured against is
refused rather than guessed at.

One reading matters more than the counts. A tool that died halfway writes the
same empty `results` array as a clean tree, and the difference is
`invocations[].executionSuccessful`. A gate reporting no finding because its
tool never finished is a green that measured nothing, so a run that says so is
a fault of the measurement and not a verdict on the candidate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from .observations import SCHEMA_VERSION, Finding, Observation, Severity, Status
from .reports import ReportFault, one_line, read_report, unique

# The one version this reader was measured against. A document declaring
# another one is refused by name: reading a shape nobody checked would report
# counts that no measurement produced.
VERSION = "2.1.0"

# The kind of result that is a failure. A result declaring another one is the
# tool reporting something it did not fail on, and is not counted.
FAILURE_KIND = "fail"

# What a result means when neither it nor its rule declares a level. The
# specification's default, not this reader's choice.
DEFAULT_LEVEL = "warning"

# A suppression a tool marks rejected is one it did not apply.
REJECTED = "rejected"


class Level(StrEnum):
    """How severe a result its tool says one of its findings is."""

    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"
    NONE = "none"


# What each level is in the vocabulary an observation carries. The observation
# severity describes; the gate's exit code is what decides.
SEVERITY = {
    Level.ERROR: Severity.MAJOR,
    Level.WARNING: Severity.MINOR,
    Level.NOTE: Severity.INFO,
    Level.NONE: Severity.INFO,
}


@dataclass(frozen=True)
class Result:
    """One result a tool reported, and where in the tree it points."""

    rule: str
    level: Level
    message: str
    path: str | None
    line: int | None


@dataclass(frozen=True)
class Report:
    """One report file: the tools that wrote it, and what they reported."""

    path: str
    tools: tuple[str, ...]
    results: tuple[Result, ...]
    suppressed: int


def _refuse_constant(name: str) -> Any:
    """Refuse the three constants Python accepts and JSON does not define."""
    raise ValueError(f"{name} is not a JSON value")


def _mapping(value: Any) -> dict:
    """One object, an absent or wrongly typed one being an empty object."""
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list:
    """One array, an absent or wrongly typed one being an empty array."""
    return value if isinstance(value, list) else []


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _uri_path(uri: str, tree: Path) -> str | None:
    """Where a location's URI points, as a path under the measured tree.

    A `file://` URI is what the one producer measured here writes, and it is
    absolute, so it is read against the tree the gate measured and dropped
    when it falls outside it. A relative URI is already what the record wants,
    whether or not a `uriBaseId` names the root it is relative to, since every
    base a tool declares for a source file is the tree itself. Anything else
    names no file of this tree, and the finding keeps its rule and its message
    without pointing anywhere.
    """
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost"):
            return None
        raw = unquote(parsed.path)
    elif parsed.scheme:
        return None
    else:
        raw = unquote(uri)
    if not raw.strip():
        return None
    if PurePosixPath(raw).is_absolute():
        try:
            return Path(raw).resolve().relative_to(tree.resolve()).as_posix()
        except ValueError:
            return None
    if ".." in PurePosixPath(raw).parts:
        return None
    return PurePosixPath(raw).as_posix()


def _location(result: dict, tree: Path) -> tuple[str | None, int | None]:
    """The first location a result names, when it names one inside the tree."""
    for location in _sequence(result.get("locations")):
        physical = _mapping(_mapping(location).get("physicalLocation"))
        artifact = _mapping(physical.get("artifactLocation"))
        path = _uri_path(_string(artifact.get("uri")), tree)
        if path is None:
            continue
        line: int | None = None
        declared = _mapping(physical.get("region")).get("startLine")
        if (
            isinstance(declared, int)
            and not isinstance(declared, bool)
            and declared >= 1
        ):
            line = declared
        return path, line
    return None, None


def _rules(driver: dict) -> tuple[list[dict], dict[str, dict]]:
    """The tool's rules, by the two ways a result reaches one of them."""
    listed = [_mapping(rule) for rule in _sequence(driver.get("rules"))]
    return listed, {
        _string(rule.get("id")): rule for rule in listed if _string(rule.get("id"))
    }


def _rule_of(result: dict, listed: list[dict], by_id: dict[str, dict]) -> dict:
    index = result.get("ruleIndex")
    if (
        isinstance(index, int)
        and not isinstance(index, bool)
        and 0 <= index < len(listed)
    ):
        return listed[index]
    return by_id.get(_string(result.get("ruleId")), {})


def _identifier(result: dict, rule: dict) -> str:
    """What to call the rule a result fired, whichever field names it."""
    return _string(result.get("ruleId")) or _string(rule.get("id")) or "result"


def _level(result: dict, rule: dict) -> Level:
    """How severe a result is: its own level, its rule's, or the default.

    The specification defines that order and that default, and a level no
    version of it defines is read as the default rather than dropped.
    """
    declared = _string(result.get("level"))
    if declared not in Level:
        configuration = _mapping(rule.get("defaultConfiguration"))
        declared = _string(configuration.get("level"))
    return Level(declared) if declared in Level else Level(DEFAULT_LEVEL)


def _suppressed(result: dict) -> bool:
    """Whether the tool applied a suppression to a result it would have raised.

    A suppression the tool marks rejected is one it did not apply, and its
    result is a finding like any other. A suppression saying nothing about its
    status was applied, which is the specification's default.
    """
    return any(
        _string(_mapping(entry).get("status")) != REJECTED
        for entry in _sequence(result.get("suppressions"))
    )


def _message(result: dict, rule: dict) -> str:
    """What the tool said about one result, in one line."""
    message = _mapping(result.get("message"))
    return (
        one_line(message.get("text") if isinstance(message.get("text"), str) else None)
        or one_line(
            message.get("markdown")
            if isinstance(message.get("markdown"), str)
            else None
        )
        or one_line(_string(_mapping(rule.get("shortDescription")).get("text")))
        or "no message"
    )


def _tool(driver: dict) -> str:
    """The tool that wrote one run, as it names itself."""
    name = _string(driver.get("name"))
    version = _string(driver.get("version"))
    return f"{name} {version}".strip() if name else ""


def _finished(run: dict, where: str) -> None:
    """Refuse a run its own tool says did not complete.

    An unfinished run reports the results it reached, which is not the same
    set as the results there are: an empty one then means nothing, and reading
    it as a clean tree is the green that measures nothing.
    """
    for invocation in _sequence(run.get("invocations")):
        if _mapping(invocation).get("executionSuccessful") is False:
            raise ReportFault(
                f"{where} reports a tool run that did not complete,"
                " so what it did not find says nothing"
            )


def _results_of(run: dict, where: str, index: int) -> list:
    """The results one run reports, an array of something else being refused.

    A run reporting none is a tool that found nothing, and says so by leaving
    the array out or leaving it empty. A run whose results are not an array is
    a document this reader cannot count, and counting zero of them would be a
    verdict no measurement produced.
    """
    listed = run.get("results")
    if listed is None:
        return []
    if not isinstance(listed, list):
        raise ReportFault(
            f"{where} names results that are not an array: runs[{index}].results"
        )
    return listed


def _runs_of(raw: bytes, where: str) -> list:
    """The runs one file holds, or what makes the file not a SARIF log.

    Everything refused here is refused about the document as a whole: what it
    is encoded in, whether it is JSON, whether it is an object, which version
    of the format it declares, and whether it names the array every reading
    below walks.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ReportFault(f"{where} is not valid UTF-8") from None
    try:
        document = json.loads(text, parse_constant=_refuse_constant)
    except ValueError as exc:
        raise ReportFault(f"{where} is not JSON: {exc}") from None
    if not isinstance(document, dict):
        raise ReportFault(f"{where} is not a SARIF log: it is not a JSON object")
    version = _string(document.get("version"))
    if version != VERSION:
        raise ReportFault(
            f"{where} declares SARIF version {version or 'none'},"
            f" and this reader was measured against {VERSION}"
        )
    if not isinstance(document.get("runs"), list):
        raise ReportFault(f"{where} is not a SARIF log: it names no array of runs")
    return document["runs"]


def _run_of(entry: Any, where: str, index: int) -> dict:
    """One run of the array, an entry that is not one being refused."""
    if not isinstance(entry, dict):
        raise ReportFault(f"{where} names a run that is not an object: runs[{index}]")
    _finished(entry, where)
    return entry


def _read_result(result: dict, listed: list[dict], by_id: dict[str, dict], tree: Path):
    """One result, with as much of itself as its own fields carry."""
    rule = _rule_of(result, listed, by_id)
    path, line = _location(result, tree)
    return Result(
        rule=_identifier(result, rule),
        level=_level(result, rule),
        message=_message(result, rule),
        path=path,
        line=line,
    )


def parse_report(raw: bytes, where: str, *, tree: Path) -> Report:
    """One report as its tool wrote it, or what makes the file not one.

    A refusal names the file and what was expected of it, because a report
    the reader cannot make sense of is a fault of the measurement, never a
    verdict on the candidate. What arrives is a document another party wrote,
    and there are two kinds of wrong type in one. The arrays this walks decide
    how many results there are, so one holding something that is not a result
    is refused: reading past it would report a count nothing measured, which
    is the green that measures nothing one level down. A field inside a result
    decorates that one finding, so a wrong type there is a value this reader
    does not have, and the finding keeps whatever else it carries.
    """
    tools: list[str] = []
    results: list[Result] = []
    suppressed = 0
    for index, entry in enumerate(_runs_of(raw, where)):
        run = _run_of(entry, where, index)
        driver = _mapping(_mapping(run.get("tool")).get("driver"))
        tool = _tool(driver)
        if tool and tool not in tools:
            tools.append(tool)
        listed, by_id = _rules(driver)
        for position, item in enumerate(_results_of(run, where, index)):
            if not isinstance(item, dict):
                raise ReportFault(
                    f"{where} names a result that is not an object:"
                    f" runs[{index}].results[{position}]"
                )
            if (_string(item.get("kind")) or FAILURE_KIND) != FAILURE_KIND:
                continue
            if _suppressed(item):
                suppressed += 1
                continue
            results.append(_read_result(item, listed, by_id, tree))
    return Report(
        path=where,
        tools=tuple(tools),
        results=tuple(results),
        suppressed=suppressed,
    )


def project(
    reports: list[Report], *, sensor: str, passed: bool, pattern: str, left: int
) -> Observation:
    """The reports, as the document every reader of an observation expects.

    The status is the verdict the exit code reached, because the controller
    wrote this document and contradicts nothing it decided. Every result the
    tool reported as a failure and did not suppress is a finding naming its
    rule, where it points and what the tool said in one line. `results` counts
    them all, and the three level counts each count part of that total: a
    result its tool gave no severity is in the total and under no level.
    """
    results = [result for report in reports for result in report.results]
    counted = dict.fromkeys(Level, 0)
    for result in results:
        counted[result.level] += 1
    suppressed = sum(report.suppressed for report in reports)
    taken: set[str] = set()
    findings = tuple(
        Finding(
            id=unique(
                f"{result.rule}:{result.path}" if result.path else result.rule, taken
            ),
            severity=SEVERITY[result.level],
            path=result.path,
            line=result.line,
            message=f"{result.level}: {result.message}",
        )
        for result in results
    )
    tools = sorted({tool for report in reports for tool in report.tools})
    if reports:
        summary = (
            f"{len(results)} results, {counted[Level.ERROR]} errors,"
            f" {counted[Level.WARNING]} warnings, {counted[Level.NOTE]} notes"
        )
        if tools:
            summary += f" from {', '.join(tools)}"
        summary += f" in {len(reports)} report{'s' if len(reports) != 1 else ''}"
        if suppressed:
            summary += f", {suppressed} suppressed"
    else:
        summary = f"no analysis report matching {pattern} was written"
    if left:
        summary += f"; {left} left from an earlier measurement, not read"
    return Observation(
        schema_version=SCHEMA_VERSION,
        sensor=sensor,
        status=Status.PASSED if passed else Status.FAILED,
        summary=summary,
        findings=findings,
        metrics={
            "results": len(results),
            "errors": counted[Level.ERROR],
            "warnings": counted[Level.WARNING],
            "notes": counted[Level.NOTE],
            "suppressed": suppressed,
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
        parse_report(read_report(tree, relative), relative, tree=tree)
        for relative in written
    ]
    return project(reports, sensor=sensor, passed=passed, pattern=pattern, left=left)
