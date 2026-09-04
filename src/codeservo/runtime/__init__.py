"""How this host runs a command, and how it confines one.

Every process a run starts — a gate, an actuator, the reviewer, a provider
command — goes through here, so the confinement is a property of the
controller and not a request made to the process.
"""

from .confinement import ConfinedCommand, Confiner, confined, host_confiner, mechanism
from .process import run_command, tail
from .sandbox import (
    Isolation,
    IsolationEvidence,
    Mechanism,
    SandboxError,
    isolation_evidence,
)

__all__ = [
    "ConfinedCommand",
    "Confiner",
    "Isolation",
    "IsolationEvidence",
    "Mechanism",
    "SandboxError",
    "confined",
    "host_confiner",
    "isolation_evidence",
    "mechanism",
    "run_command",
    "tail",
]
