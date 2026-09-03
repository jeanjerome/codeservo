"""Landing an accepted run: the change enters the repository, and the run says so.

The record is closed by the decision and the journal chains on it, so the
integration is not written into the record. It is one more event, appended
after `run.finished` and chained like the others, which the verification reads
as the one thing allowed to follow a decision. The findings the review reported
on the landed candidate go to the register the state directory keeps, where
they are countable across runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..domain.run import RunStatus
from ..evidence.journal import JOURNAL_NAME, LANDED_EVENT, Journal, JournalError
from ..evidence.register import NOT_COVERED, append_rows, register_path
from ..evidence.verify import Verdict, verify_run
from ..workspace.git import GitError, apply_patch, commit, head, is_clean, root

PATCH_NAME = "change.patch"


class LandingError(RuntimeError):
    """The run cannot be landed as it stands, and this says why."""


@dataclass(frozen=True)
class Landing:
    """What landing one run left behind."""

    run_id: str
    repo: Path
    commit: str
    register: Path
    findings: int


def land(run_dir: Path, message: str | None = None) -> Landing:
    """Apply an accepted run's patch to the repository it measured, and record it.

    Every refusal here names a control input that no longer holds: a record
    that does not verify, a run that was not accepted, a run already landed, a
    repository that moved on from the base commit the run measured, or one
    holding uncommitted work. A patch applied to any of those would be a
    change nothing measured.
    """
    run_dir = Path(run_dir).resolve()
    report = verify_run(run_dir)
    if report["status"] != Verdict.VALID:
        raise LandingError(
            f"the run does not verify ({report['status']}): "
            + "; ".join(report["failures"] or report["missing"])
        )
    record = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
    run_id = str(record.get("run_id"))
    if record.get("status") != RunStatus.ACCEPTED:
        raise LandingError(f"only an accepted run is landed; this one is {record.get('status')}")
    landed = [
        check for check in report["checks"] if check["name"] == "journal.landing"
    ]
    if landed and landed[0]["detail"] != "not landed":
        raise LandingError(f"run {run_id} was already {landed[0]['detail']}")

    repo = _repository(run_dir, record)
    base = str(record.get("base_commit"))
    if head(repo) != base:
        raise LandingError(
            f"the repository moved since the run: HEAD is {head(repo)},"
            f" the run measured {base}"
        )
    if not is_clean(repo):
        raise LandingError("the repository holds uncommitted work")
    patch = run_dir / PATCH_NAME
    if not patch.is_file() or not patch.read_text(encoding="utf-8").strip():
        raise LandingError("the run changed nothing there is to land")

    digest = str(record.get("patch_sha256"))
    try:
        apply_patch(repo, patch)
        landed_commit = commit(
            repo,
            message or f"codeservo: land run {run_id}",
            f"Run: {run_id}\nBase: {base}\nPatch-SHA256: {digest}",
        )
    except GitError as exc:
        raise LandingError(f"the patch did not land: {exc}") from exc

    # The commit exists before the journal says so, so a journal that could not
    # be appended to leaves a commit a person can see rather than an event
    # naming a commit that was never made.
    try:
        journal = Journal.resume(run_dir / JOURNAL_NAME, run_id)
    except JournalError as exc:
        raise LandingError(
            f"landed as {landed_commit}, but the journal could not record it: {exc}"
        ) from exc
    journal.record(
        LANDED_EVENT,
        {"commit": landed_commit, "base_commit": base, "patch_sha256": digest},
    )

    landed_at = datetime.now(UTC).isoformat()
    rows = _finding_rows(record, repo, landed_commit, landed_at)
    register = register_path(_state_dir(run_dir, record), repo.name)
    append_rows(register, rows)
    return Landing(
        run_id=run_id,
        repo=repo,
        commit=landed_commit,
        register=register,
        findings=len(rows),
    )


def _repository(run_dir: Path, record: dict) -> Path:
    """The repository the run measured, as the record locates it from itself."""
    located = record.get("repo")
    if not isinstance(located, str):
        raise LandingError("the record names no repository")
    repo = (run_dir / located).resolve()
    if not repo.is_dir():
        raise LandingError(f"the repository the run measured is not here: {repo}")
    try:
        if root(repo) != repo:
            raise LandingError(f"{repo} is not the root of a repository")
    except GitError as exc:
        raise LandingError(f"{repo} is not a repository: {exc}") from exc
    return repo


def _state_dir(run_dir: Path, record: dict) -> Path:
    located = record.get("state_dir")
    if not isinstance(located, str):
        raise LandingError("the record names no state directory")
    return (run_dir / located).resolve()


def _finding_rows(
    record: dict, repo: Path, landed_commit: str, landed_at: str
) -> list[dict[str, object]]:
    """One row per finding the review reported on the candidate that landed."""
    iterations = record.get("iterations")
    if not isinstance(iterations, list) or not iterations:
        return []
    review = iterations[-1].get("review") if isinstance(iterations[-1], dict) else None
    result = review.get("result") if isinstance(review, dict) else None
    findings = result.get("findings") if isinstance(result, dict) else None
    if not isinstance(findings, list):
        return []
    return [
        {
            "landed_at": landed_at,
            "repo": repo.name,
            "run_id": record.get("run_id"),
            "commit": landed_commit,
            "severity": finding.get("severity"),
            "path": finding.get("path"),
            "line": finding.get("line"),
            "message": finding.get("message"),
            "evidence": finding.get("evidence"),
            "covered_by": NOT_COVERED,
        }
        for finding in findings
        if isinstance(finding, dict)
    ]
