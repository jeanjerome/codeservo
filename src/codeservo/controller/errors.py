"""How a run ends before it reaches its decision."""

from __future__ import annotations

from collections.abc import Sequence


class ControlFailure(RuntimeError):
    """A control input the run cannot proceed under."""


class Rejection(Exception):
    """The run ends here, for these reasons.

    Every step that can end a run raises this instead of returning a status,
    so the record is closed in exactly one place and no step can reach its
    own end without closing it.
    """

    def __init__(self, reasons: str | Sequence[str]) -> None:
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(self.reasons))
