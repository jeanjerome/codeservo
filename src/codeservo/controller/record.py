"""The document a run writes, and the journal that closes over it.

The record is written to the file system at every transition, so a decision
never exists only in memory. The journal is closed by the same object that
states the decision, so a status edited afterwards no longer matches the
chain.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..evidence.digests import relative_evidence_paths, sha256_text, write_json
from ..evidence.journal import Journal
from ..workspace.git import make_patch
from .document import Evidence

# The shape of evidence.json. The observation bundle versions its own shape.
EVIDENCE_SCHEMA_VERSION = 16

# The three states a record reports. A run is `RUNNING` from the moment the
# directory exists until the decision closes the journal.
RunStatus = Literal["RUNNING", "ACCEPTED", "REJECTED"]
RUNNING: RunStatus = "RUNNING"
ACCEPTED: RunStatus = "ACCEPTED"
REJECTED: RunStatus = "REJECTED"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RunRecord:
    """The evidence document of one run, and the journal beside it.

    A phase reaches the document by name, so a field the record does not
    declare is refused where it is written. Every write reaches the file
    system through `persist`, and `close` is the only way a run reaches a
    status.
    """

    def __init__(
        self, *, run_dir: Path, journal: Journal, document: Evidence
    ) -> None:
        self.run_dir = run_dir
        self.journal = journal
        self.document = document
        self.path = run_dir / "evidence.json"

    def record(self, event_type: str, payload: dict) -> None:
        """Append one transition to the journal."""
        self.journal.record(event_type, payload)

    def persist(self) -> None:
        """Write the document as it stands.

        The events block describes the journal at the moment of writing, so a
        finished record describes the complete journal.
        """
        self.document["events"] = self.journal.summary()
        write_json(self.path, relative_evidence_paths(self.document, self.run_dir))

    def close(
        self,
        status: str,
        reasons: list[str],
        *,
        worktree: Path,
        base_commit: str,
    ) -> Evidence:
        """State the decision, after the journal has closed on it."""
        patch = ""
        if worktree.exists():
            patch = make_patch(worktree, base_commit)
            (self.run_dir / "change.patch").write_text(patch, encoding="utf-8")
        self.document["status"] = status
        self.document["finished_at"] = utc_now()
        self.document["decision"] = {"reasons": reasons}
        self.document["patch_sha256"] = sha256_text(patch) if patch else None
        self.document["run_dir"] = str(self.run_dir)
        self.document["worktree"] = str(worktree) if worktree.exists() else None
        # The decision closes the chain before the record states it, so a
        # status edited afterwards no longer matches the journal.
        self.journal.record(
            "decision.recorded", {"status": status, "reasons": list(reasons)}
        )
        self.journal.record("run.finished", {"status": status})
        self.persist()
        return self.document
