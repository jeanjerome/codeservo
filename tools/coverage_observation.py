"""Project what `coverage` measured onto the controller's observation contract.

The coverage gate reached a verdict and threw the measurement away: it exited
zero or two, and the percentage it computed existed only in a log nobody
compares. A run could not say what the decision core measured, and two runs
could not be set beside each other.

This is the deterministic adapter the architecture describes, applied to this
repository: the tool keeps its own output format, and a script owned by the
target repository projects it onto the six fields the controller reads. The
controller learns nothing about `coverage`, and `coverage` learns nothing
about the controller.

    pixi run --locked --no-config coverage             # the human form
    python tools/coverage_observation.py <document>    # what a gate is given

The exit code stays the verdict. The document says what was measured, and it
is written only when a location is given: run by hand there is no document to
write and none is invented.

The location arrives as an argument rather than in the environment, because a
task gate is run with `pixi run --clean-env` and starts with no environment to
read. A gate naming a shell command is told the other way, and both are the
controller's business rather than this script's.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# What the record calls this measurement. It names the sensor, not the tool:
# a repository that replaced `coverage` would keep the name and the contract.
SENSOR = "coverage"

# The shape the controller validates against. Declared here as the number this
# document says it is, so a document written against another version says so.
SCHEMA_VERSION = 1

# A file under the floor the whole core is held to. It does not fail the gate
# — the total does — but a record that names it can be read a year later.
FINDING_SEVERITY = "info"


def _run(*command: str, **kwargs: object) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, text=True, **kwargs)  # type: ignore[call-overload]


def _floor() -> float:
    """The floor the report is held to, read where the report reads it.

    Stating it here as well would be a second declaration, and the two would
    drift; `coverage` already refuses under it, and this only names it.
    """
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return float(manifest["tool"]["coverage"]["report"]["fail_under"])


def measure(report: Path) -> tuple[int, dict]:
    """Run the suite under coverage, and read back what it covered.

    Three calls, and the order matters. The suite must pass before a
    percentage means anything, so a failing suite ends here rather than
    reporting a coverage taken over a different amount of work. `report` is
    what holds the floor and therefore what decides the exit code. `json`
    writes the numbers, outside the tree, because a gate writes nothing into
    what it measures.
    """
    suite = _run("coverage", "run", "-m", "unittest", "discover", "-s", "tests")
    if suite.returncode != 0:
        return suite.returncode, {}

    verdict = _run("coverage", "report")
    _run("coverage", "json", "-o", str(report), capture_output=True)
    return (0 if verdict.returncode == 0 else 1), json.loads(
        report.read_text(encoding="utf-8")
    )


def observation(measured: dict, floor: float, exit_code: int) -> dict:
    """The six fields, each one read off what the tool reported."""
    totals = measured["totals"]
    percent = float(totals["percent_covered"])
    findings = [
        {
            "id": f"below-floor:{path}",
            "severity": FINDING_SEVERITY,
            "path": path,
            "line": None,
            "message": (
                f"{file['summary']['percent_covered']:.2f} percent covered,"
                f" under the floor of {floor:g}"
            ),
        }
        for path, file in sorted(measured["files"].items())
        if float(file["summary"]["percent_covered"]) < floor
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "sensor": SENSOR,
        "status": "passed" if exit_code == 0 else "failed",
        "summary": (
            f"{percent:.2f} percent of {totals['num_statements']} statements"
            f" in the decision core, floor {floor:g}"
        ),
        "findings": findings,
        "metrics": {
            "line_coverage": round(percent, 2),
            "statements": totals["num_statements"],
            "covered": totals["covered_lines"],
            "missing": totals["missing_lines"],
            "floor": floor,
        },
    }


def main(argv: list[str]) -> int:
    document = Path(argv[0]) if argv else None
    with tempfile.TemporaryDirectory(prefix="codeservo-coverage-") as tmp:
        exit_code, measured = measure(Path(tmp) / "coverage.json")
    if not measured:
        print(
            "the suite does not pass, so nothing was covered that this could"
            " report",
            file=sys.stderr,
        )
        return exit_code or 1

    floor = _floor()
    if document is not None:
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text(
            json.dumps(observation(measured, floor, exit_code), indent=2) + "\n",
            encoding="utf-8",
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
