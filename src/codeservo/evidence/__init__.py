"""What a run leaves behind, and what can be decided from it alone.

The journal records each transition before it becomes visible, the digests
bind the record to the artefacts it names, and the verification reads a run
directory without trusting anything outside it.
"""

from .digests import (
    relative_evidence_paths,
    sha256_file,
    sha256_json,
    sha256_path,
    sha256_record,
    sha256_text,
    write_json,
)
from .journal import JOURNAL_NAME, Journal, JournalError, read_journal

__all__ = [
    "JOURNAL_NAME",
    "Journal",
    "JournalError",
    "read_journal",
    "relative_evidence_paths",
    "sha256_file",
    "sha256_json",
    "sha256_path",
    "sha256_record",
    "sha256_text",
    "write_json",
]
