"""What each gate of this repository measured, projected onto the contract.

Six gates run a tool over this tree. Each one kept its verdict and threw the
measurement away: how many violations, how many type errors, how many tests,
how many dependencies, what percentage. This is the deterministic adapter the
architecture describes, one per tool: the tool keeps its own output format,
and this projects that output onto the six fields the controller reads. The
controller learns nothing about `ruff`, `mypy` or `coverage`, and none of them
learns anything about the controller.

    python tools/gate.py <name> [<document>]

Two properties hold for all six, and they matter more than the projections.

The tool's own output goes through untouched, on the stream it was written to.
It is what the controller feeds back to the actuator when a gate fails, so a
wrapper that summarised it would quietly replace the feedback loop's input
with its own prose.

And the exit code is the tool's. Nothing here decides a verdict; a document
saying `passed` beside a non-zero exit is a contradiction the controller
refuses, and it is not this side's place to produce one.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

import observation  # noqa: E402

# Where the controller names the sensor it froze for one gate of this run. A
# gate naming a task could not read it: `pixi run --clean-env` empties the
# environment its task starts with, so the sensor gate names a command.
SENSOR_PATH_VARIABLE = "CODESERVO_SENSOR_PATH"

# The trees the linters read, named once. `mypy` reads the shipped tree alone,
# which is its own declaration in the manifest and not repeated here.
LINTED = ("src", "tests", "tools")

# `path:line: severity: message  [code]`, which is what mypy writes when it is
# not asked for anything else. Reading its text rather than asking for JSON
# keeps the run to one pass over a tree that takes ten seconds to check.
MYPY_LINE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+): (?P<severity>error|warning|note): "
    r"(?P<message>.*?)(?:\s+\[(?P<code>[\w-]+)\])?$"
)
MYPY_TOTALS = re.compile(
    r"(?:no issues found in|checked) (?P<files>\d+) source files?"
)

# What `lint-imports` prints about the graph it built and the contracts it
# checked, and one line of a broken contract.
IMPORTS_GRAPH = re.compile(r"Analyzed (?P<files>\d+) files, (?P<edges>\d+) dependencies")
IMPORTS_TOTALS = re.compile(r"Contracts: (?P<kept>\d+) kept, (?P<broken>\d+) broken")
IMPORTS_BREACH = re.compile(
    r"^- (?P<importer>[\w.]+) -> (?P<imported>[\w.]+) \(l\.(?P<line>\d+)\)"
)


@dataclass(frozen=True)
class Projection:
    """One gate's exit code, and what its tool reported alongside it."""

    exit_code: int
    summary: str
    metrics: dict[str, float]
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def _run(*command: str) -> tuple[int, str, str]:
    """Run one tool, pass its output through untouched, and keep it to read.

    The bytes written here are the bytes the controller feeds back to the
    actuator, so they are the tool's and not a rendering of them.
    """
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode, done.stdout, done.stderr


def _stream(*command: str) -> int:
    """Run one tool on the gate's own streams, and read only its exit code.

    Nothing is placed between the tool and the descriptors the controller
    handed the gate. A pipe there is invisible in the log and still changes
    what the tool's children can observe about where their output goes, which
    is how a suite that checks it is running confined stops being able to
    tell: `tests/isolation_harness` finds the write-protected run directory
    through the directory of its own descriptors. Every tool that executes
    this repository's tests is therefore run this way, and only a tool whose
    text is the projection's only source is captured.
    """
    return subprocess.run(command, cwd=ROOT).returncode


def _quiet(*command: str) -> tuple[int, str]:
    """Ask a tool the same question again, in the form a machine reads."""
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return done.returncode, done.stdout


def _relative(path: str) -> str:
    """One location the tool named, as this repository names it."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return path


def _module_file(module: str) -> str:
    """The file a module name stands for, so a finding names a place."""
    return f"src/{module.replace('.', '/')}.py"


# --- The six projections ---------------------------------------------------


def lint() -> Projection:
    """Every violation `ruff` raised, where it raised it.

    Asked twice: once on the gate's own streams, which is what a failing gate
    feeds back, and once as JSON. The second pass costs a fraction of a second
    and spares this from parsing a format meant for eyes.
    """
    exit_code = _stream("ruff", "check", "--no-cache", *LINTED)
    _, raw = _quiet("ruff", "check", "--no-cache", "--output-format", "json", *LINTED)
    violations = json.loads(raw or "[]")
    findings = [
        observation.finding(
            id=item.get("code") or "syntax-error",
            severity=observation.MAJOR,
            path=_relative(item["filename"]),
            line=item["location"]["row"],
            message=item["message"],
        )
        for item in violations
    ]
    return Projection(
        exit_code,
        f"{len(violations)} violations over {', '.join(LINTED)}",
        {"violations": len(violations)},
        findings,
    )


def types() -> Projection:
    """Every error `mypy` raised on the shipped tree."""
    exit_code, out, _ = _run("mypy", "--cache-dir=/dev/null", "src")
    findings = []
    counts = {"error": 0, "warning": 0, "note": 0}
    checked = 0.0
    for line in out.splitlines():
        totals = MYPY_TOTALS.search(line)
        if totals:
            checked = float(totals["files"])
        raised = MYPY_LINE.match(line)
        if not raised:
            continue
        counts[raised["severity"]] += 1
        findings.append(
            observation.finding(
                id=raised["code"] or raised["severity"],
                severity=(
                    observation.MAJOR
                    if raised["severity"] == "error"
                    else observation.INFO
                ),
                path=_relative(raised["path"]),
                line=int(raised["line"]),
                message=raised["message"],
            )
        )
    return Projection(
        exit_code,
        f"{counts['error']} errors over {checked:g} source files",
        {
            "errors": counts["error"],
            "warnings": counts["warning"],
            "files": checked,
        },
        findings,
    )


def _discovered(start_dir: Path, subject: str) -> Projection:
    """One `unittest` discovery, counted from its result rather than its text.

    Run in this process so the counts come from the result object instead of
    from a line of text, and the runner's output is written through afterwards
    on the stream `unittest` uses. `subject` names the selection in the
    summary; the suite of this repository names none, being the default one.
    """
    log = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(start_dir))
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    sys.stderr.write(log.getvalue())

    findings = [
        observation.finding(
            id=str(case),
            severity=observation.BLOCKER,
            message=trace.strip().splitlines()[-1],
        )
        for outcome in (result.failures, result.errors)
        for case, trace in outcome
    ]
    return Projection(
        0 if result.wasSuccessful() else 1,
        f"{result.testsRun} tests{subject}, {len(result.failures)} failed,"
        f" {len(result.errors)} errored",
        {
            "tests": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
        },
        findings,
    )


def test() -> Projection:
    """The suite of this repository."""
    return _discovered(ROOT / "tests", "")


def sensor() -> Projection:
    """The frozen acceptance sensor, run against the candidate.

    The controller names its location in the environment and never in the
    constitution, so the gate measures whatever sensor the run froze and this
    repository never holds its source. What the projection adds is the count
    and one finding per contract the candidate failed, where a run recorded
    only that the sensor had refused.
    """
    frozen = os.environ.get(SENSOR_PATH_VARIABLE)
    if not frozen:
        return Projection(1, "the controller named no frozen sensor to run", {})
    return _discovered(Path(frozen), " under the frozen sensor")


def compile_() -> Projection:
    """Every file of the tree parses and byte-compiles.

    The tool answers with an exit code and a traceback, so the count is of
    what was submitted to it rather than of what it reported back.
    """
    submitted = sum(
        1 for tree in ("src", "tests") for _ in (ROOT / tree).rglob("*.py")
    )
    exit_code, _, err = _run("python", "-m", "compileall", "-q", "src", "tests")
    findings = (
        []
        if exit_code == 0
        else [
            observation.finding(
                id="compile-error",
                severity=observation.BLOCKER,
                message=err.strip().splitlines()[-1] if err.strip() else "see stderr",
            )
        ]
    )
    return Projection(
        exit_code,
        f"{submitted} Python files submitted to the byte compiler",
        {"files": submitted},
        findings,
    )


def architecture() -> Projection:
    """The layer contract, and every import that breaks one."""
    exit_code, out, _ = _run("lint-imports", "--no-cache", "--no-logo")
    graph = IMPORTS_GRAPH.search(out)
    totals = IMPORTS_TOTALS.search(out)
    findings = [
        observation.finding(
            id=f"forbidden-import:{breach['importer']}",
            severity=observation.BLOCKER,
            path=_module_file(breach["importer"]),
            line=int(breach["line"]),
            message=f"{breach['importer']} imports {breach['imported']}",
        )
        for line in out.splitlines()
        if (breach := IMPORTS_BREACH.match(line.strip()))
    ]
    kept = float(totals["kept"]) if totals else 0.0
    broken = float(totals["broken"]) if totals else 0.0
    return Projection(
        exit_code,
        f"{kept:g} contracts kept, {broken:g} broken"
        + (
            f", over {graph['files']} files and {graph['edges']} dependencies"
            if graph
            else ""
        ),
        {
            "contracts_kept": kept,
            "contracts_broken": broken,
            "files": float(graph["files"]) if graph else 0.0,
            "dependencies": float(graph["edges"]) if graph else 0.0,
        },
        findings,
    )


def coverage() -> Projection:
    """What the suite covered of the decision core, against its floor.

    Three calls, and the order matters. The suite must pass before a
    percentage means anything, so a failing suite ends here rather than
    reporting a coverage taken over a different amount of work. `report` holds
    the floor and therefore decides the exit code. `json` writes the numbers,
    outside the tree, a gate writing nothing into what it measures.
    """
    import tempfile
    import tomllib

    suite = _stream("coverage", "run", "-m", "unittest", "discover", "-s", "tests")
    if suite != 0:
        return Projection(
            suite or 1,
            "the suite does not pass, so nothing was covered that this reports",
            {},
        )
    verdict = _stream("coverage", "report")
    with tempfile.TemporaryDirectory(prefix="codeservo-coverage-") as tmp:
        report = Path(tmp) / "coverage.json"
        _quiet("coverage", "json", "-o", str(report))
        measured = json.loads(report.read_text(encoding="utf-8"))

    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floor = float(manifest["tool"]["coverage"]["report"]["fail_under"])
    totals = measured["totals"]
    percent = float(totals["percent_covered"])
    findings = [
        observation.finding(
            id=f"below-floor:{path}",
            severity=observation.INFO,
            path=path,
            line=None,
            message=(
                f"{file['summary']['percent_covered']:.2f} percent covered,"
                f" under the floor of {floor:g}"
            ),
        )
        for path, file in sorted(measured["files"].items())
        if float(file["summary"]["percent_covered"]) < floor
    ]
    return Projection(
        0 if verdict == 0 else 1,
        f"{percent:.2f} percent of {totals['num_statements']} statements"
        f" in the decision core, floor {floor:g}",
        {
            "line_coverage": round(percent, 2),
            "statements": totals["num_statements"],
            "covered": totals["covered_lines"],
            "missing": totals["missing_lines"],
            "floor": floor,
        },
        findings,
    )


GATES = {
    "lint": lint,
    "types": types,
    "test": test,
    "compile": compile_,
    "architecture": architecture,
    "coverage": coverage,
    "sensor": sensor,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in GATES:
        print(f"usage: gate.py {{{'|'.join(GATES)}}} [document]", file=sys.stderr)
        return 2
    name = argv[0]
    projected = GATES[name]()
    observation.write(
        observation.location(argv[1:]),
        sensor=name,
        passed=projected.passed,
        summary=projected.summary,
        findings=projected.findings,
        metrics=projected.metrics,
    )
    return projected.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
