"""A run directory built field by field, for tests that read one back.

The record, the journal and every artefact it names are written here, so a
test can move exactly one of them and say what that does to the verdict.
"""

import json
from pathlib import Path

from codeservo.evidence.digests import (
    sha256_file,
    sha256_json,
    sha256_path,
    sha256_record,
    sha256_text,
    write_json,
)
from codeservo.evidence.journal import JOURNAL_NAME, Journal
from codeservo.evidence.verify import JOURNAL_EVIDENCE_VERSION

"""Verification of one run directory, against records built by hand.

A record written here is shaped exactly as the controller writes one, so a
case can move a single digest, artefact or journal line and watch the
verification report what that move broke.
"""


RUN_ID = "20260901T110848639656Z"


TASK = "# Task\n\n- [AC1] `value()` returns `2`.\n"


CONSTITUTION = 'version = 1\n\n[review]\nblocking_severities = ["blocker"]\n'


CATALOGUE = (
    'version = 1\nbasis = "test"\n\n[[model]]\nbackend = "claude"\n'
    'id = "claude-haiku-4-5-20251001"\npositioning = "light"\n'
)


PATCH = "diff --git a/app.py b/app.py\n"


REVIEW = {
    "criteria": [{"id": "AC1", "status": "satisfied", "evidence": "app.py"}],
    "findings": [],
}


def _gate(run_dir: Path, phase: str, name: str, *, passed: bool = True) -> dict:
    """One gate result, with the two logs the measurement left behind."""
    out_dir = run_dir / phase
    out_dir.mkdir(parents=True, exist_ok=True)
    for stream in ("stdout", "stderr"):
        (out_dir / f"{name}.{stream}.log").write_text(
            f"{name} {stream}\n", encoding="utf-8"
        )
    record = {
        "name": name,
        "command": "true",
        "passed": passed,
        "exit_code": 0 if passed else 1,
        "timed_out": False,
        "duration_ms": 12,
        "stdout_path": f"{phase}/{name}.stdout.log",
        "stdout_sha256": sha256_file(out_dir / f"{name}.stdout.log"),
        "stderr_path": f"{phase}/{name}.stderr.log",
        "stderr_sha256": sha256_file(out_dir / f"{name}.stderr.log"),
    }
    record["result_sha256"] = sha256_record(record)
    return record


def build_run(
    root: Path,
    *,
    status: str = "ACCEPTED",
    reasons: tuple[str, ...] = (),
    schema_version: int = JOURNAL_EVIDENCE_VERSION,
    journal: bool = True,
) -> Path:
    """A complete run directory, recorded the way the controller records one."""
    run_dir = root / "run"
    (run_dir / "environment").mkdir(parents=True)
    (run_dir / "TASK.md").write_text(TASK, encoding="utf-8")
    (run_dir / "constitution.toml").write_text(CONSTITUTION, encoding="utf-8")
    (run_dir / "catalogue.toml").write_text(CATALOGUE, encoding="utf-8")
    (run_dir / "change.patch").write_text(PATCH, encoding="utf-8")

    sensor = run_dir / "sensors" / "task-outcome"
    sensor.mkdir(parents=True)
    (sensor / "README.md").write_text("Controller-owned sensor.\n", encoding="utf-8")

    packages = run_dir / "environment" / "packages.json"
    write_json(packages, [{"name": "python", "version": "3.12.0"}])

    iteration_dir = run_dir / "iterations" / "01"
    (iteration_dir / "agent").mkdir(parents=True)
    (iteration_dir / "prompt.md").write_text("implement\n", encoding="utf-8")
    (iteration_dir / "agent" / "events.jsonl").write_text("{}\n", encoding="utf-8")
    agent = {
        "exit_code": 0,
        "duration_ms": 40,
        "events_path": "iterations/01/agent/events.jsonl",
        "events_sha256": sha256_file(iteration_dir / "agent" / "events.jsonl"),
    }
    agent["result_sha256"] = sha256_record(agent)

    review_dir = iteration_dir / "review"
    review_dir.mkdir()
    (review_dir / "prompt.md").write_text("review\n", encoding="utf-8")
    write_json(review_dir / "result.json", REVIEW)
    observations = {"schema_version": 1, "gates": []}
    meta = {
        "exit_code": 0,
        "result_path": "iterations/01/review/result.json",
        "result_sha256": sha256_file(review_dir / "result.json"),
    }
    meta["meta_sha256"] = sha256_record(meta)

    book = Journal(run_dir / JOURNAL_NAME, RUN_ID) if journal else None
    if book is not None:
        book.record("run.started", {"base_commit": "abc"})
        book.record("inputs.frozen", {"task_sha256": sha256_text(TASK)})

    baseline = [_gate(run_dir, "baseline", "unit")]
    quick = [_gate(run_dir, "iterations/01/quick", "unit")]
    full = [_gate(run_dir, "iterations/01/full", "compile")]
    record = {
        "schema_version": schema_version,
        "run_id": RUN_ID,
        "base_commit": "abc",
        "task_sha256": sha256_text(TASK),
        "constitution_sha256": sha256_text(CONSTITUTION),
        "catalogue_sha256": sha256_text(CATALOGUE),
        "sensors": {
            "task-outcome": {
                "path": "sensors/task-outcome",
                "reference": "test/task-outcome",
                "sha256": sha256_path(sensor),
            }
        },
        "environment": {
            "provider": "pixi",
            "manifest_path": "pyproject.toml",
            "manifest_sha256": sha256_text("[workspace]\n"),
            "lock_path": "pixi.lock",
            "lock_sha256": sha256_text("version: 6\n"),
            "packages_path": "environment/packages.json",
            "packages_sha256": sha256_file(packages),
        },
        "baseline": baseline,
        "iterations": [
            {
                "iteration": 1,
                "prompt": {
                    "path": "iterations/01/prompt.md",
                    "sha256": sha256_text("implement\n"),
                },
                "agent": agent,
                "quick_gates": quick,
                "full_gates": full,
                "review": {
                    "prompt": {
                        "path": "iterations/01/review/prompt.md",
                        "sha256": sha256_text("review\n"),
                    },
                    "observations": observations,
                    "observations_sha256": sha256_json(observations),
                    "result": REVIEW,
                    "result_sha256": sha256_json(REVIEW),
                    "meta": meta,
                },
            }
        ],
        "status": status,
        "decision": {"reasons": list(reasons)},
        "patch_sha256": sha256_text(PATCH),
        "run_dir": ".",
    }
    if book is not None:
        measured = (("baseline", baseline), ("quick", quick), ("full", full))
        for phase, results in measured:
            for result in results:
                book.record(
                    "gate.finished",
                    {
                        "phase": phase,
                        "name": result["name"],
                        "passed": result["passed"],
                        "result_sha256": result["result_sha256"],
                    },
                )
        if status != "RUNNING":
            book.record(
                "decision.recorded", {"status": status, "reasons": list(reasons)}
            )
            book.record("run.finished", {"status": status})
        record["events"] = book.summary().to_document()
    write_json(run_dir / "evidence.json", record)
    return run_dir


def read_record(run_dir: Path) -> dict:
    return json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))


def rewrite_record(run_dir: Path, record: dict) -> None:
    write_json(run_dir / "evidence.json", record)


def journal_lines(run_dir: Path) -> list[str]:
    return (run_dir / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()


def rewrite_journal(run_dir: Path, lines: list[str]) -> None:
    (run_dir / JOURNAL_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def named(report: dict, name: str) -> dict:
    return next(check for check in report["checks"] if check["name"] == name)
