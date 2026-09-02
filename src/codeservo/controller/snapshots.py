"""What the candidate looked like at each boundary of a run.

A snapshot is the patch the candidate carries at one moment, written into the
record and digested. Comparing two of them says whether the phase between
them moved the tree it was only supposed to measure.
"""

from __future__ import annotations

from pathlib import Path

from ..evidence.digests import sha256_text
from ..workspace.git import make_patch
from .document import FileRecord


def write_patch_snapshot(
    path: Path, worktree: Path, base_commit: str
) -> FileRecord:
    patch = make_patch(worktree, base_commit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(patch, encoding="utf-8")
    return {
        "path": str(path),
        "sha256": sha256_text(patch),
    }


def mutated(phase: str, before: FileRecord, after: FileRecord) -> list[str]:
    """Whether a measurement phase changed the tree it was measuring.

    A confinement refuses only the writes its profile names; the comparison
    catches everything else. A phase that moved the candidate is a control
    failure of the run and not a failing gate: whatever those gates returned,
    they no longer describe the tree that was actuated.
    """
    if before["sha256"] == after["sha256"]:
        return []
    return [f"{phase} gates changed the candidate workspace"]
