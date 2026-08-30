from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CRITERION_RE = re.compile(r"^\s*-\s*\[([A-Z][A-Z0-9_-]*)\]\s+(.+?)\s*$")


@dataclass(frozen=True)
class Task:
    path: Path
    raw_text: str
    criteria: dict[str, str]


class TaskError(ValueError):
    pass


def load_task(path: Path) -> Task:
    if not path.is_file():
        raise TaskError(f"task file does not exist: {path}")
    raw = path.read_text(encoding="utf-8")
    criteria: dict[str, str] = {}
    for line in raw.splitlines():
        match = CRITERION_RE.match(line)
        if not match:
            continue
        criterion_id, text = match.groups()
        if criterion_id in criteria:
            raise TaskError(f"duplicate acceptance criterion: {criterion_id}")
        criteria[criterion_id] = text
    if not criteria:
        raise TaskError("task must contain at least one '- [AC1] ...' acceptance criterion")
    return Task(path=path, raw_text=raw, criteria=criteria)
