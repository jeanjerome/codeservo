"""How this host runs a command, and how it confines one.

Every process a run starts — a gate, an actuator, the reviewer, a provider
command — goes through here, so the confinement is a property of the
controller and not a request made to the process.
"""

from .process import run_command, tail
from .sandbox import (
    Isolation,
    SandboxError,
    isolation_evidence,
    seatbelt_command,
    seatbelt_profile,
)

__all__ = [
    "Isolation",
    "SandboxError",
    "isolation_evidence",
    "run_command",
    "seatbelt_command",
    "seatbelt_profile",
    "tail",
]
