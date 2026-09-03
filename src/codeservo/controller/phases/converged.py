"""The candidate the quick phase accepted, handed on to the slower measurements."""

from __future__ import annotations

from dataclasses import dataclass

from ...sensors.gates import GateResult
from ..document import FileRecord


@dataclass(frozen=True)
class Converged:
    """The candidate the quick phase accepted, and what it looked like then."""

    quick_gates: tuple[GateResult, ...]
    state: FileRecord
