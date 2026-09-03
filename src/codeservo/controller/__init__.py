"""The deterministic controller: what decides, as opposed to what proposes.

A run freezes its control inputs, measures the repository through sensors it
owns, feeds failures back to the actuator, and applies the acceptance rules
mechanically. The model proposes; nothing here asks it whether it succeeded.
"""

from .context import RunContext, RunRequest, declared_environment
from .errors import ControlFailure, Escalation, Rejection
from .landing import Landing, LandingError, land
from .record import EVIDENCE_SCHEMA_VERSION, RunRecord
from .run import run

__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "ControlFailure",
    "Escalation",
    "Landing",
    "LandingError",
    "Rejection",
    "RunContext",
    "RunRecord",
    "RunRequest",
    "declared_environment",
    "land",
    "run",
]
