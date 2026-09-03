"""How a run ends before it reaches its decision."""

from __future__ import annotations

from collections.abc import Sequence


class ControlFailure(RuntimeError):
    """A control input the run cannot proceed under."""


class Ending(Exception):
    """The run ends here, for these reasons.

    Every step that can end a run raises one of these instead of returning a
    status, so the record is closed in exactly one place and no step can reach
    its own end without closing it.
    """

    def __init__(self, reasons: str | Sequence[str]) -> None:
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(self.reasons))


class Rejection(Ending):
    """A deterministic control refused the candidate, or could not measure it."""


class Escalation(Ending):
    """Every deterministic control let the candidate through, and nobody decided.

    The reasons name what only a person can settle: a criterion no gate and
    no reviewer could verify, a review contradicting a gate that passed, or a
    budget spent on review objections alone.
    """
