"""The findings register: what landed with a finding no gate had caught.

An accepted run's review may carry findings below the blocking line. They enter
the repository with the change, and until now they lived in prose. Here they
are one tabulated line each, in the state directory, so the same kind of
finding seen twice across runs is countable, and a person can write beside it
which deterministic control covers it now.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

COLUMNS = (
    "landed_at",
    "repo",
    "run_id",
    "commit",
    "severity",
    "path",
    "line",
    "message",
    "evidence",
    "covered_by",
)

# What the controller writes in the last column. A gate that takes a finding
# over is named there afterwards, by the person who wrote the gate.
NOT_COVERED = "none"


def register_path(state_dir: Path, repo_name: str) -> Path:
    """Where one repository's register lives, beside its runs."""
    return state_dir / "findings" / f"{repo_name}.tsv"


def cell(value: object) -> str:
    """One value as a cell: nothing in it may end the cell or the line."""
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def append_rows(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    """Append one line per row, writing the header first when the file is new.

    A landing with nothing to register leaves no file behind: a register that
    exists says a finding landed, and an empty one would say so of nothing.
    """
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if not path.exists() or path.stat().st_size == 0:
        lines.append("\t".join(COLUMNS))
    lines.extend(
        "\t".join(cell(row.get(column)) for column in COLUMNS) for row in rows
    )
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
