"""The deterministic controller: what decides, as opposed to what proposes.

A run freezes its control inputs, measures the repository through sensors it
owns, feeds failures back to the actuator, and applies the acceptance rules
mechanically. The model proposes; nothing here asks it whether it succeeded.
"""

from .context import NO_ENVIRONMENT, RunContext, RunRequest
from .errors import ControlFailure, Rejection
from .record import EVIDENCE_SCHEMA_VERSION, RunRecord
from .run import run

__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "NO_ENVIRONMENT",
    "ControlFailure",
    "Rejection",
    "RunContext",
    "RunRecord",
    "RunRequest",
    "run",
]
