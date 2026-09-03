"""The document a run writes, and the journal that closes over it.

The record is written to the file system at every transition, so a decision
never exists only in memory. The journal is closed by the same object that
states the decision, so a status edited afterwards no longer matches the
chain.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from ..domain.document import Unset, rendered
from ..domain.run import RunStatus
from ..evidence.digests import relative_evidence_paths, sha256_text, write_json
from ..evidence.journal import Journal
from ..sensors.gates import GateResult
from ..workspace.git import make_patch
from .document import Decision, Evidence, Iteration
from .errors import ControlFailure

# The shape of evidence.json. The observation bundle versions its own shape.
EVIDENCE_SCHEMA_VERSION = 19


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RunRecord:
    """The evidence document of one run, and the journal beside it.

    The document is frozen, so a phase does not edit what the run already
    stated: it states the record it reached, and the previous one is what the
    file system already holds. Every write reaches the file system through
    `persist`, and `close` is the only way a run reaches a status.

    One iteration is the exception a loop needs. It is assembled over several
    transitions and has to be kept however it ends, including on the rejection
    that ended it, so the attempt in progress is held here and each stage
    states the version it reached.
    """

    def __init__(
        self, *, run_dir: Path, journal: Journal, document: Evidence
    ) -> None:
        self.run_dir = run_dir
        self.journal = journal
        self.document = document
        self.attempt: Iteration | None = None
        self.path = run_dir / "evidence.json"

    def record(self, event_type: str, payload: dict) -> None:
        """Append one transition to the journal."""
        self.journal.record(event_type, payload)

    def attempted(self) -> Iteration:
        """The iteration in progress, as the last stage left it."""
        if self.attempt is None:
            raise ControlFailure("no iteration is in progress")
        return self.attempt

    def baseline(self) -> tuple[GateResult, ...]:
        """The baseline measurement, which every phase after it reads.

        A ratchet compares a candidate's document with the baseline's, so a
        phase asking for one before the baseline was measured is a control
        failure of the loop rather than a fact about the run.
        """
        if isinstance(self.document.baseline, Unset):
            raise ControlFailure("the run has measured no baseline yet")
        return self.document.baseline

    def keep(self) -> None:
        """Keep the iteration in progress, however far it got."""
        self.document = replace(
            self.document, iterations=(*self.document.iterations, self.attempted())
        )
        self.attempt = None

    def persist(self) -> None:
        """Write the document as it stands.

        The events block describes the journal at the moment of writing, so a
        finished record describes the complete journal. What the run holds is
        rendered on the way out: a block the run built as a document becomes
        the JSON object it declares, and a field it never measured is left out
        rather than written as null.
        """
        self.document = replace(self.document, events=self.journal.summary())
        write_json(self.path, relative_evidence_paths(self.written(), self.run_dir))

    def written(self) -> dict:
        """The record as a reader of the run directory sees it.

        The locations are the ones this machine used; only writing makes them
        relative to the run directory. An iteration in progress is written as
        far as it got, so what a stage stated reaches the file system before
        the next stage acts on it, whether or not the iteration is kept later.
        """
        document = self.document
        if self.attempt is not None:
            document = replace(
                document, iterations=(*document.iterations, self.attempt)
            )
        return rendered(document)

    def close(
        self,
        status: RunStatus,
        reasons: list[str],
        *,
        worktree: Path,
        base_commit: str,
    ) -> dict:
        """State the decision, after the journal has closed on it.

        What comes back is the record as it was written, so a caller reads the
        document a run leaves behind rather than the objects that built it.
        """
        patch = ""
        if worktree.exists():
            patch = make_patch(worktree, base_commit)
            (self.run_dir / "change.patch").write_text(patch, encoding="utf-8")
        self.document = replace(
            self.document,
            status=status,
            finished_at=utc_now(),
            decision=Decision(reasons=tuple(reasons)),
            patch_sha256=sha256_text(patch) if patch else None,
            run_dir=str(self.run_dir),
            worktree=str(worktree) if worktree.exists() else None,
        )
        # The decision closes the chain before the record states it, so a
        # status edited afterwards no longer matches the journal.
        self.journal.record(
            "decision.recorded", {"status": status, "reasons": list(reasons)}
        )
        self.journal.record("run.finished", {"status": status})
        self.persist()
        return self.written()
