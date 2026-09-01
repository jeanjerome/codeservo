"""The run journal: one immutable event per transition, chained by digests.

`evidence.json` is a document a run rewrites as it goes. The journal is the
trajectory that produced it: every transition is appended once, in the order it
happened, and each line closes over the one before it, so a line that was
reordered, removed or altered no longer chains. The record and the journal
anchor each other, because the decision a run reached is itself an event.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence import sha256_file, sha256_json

# The shape of one journal line. The journal versions its own shape.
EVENT_SCHEMA_VERSION = 1

# Where a run keeps its journal, relative to the run directory.
JOURNAL_NAME = "events.jsonl"

# Everything one line carries, and nothing else.
EVENT_FIELDS = (
    "schema_version",
    "run_id",
    "sequence",
    "recorded_at",
    "type",
    "payload",
    "previous_sha256",
    "sha256",
)


class JournalError(RuntimeError):
    pass


def event_sha256(event: dict[str, Any]) -> str:
    """The digest closing one event: the event without its own digest."""
    return sha256_json({key: value for key, value in event.items() if key != "sha256"})


class Journal:
    """The append-only journal of one run.

    Nothing is buffered: an event is on the file system before the caller can
    act on the transition it records, so a gate, a reviewer or a reader that
    looks while the run is going finds every transition that already happened.
    """

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._sequence = 0
        self._head: str | None = None

    @property
    def count(self) -> int:
        return self._sequence

    @property
    def head_sha256(self) -> str | None:
        return self._head

    def record(self, event_type: str, payload: dict[str, Any] | None = None) -> dict:
        event = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "run_id": self.run_id,
            "sequence": self._sequence + 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "payload": dict(payload or {}),
            "previous_sha256": self._head,
        }
        event["sha256"] = event_sha256(event)
        self._append(event)
        self._sequence = event["sequence"]
        self._head = event["sha256"]
        return event

    def summary(self) -> dict:
        """The `events` block of the record: the journal as it stands now."""
        return {
            "path": JOURNAL_NAME,
            "count": self.count,
            "head_sha256": self._head,
            "file_sha256": sha256_file(self.path) if self.path.is_file() else None,
        }

    def _append(self, event: dict[str, Any]) -> None:
        line = json.dumps(
            event, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def read_journal(path: Path) -> list[dict]:
    """Every event of a journal, in the order the file holds them."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise JournalError(f"{JOURNAL_NAME}: the journal is not readable") from exc

    events: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JournalError(
                f"{JOURNAL_NAME}: line {number} is not a readable event"
            ) from exc
        if not isinstance(event, dict):
            raise JournalError(f"{JOURNAL_NAME}: line {number} is not an event")
        events.append(event)
    return events


def chain_failures(
    events: list[dict], run_id: str | None = None
) -> list[tuple[str, str]]:
    """What a journal disagrees with, as (aspect, statement) pairs.

    The aspects are separate readings of the same lines: the shape of an
    event, its place in the sequence, the link to the event before it, and the
    digest closing it. A line that was reordered or removed breaks the
    sequence and the chain; a line that was altered breaks its own digest and
    the link the next line makes to it.
    """
    failures: list[tuple[str, str]] = []
    previous: str | None = None
    for index, event in enumerate(events):
        position = index + 1
        label = f"{JOURNAL_NAME}: event {position}"
        if set(event) != set(EVENT_FIELDS):
            failures.append(("shape", f"{label} does not carry the event fields"))
            continue
        if event["schema_version"] != EVENT_SCHEMA_VERSION:
            failures.append(
                ("shape", f"{label} declares schema {event['schema_version']}")
            )
        if run_id is not None and event["run_id"] != run_id:
            failures.append(("shape", f"{label} belongs to run {event['run_id']}"))
        if event["sequence"] != position:
            failures.append(
                ("sequence", f"{label} records sequence {event['sequence']}")
            )
        if event["previous_sha256"] != previous:
            failures.append(
                ("chain", f"{label} does not chain to the event before it")
            )
        if event["sha256"] != event_sha256(event):
            failures.append(("digests", f"{label} does not match its own digest"))
        previous = event["sha256"]
    return failures
