"""The report files a gate's own tool wrote, and which of them are this run's.

A gate may answer with the reports its tool already writes rather than with a
document written for the controller. The constitution names them as a glob
relative to the tree the gate measures; this module finds them and decides
which ones the measurement produced, and a module per format reads them.

What is read is what this measurement wrote. The files matching the pattern
are listed before the gate runs and again after it, and a report the gate left
exactly as it found it — same size, same modification time — is not this
measurement's: a module a build skipped keeps its old report, and the run must
not count it. Nothing is deleted to make that so. The tree is read and never
written, in the source repository as in the checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

from .observations import Observation

# A file larger than this is not a report a tool wrote, and its size is read
# before any of its bytes are.
REPORT_SIZE_LIMIT = 64 * 1024 * 1024

# A file as listed before and after a measurement: its size and the
# nanosecond it was last written.
Listing = dict[str, tuple[int, int]]


class ReportFault(ValueError):
    """What a file matching the pattern is not: a report this reader knows."""


def list_reports(tree: Path, pattern: str) -> Listing:
    """Every file the pattern matches under the tree, with size and write time.

    A file the pattern reaches through a link leaving the tree is not under
    it and is not listed.
    """
    root = tree.resolve()
    listing: Listing = {}
    for path in sorted(tree.glob(pattern)):
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        stat = path.stat()
        listing[path.relative_to(tree).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return listing


def written_reports(
    tree: Path, pattern: str, before: Listing
) -> tuple[list[str], list[str]]:
    """The reports this measurement wrote, and those it left as it found them.

    A file is this measurement's when it did not exist before the gate ran or
    when its size or write time moved while the gate ran. One the gate never
    touched is listed second, so a caller can say it was there and not read.
    """
    after = list_reports(tree, pattern)
    written = [path for path, entry in after.items() if before.get(path) != entry]
    left = [path for path, entry in after.items() if before.get(path) == entry]
    return written, left


def read_report(tree: Path, relative: str, *, limit: int = REPORT_SIZE_LIMIT) -> bytes:
    """The bytes of one report, refused by size before any of them is read.

    A report the file system will not hand over is a fault of the measurement
    rather than an interpreter traceback: the listing found it a moment ago,
    and what changed since is not something a candidate is told about.
    """
    path = tree / relative
    try:
        size = path.stat().st_size
        if size > limit:
            raise ReportFault(f"{relative} is larger than {limit} bytes")
        return path.read_bytes()
    except OSError as exc:
        raise ReportFault(f"{relative} could not be read: {exc}") from None


def one_line(text: str | None) -> str:
    """The first non-empty line of a text, its whitespace collapsed.

    A tool says what it found in as many lines as it likes, and a finding
    carries one; the rest of what it printed reaches the actuator as the tail
    of the gate's own output.
    """
    for line in (text or "").splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed
    return ""


def unique(identifier: str, taken: set[str]) -> str:
    """The identifier, or the first numbered variant nobody holds yet.

    A rule that fires twice in one file, and a scenario a feature declares
    twice, each yield two results of one name; a finding is one thing seen
    once.
    """
    candidate = identifier
    ordinal = 2
    while candidate in taken:
        candidate = f"{identifier}#{ordinal}"
        ordinal += 1
    taken.add(candidate)
    return candidate


def render(observation: Observation) -> bytes:
    """One projected document as the record keeps it: canonical JSON text.

    A gate writing its own document is kept byte for byte as it wrote it. A
    projection has no bytes of its own until here, so it gets one spelling,
    the same for every format.
    """
    return (
        json.dumps(observation.to_document(), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
