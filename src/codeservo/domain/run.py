"""What one run ends as.

A run reports its state in the record it writes, and the verification of a
run directory reads that same state back. Both are written against this
vocabulary, so a state is named in one place and no reader carries its own
copy of the string.
"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """The three states a record reports.

    A run is `RUNNING` from the moment its directory exists until the
    decision closes the journal.
    """

    RUNNING = "RUNNING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
