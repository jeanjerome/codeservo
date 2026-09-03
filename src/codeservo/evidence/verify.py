"""Decide, from one run directory alone, whether its record still holds.

The verification reads and never writes. It recomputes every digest the
record names against the artefact the run directory holds, recomputes the
digests a record takes over itself, replays the journal's chain, and checks
that the decision the record states is the decision the journal closed on.
Nothing is compared against the source repository, the base commit or the
machine the run ran on: what is not readable here is reported as such.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..domain.run import RunStatus
from .digests import (
    VERBATIM_TRAILS,
    sha256_file,
    sha256_json,
    sha256_path,
    sha256_record,
)
from .journal import (
    JOURNAL_NAME,
    JournalError,
    chain_failures,
    read_journal,
)

# The shape of the report. The verification versions its own shape.
REPORT_SCHEMA_VERSION = 1

# The record shape that first required a journal. Anything below it was
# written before this contract existed and cannot be held to it.
JOURNAL_EVIDENCE_VERSION = 14


class CheckStatus(StrEnum):
    """What one check of the report concluded."""

    OK = "ok"
    FAILED = "failed"
    ABSENT = "missing"
    NOT_VERIFIABLE = "not_verifiable"


class Verdict(StrEnum):
    """What the report concludes from every check it ran."""

    VALID = "VALID"
    INVALID = "INVALID"
    INCOMPLETE = "INCOMPLETE"


# Recorded locations naming a file of the source repository at the base
# commit. The run directory holds no copy of either, so neither is a proof it
# can produce and neither is a proof it is missing.
SOURCE_REPOSITORY_TRAILS = (
    ("environment", "manifest_path"),
    ("environment", "lock_path"),
)

# Trees the controller froze whole. They are digested as directories, so they
# are checked on their own rather than as files.
SENSOR_TRAIL = ("sensors",)

# The third trail this reads, `VERBATIM_TRAILS`, is not declared here. It
# states which documents were recorded as their producer returned them, which
# the relativisation that writes a record and this verification have to agree
# on, so it is stated once beside the writer and imported from there.


class VerificationError(RuntimeError):
    pass


class _Report:
    """The checks, and the two lists a status follows from."""

    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.failures: list[str] = []
        self.missing: list[str] = []

    def ok(self, name: str, detail: str) -> None:
        self.checks.append({"name": name, "status": CheckStatus.OK, "detail": detail})

    def failed(self, name: str, statement: str) -> None:
        self.checks.append({"name": name, "status": CheckStatus.FAILED, "detail": statement})
        self.failures.append(statement)

    def absent(self, name: str, statement: str) -> None:
        self.checks.append({"name": name, "status": CheckStatus.ABSENT, "detail": statement})
        self.missing.append(statement)

    def not_verifiable(self, name: str, detail: str) -> None:
        self.checks.append({"name": name, "status": CheckStatus.NOT_VERIFIABLE, "detail": detail})

    @property
    def status(self) -> Verdict:
        if self.failures:
            return Verdict.INVALID
        if self.missing:
            return Verdict.INCOMPLETE
        return Verdict.VALID


def verify_run(run_dir: Path) -> dict:
    """Verify one run directory and return the report as data."""
    run_dir = Path(run_dir)
    record = _load_record(run_dir / "evidence.json")
    report = _Report()
    _check_inputs(report, run_dir, record)
    _check_sensors(report, run_dir, record)
    _check_artifacts(report, run_dir, record)
    _check_patch(report, run_dir, record)
    _check_recomputed(report, record)
    _check_journal(report, run_dir, record)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": record.get("run_id"),
        "status": report.status,
        "checks": report.checks,
        "failures": report.failures,
        "missing": report.missing,
    }


def render_report(report: dict) -> str:
    """The human listing: every check, how it ended, and the status."""
    lines = [f"run: {report['run_id']}"]
    width = max((len(check["name"]) for check in report["checks"]), default=0)
    lines.extend(
        f"  {check['name']:<{width}}  {check['status']:<14}  {check['detail']}"
        for check in report["checks"]
    )
    lines.extend(f"  failure: {failure}" for failure in report["failures"])
    lines.extend(f"  missing: {absent}" for absent in report["missing"])
    lines.append(f"status: {report['status']}")
    return "\n".join(lines) + "\n"


def _load_record(path: Path) -> dict:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VerificationError(f"no readable evidence.json in {path.parent}") from exc
    if not isinstance(record, dict):
        raise VerificationError(f"no readable evidence.json in {path.parent}")
    return record


# --- Artefacts the record names -------------------------------------------


def _inside(run_dir: Path, location: str) -> Path | None:
    """The file a record names, or nothing when it names one outside the run.

    A record is the input this command exists to distrust, and the run
    directory is what the command says is its only intake. A location that is
    absolute, or that climbs out of the directory, names a file this run does
    not hold whatever that file turns out to be, so it is refused before it is
    read rather than read and then judged. Resolving both sides settles the
    spellings that normalise away and the symbolic links that do not.
    """
    try:
        resolved = Path(run_dir, location).resolve()
        root = run_dir.resolve()
    except (OSError, ValueError):
        return None
    if resolved != root and not resolved.is_relative_to(root):
        return None
    return resolved


OUTSIDE = "the record names a path outside this run"


def _check_file(
    report: _Report, name: str, run_dir: Path, location: str, digest: str
) -> None:
    target = _inside(run_dir, location)
    if target is None:
        report.failed(name, f"{location}: {OUTSIDE}")
    elif not target.is_file():
        report.failed(
            name, f"{location}: the record names an artefact this run does not hold"
        )
    elif sha256_file(target) != digest:
        report.failed(name, f"{location}: the recorded digest describes other bytes")
    else:
        report.ok(name, location)


def _check_inputs(report: _Report, run_dir: Path, record: dict) -> None:
    for location, field in (
        ("TASK.md", "task_sha256"),
        ("constitution.toml", "constitution_sha256"),
    ):
        digest = record.get(field)
        if isinstance(digest, str):
            _check_file(report, f"input.{location}", run_dir, location, digest)


def _check_sensors(report: _Report, run_dir: Path, record: dict) -> None:
    """Each frozen sensor tree, against the directory digest it was frozen with."""
    sensors = record.get("sensors")
    if not isinstance(sensors, dict):
        return
    for gate in sorted(sensors):
        frozen = sensors[gate]
        if not isinstance(frozen, dict):
            continue
        location = frozen.get("path")
        digest = frozen.get("sha256")
        if not isinstance(location, str) or not isinstance(digest, str):
            continue
        name = f"sensor.{gate}"
        target = _inside(run_dir, location)
        if target is None:
            report.failed(name, f"{location}: {OUTSIDE}")
        elif not target.exists():
            report.failed(
                name, f"{location}: the record names a sensor this run does not hold"
            )
        elif sha256_path(target) != digest:
            report.failed(name, f"{location}: the frozen sensor changed")
        else:
            report.ok(name, location)


def _digest_pairs(record: dict) -> list[tuple[tuple[str, ...], str, str]]:
    """Every `path`/`sha256` pair the record names, with where it names it.

    A digest without a companion path names no artefact of the run directory,
    and a path without a digest names nothing to compare, so neither is a
    pair. A null digest records the absence of a file rather than a file.
    """
    pairs: list[tuple[tuple[str, ...], str, str]] = []

    def visit(value: Any, trail: tuple[str, ...]) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*trail, str(index)))
            return
        if not isinstance(value, dict):
            return
        if trail in VERBATIM_TRAILS or trail == SENSOR_TRAIL:
            return
        for key, digest in sorted(value.items()):
            if not key.endswith("sha256") or not isinstance(digest, str):
                continue
            prefix = key[: -len("sha256")]
            path_key = "path" if key == "sha256" else f"{prefix}path"
            location = value.get(path_key)
            if not isinstance(location, str):
                continue
            if (*trail, path_key) in SOURCE_REPOSITORY_TRAILS:
                continue
            pairs.append(((*trail, path_key), location, digest))
        for key, item in value.items():
            visit(item, (*trail, key))

    visit(record, ())
    return pairs


def _check_artifacts(report: _Report, run_dir: Path, record: dict) -> None:
    for _, location, digest in _digest_pairs(record):
        _check_file(report, f"artifact.{location}", run_dir, location, digest)
    for trail in SOURCE_REPOSITORY_TRAILS:
        location = _at(record, trail)
        if isinstance(location, str):
            report.not_verifiable(
                ".".join(trail),
                f"{location}: a file of the source repository, not of this run",
            )


def _check_patch(report: _Report, run_dir: Path, record: dict) -> None:
    digest = record.get("patch_sha256")
    if isinstance(digest, str):
        _check_file(report, "artifact.change.patch", run_dir, "change.patch", digest)


# --- Digests a record takes over itself ------------------------------------


def _sequence(value: Any) -> list:
    """The list a record names at one place, or none where it names otherwise.

    A record is the input this command exists to distrust, so a value of
    another shape is one the run does not hold rather than one to walk: a
    boolean where the baseline gates belong would otherwise raise through the
    verification and leave the run with no verdict at all.
    """
    return value if isinstance(value, list) else []


def _at(record: dict, trail: tuple[str, ...]) -> Any:
    value: Any = record
    for key in trail:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _recomputed(
    report: _Report, name: str, relation: str, recorded: Any, computed: str
) -> None:
    if not isinstance(recorded, str):
        return
    if recorded != computed:
        report.failed(name, f"{relation}: does not describe what the record holds")
    else:
        report.ok(name, relation)


def _check_gate(report: _Report, location: str, gate: Any) -> None:
    if not isinstance(gate, dict):
        return
    _recomputed(
        report,
        f"digest.{location}",
        f"{location}.result_sha256",
        gate.get("result_sha256"),
        sha256_record(gate),
    )


def _check_recomputed(report: _Report, record: dict) -> None:
    """The digests a record recomputes from itself, each as it was produced."""
    for index, gate in enumerate(_sequence(record.get("baseline"))):
        _check_gate(report, f"baseline.{index}", gate)
    for position, iteration in enumerate(_sequence(record.get("iterations"))):
        if not isinstance(iteration, dict):
            continue
        number = iteration.get("iteration", position)
        for index, gate in enumerate(_sequence(iteration.get("quick_gates"))):
            _check_gate(report, f"iterations.{number}.quick_gates.{index}", gate)
        agent = iteration.get("agent")
        if isinstance(agent, dict):
            _recomputed(
                report,
                f"digest.iterations.{number}.agent",
                f"iterations.{number}.agent.result_sha256",
                agent.get("result_sha256"),
                sha256_record(agent),
            )
    for index, gate in enumerate(_sequence(record.get("full_gates"))):
        _check_gate(report, f"full_gates.{index}", gate)

    review = record.get("review")
    if not isinstance(review, dict):
        return
    if "result" in review:
        _recomputed(
            report,
            "digest.review.result",
            "review.result_sha256",
            review.get("result_sha256"),
            sha256_json(review["result"]),
        )
    if "observations" in review:
        _recomputed(
            report,
            "digest.review.observations",
            "review.observations_sha256",
            review.get("observations_sha256"),
            sha256_json(review["observations"]),
        )
    meta = review.get("meta")
    if isinstance(meta, dict):
        _recomputed(
            report,
            "digest.review.meta",
            "review.meta.meta_sha256",
            meta.get("meta_sha256"),
            sha256_record(
                {key: value for key, value in meta.items() if key != "meta_sha256"}
            ),
        )


# --- The journal, and what it must agree with ------------------------------


def _check_journal(report: _Report, run_dir: Path, record: dict) -> None:
    schema = record.get("schema_version")
    running = record.get("status") == RunStatus.RUNNING
    block = record.get("events")
    location = block.get("path") if isinstance(block, dict) else None
    if not isinstance(location, str):
        location = JOURNAL_NAME

    if not isinstance(schema, int) or schema < JOURNAL_EVIDENCE_VERSION:
        report.absent(
            "journal",
            f"{JOURNAL_NAME}: the record predates the run journal"
            f" (schema {schema})",
        )
        return

    journal_path = _inside(run_dir, location)
    if journal_path is None:
        report.failed("journal", f"{location}: {OUTSIDE}")
        return
    if not journal_path.is_file():
        statement = f"{location}: the run directory holds no journal"
        if running:
            report.absent("journal", statement)
        else:
            report.failed("journal", statement)
        return

    try:
        events = read_journal(journal_path)
    except JournalError as exc:
        report.failed("journal.shape", str(exc))
        return

    _check_chain(report, events, record.get("run_id"))
    if running:
        statement = f"{location}: the run never finished"
        report.absent("journal.events", statement)
        report.absent("journal.decision", statement)
        return
    _check_events_block(report, journal_path, location, block, events)
    _check_decision(report, location, record, events)


def _check_chain(report: _Report, events: list[dict], run_id: Any) -> None:
    failures = chain_failures(events, run_id if isinstance(run_id, str) else None)
    for aspect in ("shape", "sequence", "chain", "digests"):
        stated = [statement for name, statement in failures if name == aspect]
        if stated:
            for statement in stated:
                report.failed(f"journal.{aspect}", statement)
        else:
            report.ok(f"journal.{aspect}", f"{len(events)} events")


def _check_events_block(
    report: _Report,
    journal_path: Path,
    location: str,
    block: Any,
    events: list[dict],
) -> None:
    if not isinstance(block, dict):
        report.failed("journal.events", f"{location}: the record declares no journal")
        return
    head = events[-1].get("sha256") if events else None
    bytes_digest = sha256_file(journal_path)
    stated = [
        statement
        for statement, recorded, computed in (
            ("counts", block.get("count"), len(events)),
            ("names another last event", block.get("head_sha256"), head),
            ("describes other bytes", block.get("file_sha256"), bytes_digest),
        )
        if recorded != computed
    ]
    if stated:
        for statement in stated:
            report.failed(
                "journal.events", f"{location}: the recorded events block {statement}"
            )
    else:
        report.ok("journal.events", f"{len(events)} events")


def _check_decision(
    report: _Report, location: str, record: dict, events: list[dict]
) -> None:
    """The decision the record states, against the two events that closed it."""
    if len(events) < 2:
        report.failed(
            "journal.decision", f"{location}: the journal closes on no decision"
        )
        return
    decision, finished = events[-2], events[-1]
    status = record.get("status")
    reasons = _at(record, ("decision", "reasons"))
    statements = []
    if finished.get("type") != "run.finished":
        statements.append(f"{location}: the journal does not end on run.finished")
    elif _at(finished, ("payload", "status")) != status:
        statements.append(f"{location}: run.finished states another status")
    if decision.get("type") != "decision.recorded":
        statements.append(f"{location}: no decision.recorded closes the journal")
    else:
        payload = decision.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if payload.get("status") != status:
            statements.append(f"{location}: decision.recorded states another status")
        if payload.get("reasons") != reasons:
            statements.append(f"{location}: decision.recorded states other reasons")
    if statements:
        for statement in statements:
            report.failed("journal.decision", statement)
    else:
        report.ok("journal.decision", str(status))
