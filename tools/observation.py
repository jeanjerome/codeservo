"""The document this repository's gates answer with, beside their exit code.

A gate exits zero or non-zero, and that stays the verdict. A gate declaring
`codeservo-json` answers a second time, with a document saying what it
measured. Without it a run records that a gate passed and nothing about what
it found: the number the tool computed lives in a log nobody compares, and two
runs cannot be set beside each other.

This module owns one half of that, the writing. The controller owns the
contract and validates what arrives; what is here only has to produce it, and
to produce nothing at all when no location was given — a gate run by hand has
no document to write and invents none.

The location comes from the controller, and how it arrives is the controller's
business: a gate naming a shell command reads `CODESERVO_OBSERVATION_PATH`, a
gate naming a provider task is handed the location as its one argument,
because its task starts with an environment no variable survives into.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# The shape the controller validates against. Stated as the number this
# document says it is, so a document written against another version says so.
SCHEMA_VERSION = 1

# The severities the observation schema declares. A finding describes; the
# exit code decides, so nothing here changes a verdict.
BLOCKER = "blocker"
MAJOR = "major"
MINOR = "minor"
INFO = "info"

# Where a gate naming a shell command is told to write. A task gate is handed
# the same location as an argument instead.
PATH_VARIABLE = "CODESERVO_OBSERVATION_PATH"


def location(argv: list[str]) -> str | None:
    """Where to write, from the argument or from the environment, or nowhere.

    Both channels are read so one script serves a gate of either kind, and a
    gate run by hand answers with neither and writes nothing.
    """
    return argv[0] if argv else os.environ.get(PATH_VARIABLE) or None


def finding(
    *,
    id: str,  # noqa: A002 — the schema's field name
    severity: str,
    message: str,
    path: str | None = None,
    line: int | None = None,
) -> dict[str, Any]:
    """One thing a gate saw, and where it saw it.

    A finding names a place when the tool named one. A measurement over a
    whole tree, or one no line can be pointed at, says so rather than
    inventing a location the tool never reported.
    """
    return {
        "id": id,
        "severity": severity,
        "path": path,
        "line": line,
        "message": message,
    }


def write(
    where: str | None,
    *,
    sensor: str,
    passed: bool,
    summary: str,
    findings: Iterable[dict[str, Any]] = (),
    metrics: dict[str, float] | None = None,
) -> None:
    """Write one gate's document, where the controller asked for one.

    `status` follows the exit code the gate is about to return rather than
    being stated twice: a document claiming to have passed beside a non-zero
    exit is a contradiction the controller refuses, and it should never be
    this side that produces one.
    """
    if where is None:
        return
    document = Path(where)
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "sensor": sensor,
                "status": "passed" if passed else "failed",
                "summary": summary,
                "findings": list(findings),
                "metrics": metrics or {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
